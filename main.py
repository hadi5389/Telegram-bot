"""
Telegram Bot powered by NVIDIA Build API - production entrypoint.

Architecture:
    Telegram  --(webhook, HTTPS POST)-->  FastAPI  --> python-telegram-bot Application
                                                          |
                                                          v
                                              NVIDIA Build API (httpx, async, retries)

All secrets are read exclusively from environment variables. Nothing sensitive
is hard-coded anywhere in this file.
"""

import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager
from typing import List, Optional

import httpx
from fastapi import FastAPI, Request, Response
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ---------------------------------------------------------------------------
# Logging configuration
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)
logger = logging.getLogger("telegram-nvidia-bot")

# Quiet down some very chatty third-party loggers while keeping our own logs.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.INFO)

# ---------------------------------------------------------------------------
# Environment variables (all secrets/config come from here — never hard-code)
# ---------------------------------------------------------------------------
TELEGRAM_BOT_TOKEN: Optional[str] = os.getenv("TELEGRAM_BOT_TOKEN")
NVIDIA_API_KEY: Optional[str] = os.getenv("NVIDIA_API_KEY")
NVIDIA_MODEL: str = os.getenv("NVIDIA_MODEL", "meta/llama-3.1-70b-instruct")
WEBHOOK_URL: Optional[str] = os.getenv("WEBHOOK_URL")  # e.g. https://your-app.up.railway.app
PORT: int = int(os.getenv("PORT", "8080"))

_REQUIRED_VARS = {
    "TELEGRAM_BOT_TOKEN": TELEGRAM_BOT_TOKEN,
    "NVIDIA_API_KEY": NVIDIA_API_KEY,
    "WEBHOOK_URL": WEBHOOK_URL,
}
_missing = [name for name, value in _REQUIRED_VARS.items() if not value]
if _missing:
    logger.error("Missing required environment variables: %s", ", ".join(_missing))
    raise RuntimeError(
        "Missing required environment variables: "
        f"{', '.join(_missing)}. Set them in Railway -> Variables before deploying."
    )

# Use the bot token as part of the webhook path so random requests to the
# root domain cannot trigger update processing.
WEBHOOK_PATH = f"/webhook/{TELEGRAM_BOT_TOKEN}"
FULL_WEBHOOK_URL = f"{WEBHOOK_URL.rstrip('/')}{WEBHOOK_PATH}"

NVIDIA_API_URL = "https://integrate.api.nvidia.com/v1/chat/completions"

TELEGRAM_MAX_MESSAGE_LENGTH = 4096

HTTP_TIMEOUT = httpx.Timeout(connect=10.0, read=60.0, write=10.0, pool=10.0)
MAX_RETRIES = 3
RETRY_BACKOFF_SECONDS = 2.0
RETRYABLE_STATUS_CODES = {429, 500, 502, 503, 504}

# ---------------------------------------------------------------------------
# User-facing text (Persian)
# ---------------------------------------------------------------------------
WELCOME_MESSAGE = (
    "سلام! 👋\n\n"
    "به ربات هوش مصنوعی خوش آمدید.\n"
    "هر پیام متنی که برای من ارسال کنید را به مدل هوش مصنوعی می‌فرستم "
    "و پاسخ آن را برایتان بازمی‌گردانم.\n\n"
    "برای مشاهده راهنما، دستور /help را ارسال کنید."
)

HELP_MESSAGE = (
    "📖 راهنمای استفاده از ربات:\n\n"
    "/start - نمایش پیام خوش‌آمدگویی\n"
    "/help - نمایش همین راهنما\n\n"
    "برای گفتگو، کافی است پیام متنی خود را ارسال کنید. "
    "پاسخ توسط مدل هوش مصنوعی از طریق NVIDIA Build API تولید می‌شود.\n\n"
    "در صورت بروز خطا یا کندی، لطفاً کمی صبر کرده و دوباره تلاش کنید."
)

ERROR_MESSAGE_GENERIC = (
    "⚠️ متأسفانه در پردازش درخواست شما خطایی رخ داد. لطفاً کمی بعد دوباره تلاش کنید."
)

ERROR_MESSAGE_SERVICE_UNAVAILABLE = (
    "⚠️ در حال حاضر امکان ارتباط با سرویس هوش مصنوعی وجود ندارد. "
    "لطفاً چند لحظه دیگر دوباره تلاش کنید."
)

ERROR_MESSAGE_EMPTY_REPLY = (
    "⚠️ مدل هوش مصنوعی پاسخ خالی برگرداند. لطفاً دوباره تلاش کنید یا سوال خود را متفاوت بیان کنید."
)


# ---------------------------------------------------------------------------
# NVIDIA Build API client
# ---------------------------------------------------------------------------
async def query_nvidia_api(user_message: str) -> str:
    """
    Send a chat-completion request to the NVIDIA Build API and return the
    assistant's reply text, unchanged.

    Retries on timeouts and on retryable HTTP status codes (429, 5xx) using
    exponential backoff. Raises RuntimeError with a Persian, user-safe
    message when the call ultimately fails.
    """
    headers = {
        "Authorization": f"Bearer {NVIDIA_API_KEY}",
        "Accept": "application/json",
        "Content-Type": "application/json; charset=utf-8",
    }
    payload = {
        "model": NVIDIA_MODEL,
        "messages": [
            {"role": "user", "content": user_message},
        ],
        "temperature": 0.5,
        "top_p": 1.0,
        "max_tokens": 1024,
        "stream": False,
    }

    last_error: Optional[BaseException] = None

    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT) as client:
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = await client.post(NVIDIA_API_URL, headers=headers, json=payload)
            except httpx.TimeoutException as exc:
                last_error = exc
                logger.warning(
                    "NVIDIA API attempt %s/%s timed out: %s", attempt, MAX_RETRIES, exc
                )
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue
            except httpx.HTTPError as exc:
                last_error = exc
                logger.warning(
                    "NVIDIA API attempt %s/%s network error: %s", attempt, MAX_RETRIES, exc
                )
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue

            if response.status_code == 200:
                try:
                    data = response.json()
                    content = data["choices"][0]["message"]["content"]
                except (KeyError, IndexError, TypeError, ValueError) as parse_error:
                    logger.error("Unexpected NVIDIA API response shape: %s", response.text[:500])
                    raise RuntimeError(
                        "پاسخ دریافتی از سرویس هوش مصنوعی قابل تفسیر نبود."
                    ) from parse_error
                return content.strip()

            if response.status_code in RETRYABLE_STATUS_CODES:
                last_error = RuntimeError(
                    f"NVIDIA API temporary error {response.status_code}: {response.text[:300]}"
                )
                logger.warning(
                    "NVIDIA API attempt %s/%s failed with status %s. Retrying...",
                    attempt,
                    MAX_RETRIES,
                    response.status_code,
                )
                await asyncio.sleep(RETRY_BACKOFF_SECONDS * attempt)
                continue

            # Non-retryable error (e.g. 400, 401, 403, 404)
            logger.error(
                "NVIDIA API returned non-retryable status %s: %s",
                response.status_code,
                response.text[:500],
            )
            raise RuntimeError(
                f"سرویس هوش مصنوعی خطای {response.status_code} برگرداند. "
                "لطفاً تنظیمات NVIDIA_API_KEY و NVIDIA_MODEL را بررسی کنید."
            )

    logger.error("NVIDIA API failed after %s attempts. Last error: %s", MAX_RETRIES, last_error)
    raise RuntimeError(ERROR_MESSAGE_SERVICE_UNAVAILABLE)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def split_message(text: str, limit: int = TELEGRAM_MAX_MESSAGE_LENGTH) -> List[str]:
    """
    Split a long text into chunks that respect Telegram's per-message length
    limit. Prefers splitting on newlines, then spaces, to keep chunks
    readable; falls back to a hard cut if no good boundary is found.
    """
    if len(text) <= limit:
        return [text]

    chunks: List[str] = []
    remaining = text
    while len(remaining) > limit:
        split_at = remaining.rfind("\n", 0, limit)
        if split_at <= 0:
            split_at = remaining.rfind(" ", 0, limit)
        if split_at <= 0:
            split_at = limit
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


# ---------------------------------------------------------------------------
# Telegram handlers
# ---------------------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(WELCOME_MESSAGE)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None:
        return
    await update.message.reply_text(HELP_MESSAGE)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.message is None or not update.message.text:
        return

    user_id = update.effective_user.id if update.effective_user else "unknown"
    user_text = update.message.text
    logger.info("Received message from user_id=%s (%d chars)", user_id, len(user_text))

    if update.effective_chat is not None:
        try:
            await context.bot.send_chat_action(
                chat_id=update.effective_chat.id, action="typing"
            )
        except Exception:
            logger.exception("Failed to send typing action for user_id=%s", user_id)

    try:
        reply_text = await query_nvidia_api(user_text)
    except RuntimeError as exc:
        logger.error("AI reply failed for user_id=%s: %s", user_id, exc)
        await update.message.reply_text(str(exc) or ERROR_MESSAGE_GENERIC)
        return
    except Exception:
        logger.exception("Unexpected error while handling message from user_id=%s", user_id)
        await update.message.reply_text(ERROR_MESSAGE_GENERIC)
        return

    if not reply_text:
        await update.message.reply_text(ERROR_MESSAGE_EMPTY_REPLY)
        return

    for chunk in split_message(reply_text):
        try:
            await update.message.reply_text(chunk)
        except Exception:
            logger.exception("Failed to send a message chunk to user_id=%s", user_id)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(ERROR_MESSAGE_GENERIC)
        except Exception:
            logger.exception("Failed to notify the user about the error.")


# ---------------------------------------------------------------------------
# Application wiring
# ---------------------------------------------------------------------------
telegram_app: Optional[Application] = None


def build_telegram_application() -> Application:
    application = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message)
    )
    application.add_error_handler(error_handler)
    return application


@asynccontextmanager
async def lifespan(app: FastAPI):
    global telegram_app
    logger.info("Starting up Telegram application...")
    telegram_app = build_telegram_application()

    await telegram_app.initialize()
    await telegram_app.start()

    try:
        await telegram_app.bot.set_webhook(
            url=FULL_WEBHOOK_URL,
            allowed_updates=Update.ALL_TYPES,
            drop_pending_updates=True,
        )
        logger.info("Webhook successfully set to: %s", FULL_WEBHOOK_URL)
    except Exception:
        logger.exception("Failed to set webhook on startup.")

    yield

    logger.info("Shutting down Telegram application...")
    try:
        await telegram_app.bot.delete_webhook()
    except Exception:
        logger.exception("Failed to delete webhook on shutdown.")
    await telegram_app.stop()
    await telegram_app.shutdown()


app = FastAPI(title="Telegram NVIDIA Bot", lifespan=lifespan)


@app.get("/")
async def root() -> dict:
    return {"status": "ok", "service": "telegram-nvidia-bot"}


@app.get("/health")
async def health() -> dict:
    return {"status": "healthy"}


@app.post(WEBHOOK_PATH)
async def telegram_webhook(request: Request) -> Response:
    if telegram_app is None:
        logger.error("Telegram application is not initialized yet.")
        return Response(status_code=503)

    try:
        payload = await request.json()
    except Exception:
        logger.exception("Failed to parse incoming webhook payload as JSON.")
        return Response(status_code=400)

    try:
        update = Update.de_json(payload, telegram_app.bot)
        await telegram_app.process_update(update)
    except Exception:
        logger.exception("Failed to process incoming Telegram update.")
        return Response(status_code=500)

    return Response(status_code=200)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=PORT, log_level="info")
