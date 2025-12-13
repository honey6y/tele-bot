import os
import html
import json
from dotenv import load_dotenv
from pathlib import Path
from typing import Dict, Any, List
import datetime

# import pytz  # timezone VN
from fastapi import FastAPI, Request
import uvicorn

from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)
from telegram import Update
from telegram.constants import ParseMode
from threading import Thread

load_dotenv()
TOKEN = os.getenv("TOKEN")
WEBHOOK_PATH = "/webhook"
PORT = int(os.environ.get("PORT", 10000))
RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")


if not TOKEN:
    raise ValueError(
        "❌ Không tìm thấy TOKEN! Hãy tạo file .env với dòng: TOKEN=your_token_here"
    )


# File lưu dữ liệu
DATA_FILE = Path("members.json")
TELETHON_FILE = Path("telethon_members.json")
db: Dict[str, Dict[str, Dict[str, Any]]] = {}

# ------------------ Config ------------------
TARGET_CHAT_ID = -1002727375183  # ID group thật
TOPIC_TUESDAY_ID = 9
TOPIC_SUNDAY_ID = 4

app = Application.builder().token(TOKEN).build()


# ------------------ DB Helpers ------------------
def load_db():
    global db
    if DATA_FILE.exists():
        try:
            db = json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except Exception:
            db = {}
    else:
        db = {}


def save_db():
    tmp_file = DATA_FILE.with_suffix(".tmp")
    tmp_file.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_file.replace(DATA_FILE)


def upsert_member(chat_id: int, user_id: int, username: str | None, name: str):
    cid = str(chat_id)
    uid = str(user_id)
    load_db()
    if cid not in db:
        db[cid] = {}
    db[cid][uid] = {"username": username, "name": name}
    save_db()


def import_from_telethon():
    if not TELETHON_FILE.exists():
        return
    try:
        telethon_data = json.loads(TELETHON_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"❌ Không đọc được telethon_members.json: {e}")
        return
    load_db()
    for chat_id, members in telethon_data.items():
        if chat_id not in db:
            db[chat_id] = {}
        for uid, info in members.items():
            db[chat_id][uid] = info
    save_db()
    TELETHON_FILE.rename("telethon_members.imported.json")
    print("✅ Import từ Telethon xong.")


# ------------------ Helpers ------------------
def format_mention(user_id: int, username: str | None, name: str) -> str:
    if username:
        return f"@{username}"
    safe = html.escape(name or "user")
    return f'<a href="tg://user?id={user_id}">{safe}</a>'


async def is_admin(
    chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE
) -> bool:
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        return any(a.user.id == user_id for a in admins)
    except Exception:
        return False


def next_weekday(target_weekday: int) -> datetime.date:
    today = datetime.date.today()
    days_ahead = (target_weekday - today.weekday() + 7) % 7
    return today + datetime.timedelta(days=days_ahead)


# ------------------ Poll Helper ------------------
async def create_poll(
    chat_id: int,
    title: str,
    options: List[str],
    context: ContextTypes.DEFAULT_TYPE,
    tag_all: bool = True,
    is_anonymous: bool = False,
    thread_id: int | None = None,
):
    chat = await context.bot.get_chat(chat_id)
    is_forum = getattr(chat, "is_forum", False)

    # Tag all nếu cần
    if tag_all:
        load_db()
        users_map = db.get(str(chat_id), {})
        mentions = [
            format_mention(int(uid), info.get("username"), info.get("name"))
            for uid, info in users_map.items()
        ]
        if mentions:
            txt = "🔔 Mọi người ơi, vote nè:\n" + " ".join(mentions)
            try:
                if is_forum and thread_id:
                    await context.bot.send_message(
                        chat_id=chat_id,
                        text=txt,
                        parse_mode=ParseMode.HTML,
                        message_thread_id=thread_id,
                    )
                else:
                    await context.bot.send_message(
                        chat_id=chat_id, text=txt, parse_mode=ParseMode.HTML
                    )
            except Exception as e:
                print(f"⚠️ Lỗi khi tag all: {e}")

    # Gửi poll
    try:
        if is_forum and thread_id:
            await context.bot.send_poll(
                chat_id=chat_id,
                question=title,
                options=options,
                is_anonymous=is_anonymous,
                message_thread_id=thread_id,
            )
        else:
            await context.bot.send_poll(
                chat_id=chat_id,
                question=title,
                options=options,
                is_anonymous=is_anonymous,
            )
    except Exception as e:
        print(f"⚠️ Lỗi khi gửi poll: {e}")


# ------------------ Commands ------------------
async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("pong ✅")


async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.effective_message.reply_text(
        f"📌 Chat ID: <code>{chat.id}</code>", parse_mode=ParseMode.HTML
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 Hướng dẫn:\n"
        "/ping - kiểm tra bot\n"
        "/help - danh sách lệnh\n"
        "/all - tag mọi người\n"
        "/sync - đồng bộ admins (chỉ admin)\n"
        "/poll - Cú pháp: \n/poll [anonymous]\n[title]\n[option]\n[option]\n..."
        "/poll_sunday - poll chủ nhật (cầu lông)\n"
        "/poll_tuesday - poll thứ 3 (cầu lông)\n"
        "/poll_thursday - poll thứ 5 (đá bóng)\n"
    )
    await update.effective_message.reply_text(text)


async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    thread_id = update.effective_message.message_thread_id
    is_forum = getattr(chat, "is_forum", False)

    load_db()
    users_map = db.get(str(chat.id), {})
    if not users_map:
        await update.effective_message.reply_text(
            "📭 Danh sách trống. Hãy để mọi người chat vài câu hoặc dùng /sync."
        )
        return

    mentions = [
        format_mention(int(uid), info.get("username"), info.get("name"))
        for uid, info in users_map.items()
    ]
    text_cmd = update.effective_message.text.lower()
    prefix = ""
    if "vote" in text_cmd:
        prefix = "A/E vote giúp em nhé:\n"

    chunk_size = 50
    for i in range(0, len(mentions), chunk_size):
        text = prefix + " ".join(mentions[i : i + chunk_size])
        try:
            if is_forum and thread_id:
                await context.bot.send_message(
                    chat.id,
                    text,
                    parse_mode=ParseMode.HTML,
                    message_thread_id=thread_id,
                )
            else:
                await context.bot.send_message(chat.id, text, parse_mode=ParseMode.HTML)
        except Exception as e:
            print(f"⚠️ Lỗi khi gửi /all: {e}")


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if not await is_admin(chat.id, user.id, context):
        await update.effective_message.reply_text("⛔ Chỉ admin mới dùng /sync.")
        return
    admins = await context.bot.get_chat_administrators(chat.id)
    for a in admins:
        u = a.user
        upsert_member(chat.id, u.id, u.username, u.full_name)
    await update.effective_message.reply_text(f"Đã đồng bộ {len(admins)} admin ✅")


async def cmd_poll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = update.effective_message.text.split("\n", 2)
    first_line = args[0].lower()
    is_anonymous = "anonymous" in first_line
    if len(args) < 2:
        await update.effective_message.reply_text(
            "📌 Cú pháp:\n/poll [anonymous]\n[title]\n[option]\n[option] ..."
        )
        return
    title, options = args[1], []
    for line in args[2].split("\n"):
        options.append(line.strip())
    if not title or len(options) < 2:
        await update.effective_message.reply_text(
            "⚠️ Phải có title và ít nhất 2 option."
        )
        return
    thread_id = update.effective_message.message_thread_id
    await create_poll(
        update.effective_chat.id,
        title,
        options,
        context,
        tag_all=True,
        is_anonymous=is_anonymous,
        thread_id=thread_id,
    )


async def cmd_poll_sunday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sunday = next_weekday(6)
    title = f"Chơi chủ nhật 17h30-19h30 ({sunday.strftime('%d/%m')})"
    options = ["Có", "Không", "+1", "+2", "+3"]
    thread_id = update.effective_message.message_thread_id
    await create_poll(
        update.effective_chat.id, title, options, context, thread_id=thread_id
    )


async def cmd_poll_tuesday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    tuesday = next_weekday(1)
    title = f"Chơi cố định thứ 3 17h30-19h30 ({tuesday.strftime('%d/%m')})"
    options = ["Có", "Không", "Xé vé lẻ"]
    thread_id = update.effective_message.message_thread_id
    await create_poll(
        update.effective_chat.id, title, options, context, thread_id=thread_id
    )


async def cmd_poll_thursday(update: Update, context: ContextTypes.DEFAULT_TYPE):
    thursday = next_weekday(3)
    title = f"Đá bóng thứ 5 19h00-20h30 ({thursday.strftime('%d/%m')})"
    options = ["Có", "Không", "+1"]
    thread_id = update.effective_message.message_thread_id
    await create_poll(
        update.effective_chat.id, title, options, context, thread_id=thread_id
    )


# ------------------ Track events ------------------
async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    u = update.effective_user
    if chat and u:
        upsert_member(chat.id, u.id, u.username, u.full_name)


async def track_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    for u in update.effective_message.new_chat_members:
        upsert_member(chat.id, u.id, u.username, u.full_name)


# ------------------ Main ------------------
def main():
    # load_db()
    # import_from_telethon()
    # app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("all", cmd_all))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CommandHandler("poll", cmd_poll))
    app.add_handler(CommandHandler("poll_sunday", cmd_poll_sunday))
    app.add_handler(CommandHandler("poll_tuesday", cmd_poll_tuesday))
    app.add_handler(CommandHandler("poll_thursday", cmd_poll_thursday))

    app.add_handler(
        MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, track_new_members)
    )
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, track_message))

    # ------------------ Jobs ------------------
    # vn_tz = pytz.timezone("Asia/Ho_Chi_Minh")

    # async def job_tuesday(context: ContextTypes.DEFAULT_TYPE):
    #     today = datetime.date.today()
    #     print("🔄 job_tuesday chạy:", today, "today.weekday", today.weekday())
    #     if today.weekday() != 0:
    #         return
    #     tuesday = next_weekday(1)
    #     title = f"Chơi cố định thứ 3 17h30-19h30 ({tuesday.strftime('%d/%m')})"
    #     options = ["Có", "Không", "Xé vé lẻ"]
    #     await create_poll(TARGET_CHAT_ID, title, options, context, thread_id=TOPIC_TUESDAY_ID)

    # async def job_sunday(context: ContextTypes.DEFAULT_TYPE):
    #     today = datetime.date.today()
    #     print("🔄 job_sunday chạy:", today, "today.weekday", today.weekday())
    #     if today.weekday() != 4:
    #         return
    #     sunday = next_weekday(6)
    #     title = f"Chơi chủ nhật 17h30-19h30 ({sunday.strftime('%d/%m')})"
    #     options = ["Có", "Không", "+1", "+2", "+3"]
    #     await create_poll(TARGET_CHAT_ID, title, options, context, thread_id=TOPIC_SUNDAY_ID)

    # async def job_monthly_poll(context: ContextTypes.DEFAULT_TYPE):
    #     today = datetime.date.today()
    #     print("🔄 job_monthly_poll chạy:", today, "today.weekday", today.day)
    #     if today.day != 25:
    #         return
    #     next_month = today.month + 1
    #     year = today.year
    #     if next_month > 12:
    #         next_month = 1
    #         year += 1
    #     title = f"Tham gia chơi cầu cố định thứ 3 hàng tuần, tháng {next_month}/{year}"
    #     options = ["Chơi", "Không chơi"]
    #     await create_poll(TARGET_CHAT_ID, title, options, context, thread_id=TOPIC_TUESDAY_ID)

    # app.job_queue.run_daily(job_tuesday, time=datetime.time(hour=9, minute=0, tzinfo=vn_tz), days=(0,), name="auto_poll_tuesday")
    # app.job_queue.run_daily(job_sunday, time=datetime.time(hour=9, minute=0, tzinfo=vn_tz), days=(4,), name="auto_poll_sunday")
    # app.job_queue.run_daily(job_monthly_poll, time=datetime.time(hour=9, minute=0, tzinfo=vn_tz), days=tuple(range(7)), name="auto_poll_monthly")

    # app.run_polling()

    # ------------------ Run Bot ------------------


# =========================================================
# FASTAPI WEBHOOK
# =========================================================
web_app = FastAPI()


@web_app.on_event("startup")
async def on_startup():
    load_db()
    import_from_telethon()

    await app.initialize()

    if not RENDER_EXTERNAL_URL:
        raise RuntimeError("❌ Thiếu RENDER_EXTERNAL_URL")

    webhook_url = RENDER_EXTERNAL_URL + WEBHOOK_PATH
    await app.bot.set_webhook(webhook_url)
    print(f"✅ Webhook set: {webhook_url}")


@web_app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, app.bot)
    await app.process_update(update)
    return {"ok": True}


if __name__ == "__main__":
    uvicorn.run(
        "main:web_app",
        host="0.0.0.0",
        port=PORT,
        reload=False,
    )
