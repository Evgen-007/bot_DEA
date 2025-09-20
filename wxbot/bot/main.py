"""Entry point for WX Bot (RU-only)."""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Final

from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from dotenv import load_dotenv
from pydantic import BaseModel, Field, ValidationError

from .handlers import router

ALLOWED_UPDATES: Final[list[str]] = ["message", "callback_query"]


class BotSettings(BaseModel):
    """Runtime settings loaded from environment variables."""

    telegram_bot_token: str = Field(alias="TELEGRAM_BOT_TOKEN", min_length=1)

    @classmethod
    def load(cls) -> "BotSettings":
        """Load settings from environment variables."""

        load_dotenv()
        data = {"TELEGRAM_BOT_TOKEN": os.getenv("TELEGRAM_BOT_TOKEN", "").strip()}
        return cls.model_validate(data)


def configure_logging() -> None:
    """Configure basic application logging."""

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )


async def _run_bot(settings: BotSettings) -> None:
    """Run polling for the Telegram bot."""

    bot = Bot(token=settings.telegram_bot_token, parse_mode=ParseMode.HTML)
    dispatcher = Dispatcher()
    dispatcher.include_router(router)

    logging.info("Starting WX Bot polling")
    await dispatcher.start_polling(bot, allowed_updates=ALLOWED_UPDATES)


def main() -> None:
    """Entrypoint wrapper used by ``python -m bot.main``."""

    configure_logging()
    try:
        settings = BotSettings.load()
    except ValidationError as exc:  # pragma: no cover - configuration error path
        logging.error("Failed to load bot settings: %s", exc)
        raise SystemExit(2) from exc

    asyncio.run(_run_bot(settings))


if __name__ == "__main__":  # pragma: no cover - CLI execution
    main()
