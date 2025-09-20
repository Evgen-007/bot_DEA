"""AWC SIGMET GeoJSON provider."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, Mapping, Sequence

import httpx

SIGMET_URL = "https://aviationweather.gov/api/data/geojson"


class SigmetServiceError(RuntimeError):
    """Raised when the SIGMET feed cannot be retrieved."""


@dataclass(frozen=True)
class GeoBounds:
    """Simple geographic bounding box."""

    min_lat: float
    max_lat: float
    min_lon: float
    max_lon: float

    def contains(self, lat: float, lon: float) -> bool:
        """Return ``True`` if the coordinate lies within the bounds."""

        norm_lon = _normalize_longitude(lon)
        min_lon = _normalize_longitude(self.min_lon)
        max_lon = _normalize_longitude(self.max_lon)

        if min_lon <= max_lon:
            within_lon = min_lon <= norm_lon <= max_lon
        else:  # Bounds cross the dateline
            within_lon = norm_lon >= min_lon or norm_lon <= max_lon

        return self.min_lat <= lat <= self.max_lat and within_lon

    def expand(self, delta_lat: float, delta_lon: float) -> "GeoBounds":
        """Return an expanded bounding box by the provided deltas."""

        return GeoBounds(
            min_lat=max(-90.0, self.min_lat - delta_lat),
            max_lat=min(90.0, self.max_lat + delta_lat),
            min_lon=_normalize_longitude(self.min_lon - delta_lon),
            max_lon=_normalize_longitude(self.max_lon + delta_lon),
        )


@dataclass(frozen=True)
class SigmetItem:
    """Structured SIGMET summary."""

    fir: str | None
    hazard: str | None
    severity: str | None
    valid_from: datetime | None
    valid_to: datetime | None
    raw_text: str | None

    def summary(self) -> str:
        """Return a short human-readable summary of the SIGMET."""

        parts: list[str] = []
        if self.fir:
            parts.append(self.fir)
        if self.hazard:
            parts.append(_hazard_label(self.hazard))
        if self.severity:
            parts.append(self.severity.upper())
        validity = _format_validity(self.valid_from, self.valid_to)
        if validity:
            parts.append(validity)
        return " | ".join(parts) if parts else (self.raw_text or "SIGMET")


class AWCSigmetProvider:
    """Provider for SIGMET information from the Aviation Weather Center."""

    def __init__(self, *, timeout: float | None = None) -> None:
        self._timeout = timeout or 12.0

    async def fetch_sigmet(
        self,
        icaos: Sequence[str],
        *,
        bounds: GeoBounds | None = None,
        firs: Sequence[str] | None = None,
    ) -> list[SigmetItem]:  # pragma: no cover - network interaction
        """Fetch SIGMETs affecting the given region."""

        params = {"datatype": "sigmet"}
        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.get(SIGMET_URL, params=params)
                response.raise_for_status()
        except httpx.TimeoutException as exc:
            raise SigmetServiceError("Таймаут запроса SIGMET") from exc
        except httpx.HTTPStatusError as exc:
            raise SigmetServiceError("Сервис SIGMET вернул ошибку") from exc
        except httpx.RequestError as exc:
            raise SigmetServiceError("Не удалось получить данные SIGMET") from exc

        try:
            payload = response.json()
        except ValueError as exc:
            raise SigmetServiceError("Некорректный JSON от AWC") from exc

        features = payload.get("features", [])
        results: list[SigmetItem] = []
        fir_filter = {fir.upper() for fir in firs} if firs else None
        if not fir_filter:
            fir_filter = _derive_fir_codes(icaos)
        if fir_filter:
            fir_filter = {code.upper() for code in fir_filter}

        for feature in features:
            item = _parse_feature(feature)
            if item is None:
                continue
            if fir_filter and item.fir and item.fir.upper() not in fir_filter:
                continue
            if bounds and not _feature_intersects_bounds(feature, bounds):
                continue
            results.append(item)

        return results


def _parse_feature(feature: Mapping[str, Any]) -> SigmetItem | None:
    properties = feature.get("properties", {})
    if not isinstance(properties, Mapping):
        return None

    hazard = str(properties.get("hazard") or properties.get("phenomenon") or "").upper()
    if hazard not in {"TS", "TURB", "ICE"}:
        return None

    fir = properties.get("fir") or properties.get("firname") or properties.get("name")
    severity = properties.get("severity") or properties.get("intensity")
    raw_text = properties.get("raw_text") or properties.get("rawText")

    valid_from = _parse_time(
        properties.get("validTimeFrom")
        or properties.get("validtimefrom")
        or properties.get("valid_from")
    )
    valid_to = _parse_time(
        properties.get("validTimeTo")
        or properties.get("validtimeto")
        or properties.get("valid_to")
    )

    return SigmetItem(
        fir=str(fir).upper() if fir else None,
        hazard=hazard or None,
        severity=str(severity).upper() if severity else None,
        valid_from=valid_from,
        valid_to=valid_to,
        raw_text=raw_text if isinstance(raw_text, str) else None,
    )


def _feature_intersects_bounds(feature: Mapping[str, Any], bounds: GeoBounds) -> bool:
    geometry = feature.get("geometry")
    if not isinstance(geometry, Mapping):
        return False
    for lat, lon in _iterate_geometry_points(geometry):
        if bounds.contains(lat, lon):
            return True
    return False


def _iterate_geometry_points(geometry: Mapping[str, Any]) -> Iterable[tuple[float, float]]:
    gtype = geometry.get("type")
    coords = geometry.get("coordinates")
    if not coords:
        return []

    if gtype == "Point":
        lon, lat = coords
        return [(float(lat), float(lon))]
    if gtype == "LineString":
        return [(float(lat), float(lon)) for lon, lat in coords]
    if gtype == "Polygon":
        points: list[tuple[float, float]] = []
        for ring in coords:
            points.extend((float(lat), float(lon)) for lon, lat in ring)
        return points
    if gtype == "MultiPolygon":
        points: list[tuple[float, float]] = []
        for polygon in coords:
            for ring in polygon:
                points.extend((float(lat), float(lon)) for lon, lat in ring)
        return points

    return []


def _normalize_longitude(lon: float) -> float:
    result = (lon + 180.0) % 360.0 - 180.0
    return result


def _parse_time(raw: Any) -> datetime | None:
    if not raw:
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        candidate = raw.strip().replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(candidate)
        except ValueError:
            return None
    return None


def _format_validity(start: datetime | None, end: datetime | None) -> str | None:
    if not start and not end:
        return None
    if start and end:
        return f"{start:%d %H%MZ}-{end:%H%MZ}"
    if start:
        return f"с {start:%d %H%MZ}"
    if end:
        return f"до {end:%d %H%MZ}"
    return None


def _hazard_label(hazard: str | None) -> str:
    mapping = {
        "TS": "Грозы",
        "TURB": "Турбулентность",
        "ICE": "Обледенение",
    }
    if not hazard:
        return "SIGMET"
    return mapping.get(hazard.upper(), hazard.upper())


def _derive_fir_codes(icaos: Sequence[str]) -> set[str]:
    prefix_map: Mapping[str, set[str]] = {
        "UU": {"UUWV", "UUMM"},
        "UL": {"ULLL"},
        "UR": {"URRV", "URWW"},
        "US": {"USRR"},
        "UN": {"UNEE", "UNWW"},
        "UW": {"UWWW"},
        "UH": {"UHPP", "UHMM"},
        "UM": {"UMKK"},
    }

    firs: set[str] = set()
    for icao in icaos:
        ident = icao.upper()
        if len(ident) >= 4:
            firs.add(ident[:4])
        prefix = ident[:2]
        firs.update(prefix_map.get(prefix, set()))
    return firs


__all__ = [
    "AWCSigmetProvider",
    "SigmetItem",
    "GeoBounds",
    "SigmetServiceError",
]
