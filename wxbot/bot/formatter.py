"""Formatting utilities for weather reports."""

from __future__ import annotations

import html
import re
from datetime import datetime, timedelta, timezone
from typing import Mapping, Sequence

FlightCategory = str

AGE_PATTERN = re.compile(r"\b(\d{2})(\d{2})(\d{2})Z\b")


def format_wx_bundle(bundle: Mapping[str, Mapping[str, Sequence[str]]]) -> str:
    """Format fetched weather data into an HTML-friendly message."""

    now = datetime.now(timezone.utc)
    sections: list[str] = []

    for icao in sorted(bundle.keys()):
        station_data = bundle.get(icao, {})
        metars = list(station_data.get("metar", []))
        specis = list(station_data.get("speci", []))
        tafs = list(station_data.get("taf", []))

        header = _format_header(icao, metars, now)
        station_lines = [header]
        station_lines.append(_format_reports_section("METAR", metars))
        station_lines.append(_format_reports_section("SPECI", specis))
        station_lines.append(_format_reports_section("TAF", tafs, empty_placeholder="TAF: (нет данных)"))

        sections.append("\n".join(station_lines))

    return "\n\n".join(section for section in sections if section)


def _format_header(icao: str, metars: Sequence[str], now: datetime) -> str:
    """Build the header line for a station, including flight category and METAR age."""

    if not metars:
        return f"<b>{html.escape(icao)}</b> [нет METAR]"

    first_metar = metars[0]
    visibility = _extract_visibility(first_metar)
    ceiling = _extract_ceiling(first_metar)
    category = _determine_flight_category(visibility, ceiling)
    age_hours = _estimate_metar_age_hours(first_metar, now)
    age_fragment = f"age~{age_hours}h" if age_hours is not None else "age~?h"

    return f"<b>{html.escape(icao)}</b> [{category}, {age_fragment}]"


def _format_reports_section(
    label: str,
    reports: Sequence[str],
    *,
    empty_placeholder: str | None = None,
) -> str:
    """Format a list of reports under a section heading."""

    if reports:
        lines = [f"{label}:"]
        lines.extend(f"<code>{html.escape(report)}</code>" for report in reports)
        return "\n".join(lines)

    placeholder = empty_placeholder or f"{label}: (нет данных)"
    return placeholder


def _extract_visibility(raw_metar: str) -> int | None:
    """Extract surface visibility in meters from a METAR string."""

    tokens = raw_metar.split()
    for token in tokens:
        if token.upper() == "CAVOK":
            return 10000
        if _looks_like_visibility_group(token):
            digits = token.lstrip("P")[:4]
            if digits.isdigit():
                return int(digits)
    return None


def _looks_like_visibility_group(token: str) -> bool:
    """Return ``True`` if token resembles a visibility group in meters."""

    if "/" in token:
        return False
    return bool(re.fullmatch(r"P?\d{4}(?:NDV)?", token))


def _extract_ceiling(raw_metar: str) -> int | None:
    """Extract the lowest significant ceiling in feet."""

    ceiling_ft: int | None = None
    tokens = raw_metar.split()
    for token in tokens:
        prefix = token[:3].upper()
        if prefix not in {"BKN", "OVC"}:
            continue
        height_group = token[3:6]
        if height_group.isdigit():
            height_ft = int(height_group) * 100
            if ceiling_ft is None or height_ft < ceiling_ft:
                ceiling_ft = height_ft
    return ceiling_ft


def _determine_flight_category(visibility: int | None, ceiling: int | None) -> FlightCategory:
    """Determine the flight rules category using basic heuristics."""

    if _is_lifr(visibility, ceiling):
        return "LIFR"
    if _is_ifr(visibility, ceiling):
        return "IFR"
    if _is_mvfr(visibility, ceiling):
        return "MVFR"
    return "VFR"


def _is_lifr(visibility: int | None, ceiling: int | None) -> bool:
    return (visibility is not None and visibility < 1600) or (
        ceiling is not None and ceiling < 500
    )


def _is_ifr(visibility: int | None, ceiling: int | None) -> bool:
    if _is_lifr(visibility, ceiling):
        return False
    return (visibility is not None and visibility < 4800) or (
        ceiling is not None and ceiling < 1000
    )


def _is_mvfr(visibility: int | None, ceiling: int | None) -> bool:
    if _is_ifr(visibility, ceiling):
        return False
    return (visibility is not None and visibility < 8000) or (
        ceiling is not None and ceiling < 3000
    )


def _estimate_metar_age_hours(raw_metar: str, now: datetime) -> int | None:
    """Approximate the METAR age in hours based on the DDHHMMZ timestamp."""

    match = AGE_PATTERN.search(raw_metar)
    if not match:
        return None

    day, hour, minute = (int(value) for value in match.groups())

    try:
        observation = datetime(
            year=now.year,
            month=now.month,
            day=day,
            hour=hour,
            minute=minute,
            tzinfo=timezone.utc,
        )
    except ValueError:
        return None

    delta = now - observation
    if delta.total_seconds() < 0:
        delta += timedelta(days=1)

    return int(delta.total_seconds() // 3600)


__all__ = ["format_wx_bundle"]
