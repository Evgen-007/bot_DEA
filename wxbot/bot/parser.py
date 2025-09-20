"""Utilities for parsing station identifiers from user input."""

from __future__ import annotations

import re
from typing import Final

from providers.ourairports_ru import resolve_ru_tokens

ICAO_PATTERN: Final[re.Pattern[str]] = re.compile(r"\b(U[A-Z0-9]{3})\b", re.IGNORECASE)


def parse_icao_list(text: str) -> list[str]:
    """Extract ICAO identifiers and mapped stations from the provided text."""

    icaos: set[str] = set()
    for match in ICAO_PATTERN.findall(text):
        icaos.add(match.upper())

    for resolved in resolve_ru_tokens(text):
        icaos.add(resolved.upper())

    return sorted(icaos)


__all__ = ["parse_icao_list"]
