import os
import json
import logging
import tempfile
import shutil
import time

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
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


def download_video(url: str, out_dir: str) -> str:
    """يحمل الفيديو على طول بأفضل جودة تحت حد تليجرام، من غير ما يجيب المعلومات الأول"""
    out_template = os.path.join(out_dir, "%(title).80s.%(ext)s")
    ydl_opts = {
        "format": (
            "bestvideo[ext=mp4][filesize<45M]+bestaudio[ext=m4a]"
            "/best[ext=mp4][filesize<45M]"
            "/best[filesize<45M]"
            "/best"
        ),
        "outtmpl": out_template,
        "merge_output_format": "mp4",
        "quiet": True,
        "noplaylist": True,
        # بيخلي yt-dlp يتصرف كإنه تطبيق يوتيوب على أندرويد
        # عشان يتخطى مشكلة "Sign in to confirm you're not a bot"
        # اللي بتحصل مع الأي بيهات بتاعة سيرفرات GitHub Actions
        "extractor_args": {"youtube": {"player_client": ["android", "web"]}},
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
        "ابعتلي رابط فيديو من يوتيوب وهحمله على طول."
    )


async def handle_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    users = context.bot_data.setdefault("users", load_users())
    register_user(update.effective_user.id, users)

    text = update.message.text.strip()
    if not is_youtube_url(text):
        await update.message.reply_text("ابعت رابط يوتيوب صحيح من فضلك.")
        return

    msg = await update.message.reply_text("⏳ بحمل الفيديو، استنى شوية...")

    tmp_dir = tempfile.mkdtemp()
    try:
        filepath = download_video(text, tmp_dir)
        size_mb = os.path.getsize(filepath) / 1_000_000

        if size_mb > MAX_TELEGRAM_FILE_MB:
            await msg.edit_text(
                f"❌ حجم الفيديو {size_mb:.0f}MB وده أكبر من حد تليجرام "
                f"({MAX_TELEGRAM_FILE_MB}MB)، مقدرش أبعته."
            )
            return

        await msg.edit_text("📤 بترفع الفيديو...")
        with open(filepath, "rb") as f:
            await context.bot.send_video(
                chat_id=update.effective_chat.id, video=f, supports_streaming=True
            )
        await msg.edit_text("✅ تم التحميل بنجاح.")
    except Exception as e:
        logger.exception("download failed")
        await msg.edit_text(f"❌ حصل خطأ أثناء التحميل: {e}")
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

    logger.info("Bot is running...")
    app.run_polling(close_loop=False)


if __name__ == "__main__":
    main()
