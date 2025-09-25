import asyncio
import html
import json
import os
from pathlib import Path
from typing import Dict, Any, List

from telegram import Update, ChatPermissions
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CommandHandler, MessageHandler, ChatMemberHandler,
    ContextTypes, filters
)

TOKEN = os.environ.get("TOKEN") or "PUT_YOUR_TOKEN_HERE"

# Lưu theo từng chat_id: { chat_id: { user_id: {"username": str|None, "name": str} } }
DATA_FILE = Path("members.json")
db: Dict[str, Dict[str, Dict[str, Any]]] = {}

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
    try:
        DATA_FILE.write_text(json.dumps(db, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass

def upsert_member(chat_id: int, user_id: int, username: str | None, name: str):
    cid = str(chat_id)
    uid = str(user_id)
    if cid not in db:
        db[cid] = {}
    db[cid][uid] = {
        "username": username,
        "name": name
    }
    save_db()

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

async def cmd_ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text("pong ✅")

async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "🤖 Hướng dẫn:\n"
        "/ping - kiểm tra bot\n"
        "/sync - đồng bộ admins của group\n"
        "/all - tag mọi người mà bot đã ghi nhận (chỉ admin dùng)\n\n"
        "💡 Mẹo: để bot ghi nhớ nhiều người hơn, hãy tắt privacy mode (đã hướng dẫn) "
        "và để mọi người trò chuyện/hoặc mời họ vào lại."
    )
    await update.effective_message.reply_text(text)

async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    try:
        admins = await context.bot.get_chat_administrators(chat.id)
        for a in admins:
            u = a.user
            upsert_member(chat.id, u.id, u.username, u.full_name)
        await update.effective_message.reply_text(f"Đã đồng bộ {len(admins)} admin ✅")
    except Exception as e:
        await update.effective_message.reply_text(f"Lỗi khi sync admins: {e}")

async def cmd_all(update: Update, context: ContextTypes.DEFAULT_TYPE):
    message = update.effective_message
    chat = update.effective_chat
    user = update.effective_user

    # Chỉ admin được phép
    if not await is_admin(chat.id, user.id, context):
        await message.reply_text("⛔ Chỉ admin mới dùng /all.")
        return

    cid = str(chat.id)
    users_map = db.get(cid, {})
    if not users_map:
        await message.reply_text("Danh sách trống. Hãy dùng /sync hoặc để mọi người nhắn vài câu rồi thử lại.")
        return

    # Tạo danh sách mention
    mentions: List[str] = []
    for uid, info in users_map.items():
        mentions.append(format_mention(int(uid), info.get("username"), info.get("name")))

    # Tránh quá dài: chia 20 người/1 tin (tuỳ quy mô nhóm bạn có thể tăng/giảm)
    chunk_size = 20
    chunks = [mentions[i:i+chunk_size] for i in range(0, len(mentions), chunk_size)]

    for idx, c in enumerate(chunks, start=1):
        txt = "🔔 Tag All (phần {}/{}):\n".format(idx, len(chunks)) + " ".join(c)
        await context.bot.send_message(
            chat_id=chat.id,
            text=txt,
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True
        )

# Ghi nhớ người gửi tin (để lần sau /all có thể tag)
async def track_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    u = update.effective_user
    if not chat or not u:
        return
    upsert_member(chat.id, u.id, u.username, u.full_name)

# Ghi nhớ khi có thành viên mới vào nhóm
async def track_new_members(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat = update.effective_chat
    msg = update.effective_message
    for u in msg.new_chat_members:
        upsert_member(chat.id, u.id, u.username, u.full_name)

def main():
    load_db()
    app = Application.builder().token(TOKEN).build()

    app.add_handler(CommandHandler("ping", cmd_ping))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CommandHandler("all", cmd_all))

    # Thành viên mới
    app.add_handler(MessageHandler(filters.StatusUpdate.NEW_CHAT_MEMBERS, track_new_members))
    # Theo dõi mọi tin nhắn (cần privacy mode OFF để nhận tin thường)
    app.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, track_message))

    app.run_polling(close_loop=False)

if __name__ == "__main__":
    main()
