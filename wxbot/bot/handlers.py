"""Telegram message handlers for WX Bot."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import Message

from providers.noaa_adds import NOAAServiceError, fetch_metar_taf_speci

from .formatter import format_wx_bundle
from .parser import parse_icao_list

router = Router()

LOGGER = logging.getLogger(__name__)

START_MESSAGE = (
    "Привет! Я WX Bot по погоде для российских аэродромов.\n"
    "Используй команду /wx UUEE UUWW URSS или просто напиши города: Москва, Сочи.\n"
    "Работаю только с ICAO, начинающимися на U (Россия)."
)

ICAO_FILTER_HINT = (
    "Только Россия: укажи ICAO, начинающийся на U*** (например: UUWW UUEE URSS)."
)

SOURCE_UNAVAILABLE_MESSAGE = "Источник недоступен, попробуйте позже."
NO_DATA_MESSAGE = "Нет данных по указанным станциям за выбранный период."


@router.message(CommandStart())
async def handle_start(message: Message) -> None:
    """Send a brief help message to the user."""

    await message.answer(START_MESSAGE)


@router.message(Command("wx"))
async def handle_wx_command(message: Message) -> None:
    """Process the /wx command and fetch weather for the provided ICAO list."""

    text = message.text or ""
    query = _extract_command_argument(text)
    await _handle_wx(message, query)


@router.message(F.text)
async def handle_text_query(message: Message) -> None:
    """Try to interpret plain text messages as station identifiers."""

    if message.text:
        await _handle_wx(message, message.text)


def _extract_command_argument(text: str) -> str:
    """Remove the command prefix from the raw message text."""

    parts = text.split(maxsplit=1)
    return parts[1] if len(parts) > 1 else ""


async def _handle_wx(message: Message, raw_query: str) -> None:
    """Shared logic for weather lookups."""

    if not raw_query.strip():
        await message.answer(ICAO_FILTER_HINT)
        return

    requested_icaos = parse_icao_list(raw_query)
    filtered_icaos = [icao for icao in requested_icaos if icao.startswith("U")]

    if not filtered_icaos:
        await message.answer(ICAO_FILTER_HINT)
        return

    LOGGER.info("Fetching weather for: %s", ", ".join(filtered_icaos))

    try:
        wx_bundle = await fetch_metar_taf_speci(filtered_icaos)
    except NOAAServiceError:
        await message.answer(SOURCE_UNAVAILABLE_MESSAGE)
        return

    if _is_bundle_empty(wx_bundle.values()):
        await message.answer(NO_DATA_MESSAGE)
        return

    await message.answer(format_wx_bundle(wx_bundle))


def _is_bundle_empty(bundle: Iterable[dict[str, list[str]]]) -> bool:
    """Return ``True`` when all report lists are empty."""

    for station_data in bundle:
        if any(station_data.get(key) for key in ("metar", "speci", "taf")):
            return False
    return True


__all__ = ["router"]
