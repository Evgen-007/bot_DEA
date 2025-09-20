"""NOAA ADDS data provider."""

from __future__ import annotations

import os
import asyncio
import time
from collections import defaultdict
from functools import lru_cache
from typing import Any, Mapping, NamedTuple, Sequence

import httpx
from pydantic import BaseModel, Field, ValidationError

BASE_URL = "https://aviationweather.gov/adds/dataserver_current/httpparam"

_CACHE_TTL_SECONDS = 90.0
_RATE_LIMIT_INTERVAL = 1.0
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1.0


class NOAAServiceError(RuntimeError):
    """Raised when the NOAA ADDS service cannot be reached or parsed."""


class NOAASettings(BaseModel):
    """Configuration values for NOAA ADDS requests."""

    base_url: str = Field(default=BASE_URL)
    metar_hours: int = Field(default=6, ge=1, le=12)
    taf_hours: int = Field(default=24, ge=1, le=48)
    timeout: float = Field(default=12.0, gt=0.0)


@lru_cache(maxsize=1)
def _load_settings() -> NOAASettings:
    """Load provider settings from environment variables."""

    data: dict[str, Any] = {}
    timeout_raw = os.getenv("HTTP_TIMEOUT")
    if timeout_raw:
        try:
            data["timeout"] = float(timeout_raw)
        except ValueError as exc:  # pragma: no cover - configuration error path
            raise NOAAServiceError("Некорректное значение HTTP_TIMEOUT") from exc
    try:
        return NOAASettings(**data)
    except ValidationError as exc:  # pragma: no cover - configuration error path
        raise NOAAServiceError("Ошибка конфигурации NOAA ADDS") from exc


class _CacheEntry(NamedTuple):
    expires_at: float
    value: Any


_metar_cache: dict[tuple[str, ...], _CacheEntry] = {}
_taf_cache: dict[tuple[str, ...], _CacheEntry] = {}
_rate_lock = asyncio.Lock()
_last_request_time: float = 0.0


def _normalize_icaos(icaos: Sequence[str]) -> tuple[str, ...]:
    return tuple(sorted({icao.upper() for icao in icaos}))


def _get_cache_entry(cache: dict[tuple[str, ...], _CacheEntry], key: tuple[str, ...]) -> Any | None:
    entry = cache.get(key)
    if not entry:
        return None
    if entry.expires_at < time.monotonic():
        cache.pop(key, None)
        return None
    return entry.value


def _store_cache_entry(
    cache: dict[tuple[str, ...], _CacheEntry],
    key: tuple[str, ...],
    value: Any,
) -> None:
    cache[key] = _CacheEntry(time.monotonic() + _CACHE_TTL_SECONDS, value)


def _clone_metar_cache_value(value: Mapping[str, Mapping[str, list[str]]]) -> Mapping[str, Mapping[str, list[str]]]:
    return {
        "metar": {icao: list(reports) for icao, reports in value.get("metar", {}).items()},
        "speci": {icao: list(reports) for icao, reports in value.get("speci", {}).items()},
    }


def _clone_taf_cache_value(value: Mapping[str, list[str]]) -> Mapping[str, list[str]]:
    return {icao: list(reports) for icao, reports in value.items()}


async def fetch_metar(icaos: Sequence[str]) -> Mapping[str, Mapping[str, list[str]]]:
    """Fetch METAR and SPECI reports for the given ICAO identifiers."""

    if not icaos:
        return {"metar": {}, "speci": {}}

    key = _normalize_icaos(icaos)
    cached = _get_cache_entry(_metar_cache, key)
    if cached is not None:
        return _clone_metar_cache_value(cached)

    params = {
        "dataSource": "metars",
        "requestType": "retrieve",
        "format": "JSON",
        "stationString": " ".join(icaos),
        "hoursBeforeNow": str(_load_settings().metar_hours),
    }
    payload = await _call_noaa(params)
    reports = _extract_reports(payload, "METAR")

    metar_map: defaultdict[str, list[str]] = defaultdict(list)
    speci_map: defaultdict[str, list[str]] = defaultdict(list)

    for entry in reports:
        raw_text = entry.get("raw_text")
        station_id = (entry.get("station_id") or "").upper()
        if not raw_text or not station_id:
            continue
        report_type = (entry.get("report_type") or "").upper()
        if report_type == "SPECI":
            speci_map[station_id].append(raw_text)
        else:
            metar_map[station_id].append(raw_text)

    result = {"metar": dict(metar_map), "speci": dict(speci_map)}
    _store_cache_entry(_metar_cache, key, result)
    return _clone_metar_cache_value(result)


async def fetch_taf(icaos: Sequence[str]) -> Mapping[str, list[str]]:
    """Fetch TAF reports for the given ICAO identifiers."""

    if not icaos:
        return {}

    key = _normalize_icaos(icaos)
    cached = _get_cache_entry(_taf_cache, key)
    if cached is not None:
        return _clone_taf_cache_value(cached)

    params = {
        "dataSource": "tafs",
        "requestType": "retrieve",
        "format": "JSON",
        "stationString": " ".join(icaos),
        "hoursBeforeNow": str(_load_settings().taf_hours),
    }
    payload = await _call_noaa(params)
    reports = _extract_reports(payload, "TAF")

    taf_map: defaultdict[str, list[str]] = defaultdict(list)
    for entry in reports:
        raw_text = entry.get("raw_text")
        station_id = (entry.get("station_id") or "").upper()
        if not raw_text or not station_id:
            continue
        taf_map[station_id].append(raw_text)

    result = dict(taf_map)
    _store_cache_entry(_taf_cache, key, result)
    return _clone_taf_cache_value(result)


async def fetch_metar_taf_speci(
    icaos: Sequence[str],
) -> Mapping[str, Mapping[str, list[str]]]:
    """Fetch METAR, SPECI and TAF in a single structure."""

    settings = _load_settings()  # ensure configuration validation even if cached
    _ = settings

    metar_speci = await fetch_metar(icaos)
    tafs = await fetch_taf(icaos)

    bundle: dict[str, dict[str, list[str]]] = {}
    for icao in icaos:
        bundle[icao] = {
            "metar": list(metar_speci["metar"].get(icao, [])),
            "speci": list(metar_speci["speci"].get(icao, [])),
            "taf": list(tafs.get(icao, [])),
        }
    return bundle


async def _call_noaa(params: Mapping[str, str]) -> Mapping[str, Any]:
    """Perform a request to the NOAA ADDS endpoint."""

    settings = _load_settings()
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        await _respect_rate_limit()
        try:
            async with httpx.AsyncClient(timeout=settings.timeout) as client:
                response = await client.get(settings.base_url, params=params)
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError as exc:
                    if attempt == _MAX_ATTEMPTS:
                        raise NOAAServiceError("Некорректный JSON от NOAA ADDS") from exc
        except httpx.TimeoutException as exc:
            if attempt == _MAX_ATTEMPTS:
                raise NOAAServiceError("Таймаут запроса к NOAA ADDS") from exc
        except httpx.HTTPStatusError as exc:
            if attempt == _MAX_ATTEMPTS:
                raise NOAAServiceError("Ответ NOAA ADDS содержит ошибку") from exc
        except httpx.RequestError as exc:
            if attempt == _MAX_ATTEMPTS:
                raise NOAAServiceError("Не удалось подключиться к NOAA ADDS") from exc

        if attempt < _MAX_ATTEMPTS:
            backoff = _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1))
            await asyncio.sleep(backoff)

    raise NOAAServiceError("NOAA ADDS: исчерпаны попытки запроса")


async def _respect_rate_limit() -> None:
    """Ensure a minimal delay between consecutive NOAA requests."""

    global _last_request_time
    async with _rate_lock:
        now = time.monotonic()
        wait_time = _RATE_LIMIT_INTERVAL - (now - _last_request_time)
        if wait_time > 0:
            await asyncio.sleep(wait_time)
        _last_request_time = time.monotonic()


def _extract_reports(payload: Mapping[str, Any], key: str) -> list[Mapping[str, Any]]:
    """Extract report list from NOAA JSON payload."""

    response = payload.get("response", {})
    data = response.get("data", {})
    reports = data.get(key, [])
    if isinstance(reports, list):
        return [entry for entry in reports if isinstance(entry, dict)]
    return []


__all__ = [
    "NOAAServiceError",
    "fetch_metar",
    "fetch_taf",
    "fetch_metar_taf_speci",
]
