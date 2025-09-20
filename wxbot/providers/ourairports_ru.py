"""Local OurAirports lookup for Russian aerodromes."""

from __future__ import annotations

import csv
from functools import lru_cache
from pathlib import Path
from typing import Iterable, Iterator

from pydantic import BaseModel, Field, ValidationError

DATA_PATH = Path(__file__).resolve().parent.parent / "data" / "ourairports_ru.csv"


class AirportRecord(BaseModel):
    """Representation of a Russian airport entry."""

    ident: str = Field(min_length=4)
    name: str = Field(default="")
    municipality: str = Field(default="")
    country: str = Field(min_length=2)
    latitude_deg: float | None = Field(default=None)
    longitude_deg: float | None = Field(default=None)

    def matches(self, haystack: str) -> bool:
        """Return ``True`` if the airport name or municipality appears in the haystack."""

        fields: Iterable[str] = (self.name.lower(), self.municipality.lower())
        return any(field and field in haystack for field in fields)


@lru_cache(maxsize=1)
def _load_ru_index() -> dict[str, AirportRecord]:
    """Load airport records for Russia into a dictionary keyed by ICAO."""

    index: dict[str, AirportRecord] = {}
    with DATA_PATH.open(encoding="utf-8", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for row in reader:
            try:
                record = AirportRecord.model_validate(row)
            except ValidationError:
                continue
            ident = record.ident.upper()
            if not ident.startswith("U"):
                continue
            if record.country.upper() != "RU":
                continue
            index[ident] = record
    return index


def resolve_ru_tokens(text: str) -> list[str]:
    """Resolve Russian city or airport names to ICAO identifiers."""

    haystack = text.lower()
    matches: set[str] = set()
    for ident, record in _load_ru_index().items():
        if record.matches(haystack):
            matches.add(ident)
    return sorted(matches)


def get_airport(icao: str) -> AirportRecord | None:
    """Return airport information for the given ICAO identifier."""

    return _load_ru_index().get(icao.upper())


def iter_airports() -> Iterator[AirportRecord]:
    """Iterate over known Russian airports."""

    yield from _load_ru_index().values()


__all__ = [
    "resolve_ru_tokens",
    "_load_ru_index",
    "AirportRecord",
    "get_airport",
    "iter_airports",
]
