"""Telegram-бот как отдельный процесс. Складской синк сюда не входит.

Резервы и пуш остатков: python run_sync.py
Панель: python run_web.py
Упаковщики: python run_api.py
"""

from __future__ import annotations

import asyncio
import logging

from telegram import Update
from telegram.error import NetworkError, TimedOut
from telegram.ext import Application, CommandHandler, ContextTypes

from app.config import load_settings

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    _ = context
    if update.message is None:
        return
    await update.message.reply_text(
        "Этот бот больше не управляет складом.\n"
        "Резервы и остатки на маркетплейсы крутит процесс run_sync.py.\n"
        "Сборка, отгрузка и номенклатура — в панели /warehouse."
    )


async def main() -> None:
    settings = load_settings()
    if not settings.telegram_bot_token:
        logger.info(
            "TELEGRAM_BOT_TOKEN пуст — бот не запускается. "
            "Складской синк: python run_sync.py"
        )
        return

    app = (
        Application.builder()
        .token(settings.telegram_bot_token)
        .connect_timeout(60.0)
        .read_timeout(60.0)
        .write_timeout(60.0)
        .pool_timeout(20.0)
        .get_updates_connect_timeout(60.0)
        .get_updates_read_timeout(120.0)
        .build()
    )
    app.add_handler(CommandHandler("start", start))

    max_connect_attempts = 5
    for attempt in range(1, max_connect_attempts + 1):
        try:
            await app.initialize()
            break
        except (TimedOut, NetworkError) as exc:
            if attempt >= max_connect_attempts:
                logger.error("Не удалось подключиться к Telegram API после %s попыток", max_connect_attempts)
                raise
            delay = min(5 * attempt, 30)
            logger.warning(
                "Таймаут Telegram при initialize (попытка %s/%s): %s. Повтор через %s с...",
                attempt,
                max_connect_attempts,
                exc,
                delay,
            )
            await asyncio.sleep(delay)

    await app.start()
    await app.updater.start_polling()
    logger.info("Telegram-бот в режиме заглушки (складской синк в run_sync.py)")
    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await app.updater.stop()
        await app.stop()
        await app.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
