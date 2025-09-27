import os
import html
import json
from pathlib import Path
from typing import Dict, Any, List

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, filters
)

TOKEN = os.environ["TOKEN"]

# File lưu dữ liệu
DATA_FILE = Path("members.json")
TELETHON_FILE = Path("telethon_members.json")
db: Dict[str, Dict[str, Dict[str, Any]]] = {}

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

    load_db()  # load dữ liệu cũ

    if cid not in db:
        db[cid] = {}

    db[cid][uid] = {
        "username": username,
        "name": name
    }

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
    count_new, count_updated = 0, 0

    for chat_id, members in telethon_data.items():
        if chat_id not in db:
            db[chat_id] = {}
        for uid, info in members.items():
            if uid not in db[chat_id]:
                count_new += 1
            else:
                count_updated += 1
            db[chat_id][uid] = info

    save_db()
    TELETHON_FILE.rename("telethon_members.imported.json")
    print(f"✅ Import từ Telethon xong: {count_new} mới, {count_updated} cập nhật.")

# ------------------ Bot Helpers ------------------
def format_mention(user_id: int, username: str | None, name: str) -> str:
    if username:
        return f"@{username}"
    safe = html.escape(name or "user")
    return f'<a href="tg://user?id={user_id}">{safe}</a>'

async def is_admin(chat_id: int, user_id: int, context: ContextTypes.DEFAULT_TYPE) -> bool:
    try:
        admins = await context.bot.get_chat_administrators(chat_id)
        return any(a.user.id == user_id for a in admins)
    except Exception:
        return False

# ------------------ Commands ------------------
async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("pong ✅")

async def cmd_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    await update.effective_message.reply_text(
        f"📌 Chat ID của nhóm này là: <code>{chat.id}</code>",
        parse_mode=ParseMode.HTML
    )

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 Hướng dẫn:\n"
        "/ping - kiểm tra bot\n"
        "/help - xem danh sách lệnh\n"
        "/all - tag mọi người mà bot đã ghi nhận\n"
        "/sync - đồng bộ admins (chỉ admin)\n"
    )
    await update.effective_message.reply_text(text)

async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat

    load_db()
    cid = str(chat.id)
    users_map = db.get(cid, {})
    if not users_map:
        await message.reply_text("Danh sách trống. Hãy dùng /sync hoặc để mọi người nhắn vài câu rồi thử lại.")
        return

    mentions: List[str] = []
    for uid, info in users_map.items():
        mentions.append(format_mention(int(uid), info.get("username"), info.get("name")))

    chunk_size = 50
    chunks = [mentions[i:i+chunk_size] for i in range(0, len(mentions), chunk_size)]

    for idx, c in enumerate(chunks, start=1):
        txt = "🔔 Tag All (phần {}/{}):\n".format(idx, len(chunks)) + " ".join(c)
        await context.bot.send_message(
            chat_id=chat.id,
            text=txt,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    user = update.effective_user
    if not await is_admin(chat.id, user.id, context):
        await update.effective_message.reply_text("⛔ Chỉ admin mới dùng /sync.")
        return

    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        for a in admins:
            u = a.user
            upsert_member(chat.id, u.id, u.username, u.full_name)
        await update.effective_message.reply_text(f"Đã đồng bộ {len(admins)} admin ✅")
    except Exception as e:
        await update.effective_message.reply_text(f"Lỗi khi sync admins: {e}")

# ------------------ Track events ------------------
async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    u = update.effective_user
    if not chat or not u:
        return
    upsert_member(chat.id, u.id, u.username, u.full_name)

async def track_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    for u in msg.new_chat_members:
        upsert_member(chat.id, u.id, u.username, u.full_name)

# ------------------ Startup Logger ------------------
async def log_chats(app: Application):
    chats = await app.bot.get_updates(limit=1)  # trick để chắc chắn bot sync
    print("✅ Bot đã khởi động.")
    print("👉 Khi bot nhận tin nhắn trong group, bạn sẽ thấy log Chat ID ở dưới.")

# ------------------ Main ------------------
def main():
    load_db()
    import_from_telethon()

    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("id", cmd_id))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("all", cmd_all))
    app.add_handler(CommandHandler("sync", cmd_sync))

    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, track_new_members))
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, track_message))

    # Khi có message bất kỳ, log ra chat_id
    async def log_chat_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
        chat = update.effective_chat
        if chat:
            print(f"📌 Bot đang hoạt động trong group: {chat.title} | Chat ID: {chat.id}")

    app.add_handler(MessageHandler(filters.ALL, log_chat_id))

    app.run_polling()

if __name__ == "__main__":
    main()
