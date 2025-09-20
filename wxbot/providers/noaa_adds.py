"""NOAA ADDS data provider."""

from __future__ import annotations

import asyncio
import logging
import os
import time
from collections import defaultdict
from typing import Any, Mapping, NamedTuple, Sequence

import httpx

LOG = logging.getLogger(__name__)

try:
    TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "20"))
except ValueError:
    LOG.warning("Invalid HTTP_TIMEOUT value, falling back to 20 seconds")
    TIMEOUT = 20.0

BASE_METAR = "https://aviationweather.gov/api/data/metar"
BASE_TAF = "https://aviationweather.gov/api/data/taf"

HEADERS = {
    "User-Agent": "WXBot/0.2 (https://github.com/Evgen-007/bot_DEA; [email protected])",
    "Accept": "application/json",
}

_CACHE_TTL_SECONDS = 90.0
_METAR_HOURS = 6
_TAF_HOURS = 24


class NOAAServiceError(RuntimeError):
    """Raised when the NOAA ADDS service cannot be reached or parsed."""


class _CacheEntry(NamedTuple):
    expires_at: float
    value: Any


_metar_cache: dict[tuple[str, ...], _CacheEntry] = {}
_taf_cache: dict[tuple[str, ...], _CacheEntry] = {}


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

    try:
        payload = await _req(
            BASE_METAR,
            {"ids": ",".join(key), "format": "json", "hours": str(_METAR_HOURS)},
        )
    except Exception as exc:  # pragma: no cover - network path
        raise NOAAServiceError("Не удалось получить METAR/SPECI из AWC") from exc

    raw_entries: Sequence[Any]
    if isinstance(payload, list):
        raw_entries = payload
    elif isinstance(payload, Mapping):
        raw_entries = payload.get("data", [])
    else:
        raw_entries = []

    metar_map: defaultdict[str, list[str]] = defaultdict(list)
    speci_map: defaultdict[str, list[str]] = defaultdict(list)

    for entry in raw_entries:
        if not isinstance(entry, Mapping):
            continue
        raw_text = entry.get("raw") or entry.get("raw_text")
        station_id = (
            entry.get("id")
            or entry.get("stationId")
            or entry.get("station_id")
            or ""
        ).upper()
        if not raw_text or not station_id:
            continue
        report_type = (entry.get("type") or entry.get("report_type") or "").upper()
        target_map = speci_map if report_type == "SPECI" else metar_map
        target_map[station_id].append(raw_text)

    if not metar_map and not speci_map and _METAR_HOURS == 6:
        try:
            payload = await _req(
                BASE_METAR,
                {"ids": ",".join(key), "format": "json", "hours": "12"},
            )
        except Exception:  # pragma: no cover - fallback best-effort path
            pass
        else:
            if isinstance(payload, list):
                fallback_entries: Sequence[Any] = payload
            elif isinstance(payload, Mapping):
                fallback_entries = payload.get("data", [])
            else:
                fallback_entries = []

            for entry in fallback_entries:
                if not isinstance(entry, Mapping):
                    continue
                raw_text = entry.get("raw") or entry.get("raw_text")
                station_id = (
                    entry.get("id")
                    or entry.get("stationId")
                    or entry.get("station_id")
                    or ""
                ).upper()
                if not raw_text or not station_id:
                    continue
                report_type = (entry.get("type") or entry.get("report_type") or "").upper()
                target_map = speci_map if report_type == "SPECI" else metar_map
                target_map[station_id].append(raw_text)

    result = {
        "metar": {icao: list(metar_map.get(icao, [])) for icao in key},
        "speci": {icao: list(speci_map.get(icao, [])) for icao in key},
    }
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

    try:
        payload = await _req(
            BASE_TAF,
            {"ids": ",".join(key), "format": "json", "hours": str(_TAF_HOURS)},
        )
    except Exception as exc:  # pragma: no cover - network path
        raise NOAAServiceError("Не удалось получить TAF из AWC") from exc

    raw_entries: Sequence[Any]
    if isinstance(payload, list):
        raw_entries = payload
    elif isinstance(payload, Mapping):
        raw_entries = payload.get("data", [])
    else:
        raw_entries = []

    taf_map: defaultdict[str, list[str]] = defaultdict(list)
    for entry in raw_entries:
        if not isinstance(entry, Mapping):
            continue
        raw_text = entry.get("raw") or entry.get("raw_text")
        station_id = (
            entry.get("id")
            or entry.get("stationId")
            or entry.get("station_id")
            or ""
        ).upper()
        if not raw_text or not station_id:
            continue
        taf_map[station_id].append(raw_text)

    result = {icao: list(taf_map.get(icao, [])) for icao in key}
    _store_cache_entry(_taf_cache, key, result)
    return _clone_taf_cache_value(result)


async def fetch_metar_taf_speci(
    icaos: Sequence[str],
) -> Mapping[str, Mapping[str, list[str]]]:
    """Fetch METAR, SPECI and TAF in a single structure."""

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


async def _req(url: str, params: Mapping[str, Any]) -> Any:
    """HTTP GET with retries, headers, and logging."""

    attempts = 3
    backoff = 1.6
    exc_last: Exception | None = None
    for i in range(1, attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=TIMEOUT, headers=HEADERS) as client:
                response = await client.get(url, params=params)
                if response.status_code == 204 or not response.content:
                    LOG.info(
                        "AWC %s %s → 204 No Content (ids=%s)",
                        url,
                        params.get("format"),
                        params.get("ids"),
                    )
                    return {"data": []}
                response.raise_for_status()
                try:
                    return response.json()
                except ValueError:
                    LOG.warning("AWC empty/invalid JSON treated as no data: %s", url)
                    return {"data": []}
        except (httpx.TimeoutException, httpx.ConnectError, httpx.RequestError) as exc:
            exc_last = exc
            LOG.warning("AWC GET failed (try %d/%d): %s", i, attempts, exc.__class__.__name__)
        except httpx.HTTPStatusError as exc:
            LOG.error("AWC HTTP %s: %s", exc.response.status_code, exc)
            exc_last = exc
            if 400 <= exc.response.status_code < 500 and i >= 2:
                break
        except Exception as exc:  # pragma: no cover - unexpected error path
            LOG.exception("AWC unexpected: %s", exc)
            exc_last = exc
            break
        if i < attempts:
            await asyncio.sleep(backoff**i)
    raise NOAAServiceError("AWC request failed") from exc_last


__all__ = [
    "NOAAServiceError",
    "fetch_metar",
    "fetch_taf",
    "fetch_metar_taf_speci",
]
