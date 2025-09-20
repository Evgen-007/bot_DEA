"""Telegram message handlers for WX Bot."""

from __future__ import annotations

import logging
from collections.abc import Iterable

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import BufferedInputFile, Message

from providers.noaa_adds import NOAAServiceError, fetch_metar_taf_speci

from .briefing import build_briefing_text, render_docx, render_pdf
from .formatter import format_wx_bundle
from .parser import parse_icao_list, parse_icao_sequence
from .routes import build_route_message

router = Router()

LOGGER = logging.getLogger(__name__)

START_MESSAGE = (
    "Привет! Я WX Bot по погоде для российских аэродромов.\n"
    "Используй /wx UUEE UUWW URSS или просто напиши города: Москва, Сочи.\n"
    "Маршрут: /route UUEE URSS (UUWW). Брифинг: /brief UUEE UUWW.\n"
    "Работаю только с ICAO, начинающимися на U (Россия)."
)

ICAO_FILTER_HINT = (
    "Только Россия: укажи ICAO, начинающийся на U*** (например: UUWW UUEE URSS)."
)

ROUTE_USAGE_HINT = (
    "Маршрут: укажи минимум два ICAO России, например /route UUEE URSS (UUWW)."
)

BRIEF_USAGE_HINT = (
    "Брифинг: /brief UUEE UUWW — сформирует DOCX и PDF по российским станциям."
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


@router.message(Command("route"))
async def handle_route_command(message: Message) -> None:
    """Build a corridor report for a FROM-TO-(ALTN) route."""

    text = message.text or ""
    query = _extract_command_argument(text)
    icaos = [icao for icao in parse_icao_sequence(query) if icao.startswith("U")]

    if len(icaos) < 2:
        await message.answer(ROUTE_USAGE_HINT)
        return

    try:
        report = await build_route_message(icaos)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    except NOAAServiceError:
        await message.answer(SOURCE_UNAVAILABLE_MESSAGE)
        return

    await message.answer(report)


@router.message(Command("brief"))
async def handle_brief_command(message: Message) -> None:
    """Generate DOCX/PDF briefing documents for the specified stations."""

    text = message.text or ""
    query = _extract_command_argument(text)
    if not query.strip():
        await message.answer(BRIEF_USAGE_HINT)
        return

    icaos = [icao for icao in parse_icao_sequence(query) if icao.startswith("U")]
    if not icaos:
        icaos = [icao for icao in parse_icao_list(query) if icao.startswith("U")]

    if not icaos:
        await message.answer(ICAO_FILTER_HINT)
        return

    try:
        wx_bundle = await fetch_metar_taf_speci(icaos)
    except NOAAServiceError:
        await message.answer(SOURCE_UNAVAILABLE_MESSAGE)
        return

    if _is_bundle_empty(wx_bundle.values()):
        await message.answer(NO_DATA_MESSAGE)
        return

    briefing_text = build_briefing_text(icaos, wx_bundle)
    docx_bytes = render_docx(briefing_text)
    pdf_bytes = render_pdf(briefing_text)

    docx_file = BufferedInputFile(docx_bytes, filename=f"briefing_{'_'.join(icaos)}.docx")
    pdf_file = BufferedInputFile(pdf_bytes, filename=f"briefing_{'_'.join(icaos)}.pdf")

    await message.answer_document(docx_file, caption="НАМС-86 брифинг (DOCX)")
    await message.answer_document(pdf_file, caption="НАМС-86 брифинг (PDF)")


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
