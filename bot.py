import os
import json
import base64
import logging
import tempfile
import shutil
import time

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)
import yt_dlp

# ------------------ الإعدادات ------------------
BOT_TOKEN = "8751872695:AAFRuqRCi2Lyf-9u728NvYJxjrZ-qhmtRjA"

ADMIN_ID = 8355232956
USERS_FILE = os.path.join(os.path.dirname(__file__), "users.json")
COOKIES_FILE = os.path.join(os.path.dirname(__file__), "cookies.txt")

# سيرفر البوت المحلي (Local Bot API) عشان نتخطى حد الـ50 ميجا
LOCAL_API_HOST = os.environ.get("LOCAL_API_HOST", "http://localhost:8081")

MAX_RUNTIME_SECONDS = int(os.environ.get("MAX_RUNTIME_SECONDS", 5 * 60 * 60 + 45 * 60))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ------------------ إعداد ملف الكوكيز من متغير بيئة base64 ------------------
def setup_cookies():
    b64 = os.environ.get("YT_COOKIES_B64")
    if b64:
        try:
            with open(COOKIES_FILE, "wb") as f:
                f.write(base64.b64decode(b64))
            logger.info("تم إعداد ملف الكوكيز")
        except Exception:
            logger.exception("فشل فك تشفير الكوكيز")


# ------------------ تخزين المستخدمين ------------------
def load_users() -> set:
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, OSError):
            logger.warning("users.json فاسد أو مش موجود")
    return set()


def save_users(users: set):
    with open(USERS_FILE, "w", encoding="utf-8") as f:
        json.dump(sorted(users), f)


def register_user(user_id: int, users: set):
    if user_id not in users:
        users.add(user_id)
        save_users(users)


# ------------------ دوال يوتيوب ------------------
def is_youtube_url(text: str) -> bool:
    text = text.lower()
    return "youtube.com" in text or "youtu.be" in text


def _base_opts():
    opts = {
        "quiet": True,
        "noplaylist": True,
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
    }
    if os.path.exists(COOKIES_FILE):
        opts["cookiefile"] = COOKIES_FILE
    return opts


def download_video(url: str, out_dir: str) -> str:
    """يحمل أفضل جودة فيديو متاحة، من غير حد حجم (السيرفر المحلي بيسمح لحد 2GB)"""
    out_template = os.path.join(out_dir, "%(title).80s.%(ext)s")
    opts = _base_opts()
    opts.update(
        {
            "format": "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best",
            "outtmpl": out_template,
            "merge_output_format": "mp4",
        }
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)
        if not os.path.exists(filepath):
            base, _ = os.path.splitext(filepath)
            filepath = base + ".mp4"
    return filepath


def download_audio(url: str, out_dir: str) -> str:
    """يحمل الصوت بس بأفضل جودة"""
    out_template = os.path.join(out_dir, "%(title).80s.%(ext)s")
    opts = _base_opts()
    opts.update(
        {
            "format": "bestaudio/best",
            "outtmpl": out_template,
            "postprocessors": [
                {
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "192",
                }
            ],
        }
    )
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)
        base, _ = os.path.splitext(filepath)
        mp3_path = base + ".mp3"
        return mp3_path if os.path.exists(mp3_path) else filepath


# ------------------ Handlers عامة ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = context.bot_data.setdefault("users", load_users())
    register_user(update.effective_user.id, users)
    await update.message.reply_text(
        "أهلاً بيك 👋\nابعتلي رابط فيديو من يوتيوب واختار فيديو ولا صوت."
    )


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = context.bot_data.setdefault("users", load_users())
    register_user(update.effective_user.id, users)

    text = update.message.text.strip()
    if not is_youtube_url(text):
        await update.message.reply_text("ابعت رابط يوتيوب صحيح من فضلك.")
        return

    context.user_data["url"] = text

    keyboard = [
        [
            InlineKeyboardButton("🎥 فيديو", callback_data="video"),
            InlineKeyboardButton("🎵 صوت", callback_data="audio"),
        ]
    ]
    await update.message.reply_text(
        "اختار هتحمل إيه:", reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def handle_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    choice = query.data
    url = context.user_data.get("url")
    if not url:
        await query.edit_message_text("❌ الرابط راح، ابعته تاني.")
        return

    await query.edit_message_text("⏳ بحمل، استنى شوية (ممكن ياخد وقت حسب حجم الفيديو)...")

    tmp_dir = tempfile.mkdtemp()
    try:
        if choice == "audio":
            filepath = download_audio(url, tmp_dir)
        else:
            filepath = download_video(url, tmp_dir)

        size_mb = os.path.getsize(filepath) / 1_000_000
        await query.edit_message_text(f"📤 بترفع الملف ({size_mb:.0f}MB)...")

        with open(filepath, "rb") as f:
            if choice == "audio":
                await context.bot.send_audio(chat_id=query.message.chat_id, audio=f)
            else:
                await context.bot.send_video(
                    chat_id=query.message.chat_id, video=f, supports_streaming=True
                )
        await query.edit_message_text("✅ تم بنجاح.")
    except Exception as e:
        logger.exception("download failed")
        await query.edit_message_text(f"❌ حصل خطأ أثناء التحميل: {e}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ------------------ أوامر الأونر فقط ------------------
def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != ADMIN_ID:
            await update.message.reply_text("🚫 الأمر ده للأونر بس.")
            return
        return await func(update, context)

    return wrapper


@admin_only
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = context.bot_data.setdefault("users", load_users())
    await update.message.reply_text(f"👥 عدد المستخدمين: {len(users)}")


@admin_only
async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = context.bot_data.setdefault("users", load_users())
    replied = update.message.reply_to_message
    text_after_cmd = update.message.text.partition(" ")[2].strip()

    if not replied and not text_after_cmd:
        await update.message.reply_text(
            "استخدم: /broadcast نص الرسالة\n"
            "أو اعمل ريبلاي على رسالة (نص/صورة/فيديو) واكتب /broadcast"
        )
        return

    sent, failed = 0, 0
    status_msg = await update.message.reply_text("🚀 بدأ البث...")
    for user_id in list(users):
        try:
            if replied:
                await context.bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=update.effective_chat.id,
                    message_id=replied.message_id,
                )
            else:
                await context.bot.send_message(chat_id=user_id, text=text_after_cmd)
            sent += 1
        except Exception:
            failed += 1
        time.sleep(0.05)

    await status_msg.edit_text(f"✅ اتبعت لـ {sent} مستخدم.\n❌ فشل مع {failed}.")


# ------------------ إيقاف آمن بعد وقت معين ------------------
async def shutdown_after_timeout(app: Application):
    import asyncio

    await asyncio.sleep(MAX_RUNTIME_SECONDS)
    logger.info("الوقت خلص، البوت بيقفل بأمان")
    app.stop_running()


async def post_init(app: Application):
    app.bot_data["users"] = load_users()
    app.create_task(shutdown_after_timeout(app))


# ------------------ التشغيل ------------------
def main():
    setup_cookies()

    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .base_url(f"{LOCAL_API_HOST}/bot")
        .base_file_url(f"{LOCAL_API_HOST}/file/bot")
        .local_mode(True)
        .post_init(post_init)
    )
    app = builder.build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(handle_choice))

    logger.info("Bot is running (local API mode)...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
