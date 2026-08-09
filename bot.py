import os
import json
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

ADMIN_ID = 8355232956  # الأونر الوحيد اللي يقدر يتحكم في البوت
MAX_TELEGRAM_FILE_MB = 50  # حد تليجرام العادي للبوتات
USERS_FILE = os.path.join(os.path.dirname(__file__), "users.json")

MAX_RUNTIME_SECONDS = int(os.environ.get("MAX_RUNTIME_SECONDS", 5 * 60 * 60 + 45 * 60))  # 5س 45د

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ------------------ تخزين المستخدمين ------------------
def load_users() -> set:
    if os.path.exists(USERS_FILE):
        try:
            with open(USERS_FILE, "r", encoding="utf-8") as f:
                return set(json.load(f))
        except (json.JSONDecodeError, OSError):
            logger.warning("users.json فاسد أو مش موجود، هبدأ بقائمة فاضية")
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


def get_formats(url: str):
    ydl_opts = {"quiet": True, "skip_download": True}
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        return ydl.extract_info(url, download=False)


def download_video(url: str, format_id: str, out_dir: str) -> str:
    out_template = os.path.join(out_dir, "%(title).80s.%(ext)s")
    ydl_opts = {
        "format": f"{format_id}+bestaudio/best" if format_id != "bestaudio" else "bestaudio",
        "outtmpl": out_template,
        "merge_output_format": "mp4",
        "quiet": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(url, download=True)
        filepath = ydl.prepare_filename(info)
        if not os.path.exists(filepath):
            base, _ = os.path.splitext(filepath)
            filepath = base + ".mp4"
    return filepath


# ------------------ Handlers عامة ------------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = context.bot_data.setdefault("users", load_users())
    register_user(update.effective_user.id, users)

    await update.message.reply_text(
        "أهلاً بيك 👋\n"
        "ابعتلي رابط فيديو من يوتيوب وهختارلك الجودة المناسبة وأحمله."
    )


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = context.bot_data.setdefault("users", load_users())
    register_user(update.effective_user.id, users)

    text = update.message.text.strip()
    if not is_youtube_url(text):
        await update.message.reply_text("ابعت رابط يوتيوب صحيح من فضلك.")
        return

    msg = await update.message.reply_text("⏳ بجيب معلومات الفيديو...")

    try:
        info = get_formats(text)
    except Exception as e:
        logger.exception("extract_info failed")
        await msg.edit_text(f"❌ مقدرتش أجيب معلومات الفيديو: {e}")
        return

    context.user_data["url"] = text
    context.user_data["title"] = info.get("title", "video")

    seen = set()
    buttons = []
    for f in info.get("formats", []):
        height = f.get("height")
        ext = f.get("ext")
        format_id = f.get("format_id")
        filesize = f.get("filesize") or f.get("filesize_approx")
        if not height or ext != "mp4" or not format_id:
            continue
        if height in seen:
            continue
        seen.add(height)
        size_txt = f" (~{filesize / 1_000_000:.0f}MB)" if filesize else ""
        buttons.append(
            InlineKeyboardButton(f"{height}p{size_txt}", callback_data=f"dl|{format_id}")
        )

    buttons.sort(key=lambda b: int(b.text.split("p")[0]))
    buttons.append(InlineKeyboardButton("🎵 صوت فقط (MP3)", callback_data="dl|bestaudio"))

    if not buttons:
        await msg.edit_text("❌ مفيش جودات متاحة للفيديو ده.")
        return

    keyboard = [buttons[i : i + 2] for i in range(0, len(buttons), 2)]
    await msg.edit_text(
        f"🎬 {info.get('title')}\nاختار الجودة:",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def handle_quality_choice(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    _, format_id = query.data.split("|")
    url = context.user_data.get("url")
    if not url:
        await query.edit_message_text("❌ الرابط راح، ابعته تاني.")
        return

    await query.edit_message_text("⏳ بحمل الفيديو، استنى شوية...")

    tmp_dir = tempfile.mkdtemp()
    try:
        filepath = download_video(url, format_id, tmp_dir)
        size_mb = os.path.getsize(filepath) / 1_000_000

        if size_mb > MAX_TELEGRAM_FILE_MB:
            await query.edit_message_text(
                f"❌ حجم الملف {size_mb:.0f}MB وده أكبر من حد تليجرام "
                f"({MAX_TELEGRAM_FILE_MB}MB). جرب جودة أقل."
            )
            return

        await query.edit_message_text("📤 بترفع الملف...")
        with open(filepath, "rb") as f:
            if format_id == "bestaudio":
                await context.bot.send_audio(chat_id=query.message.chat_id, audio=f)
            else:
                await context.bot.send_video(
                    chat_id=query.message.chat_id, video=f, supports_streaming=True
                )
        await query.edit_message_text("✅ تم التحميل بنجاح.")
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
    logger.info(f"البوت هيشتغل لحد أقصى {MAX_RUNTIME_SECONDS // 3600} ساعة تقريبًا")
    import asyncio

    await asyncio.sleep(MAX_RUNTIME_SECONDS)
    logger.info("الوقت المحدد خلص، البوت بيقفل بأمان (هيشتغل تاني مع الجدولة الجاية)")
    app.stop_running()


async def post_init(app: Application):
    app.bot_data["users"] = load_users()
    app.create_task(shutdown_after_timeout(app))


# ------------------ التشغيل ------------------
def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("stats", stats))
    app.add_handler(CommandHandler("broadcast", broadcast))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_link))
    app.add_handler(CallbackQueryHandler(handle_quality_choice))

    logger.info("Bot is running...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
