"""Route corridor utilities for WX Bot."""

from __future__ import annotations

import html
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Sequence

from providers.awc_sigmet import AWCSigmetProvider, GeoBounds, SigmetServiceError
from providers.noaa_adds import fetch_metar_taf_speci
from providers.ourairports_ru import AirportRecord, get_airport, iter_airports

from .formatter import StationConditions, evaluate_station

EARTH_RADIUS_KM = 6371.0
CORRIDOR_WIDTH_KM = 80.0
CATEGORY_ORDER = {"VFR": 1, "MVFR": 2, "IFR": 3, "LIFR": 4}

_SIGMET_PROVIDER = AWCSigmetProvider()


@dataclass(frozen=True)
class RoutePoint:
    icao: str
    airport: AirportRecord


@dataclass(frozen=True)
class SegmentStation:
    airport: AirportRecord
    projection: float
    distance_km: float


@dataclass
class SegmentReport:
    start: RoutePoint
    end: RoutePoint
    distance_km: float
    stations: list[SegmentStation]
    severity: str | None


async def build_route_message(icaos: Sequence[str]) -> str:
    """Return a formatted HTML summary for the requested route."""

    if len(icaos) < 2:
        raise ValueError("Маршрут должен включать минимум две точки")

    route_points = [_resolve_point(code) for code in icaos]
    origin, destination = route_points[0], route_points[1]
    alternates = route_points[2:]

    segment_reports, all_airports = _build_segments(route_points)

    station_ids = sorted({airport.ident for airport in all_airports})
    weather_bundle = await fetch_metar_taf_speci(station_ids)
    now = datetime.now(timezone.utc)
    conditions = {
        icao: evaluate_station(weather_bundle.get(icao, {}).get("metar", []), now)
        for icao in station_ids
    }

    _apply_segment_severity(segment_reports, conditions)

    lines: list[str] = []
    header = f"<b>Маршрут: {origin.icao} → {destination.icao}</b>"
    if alternates:
        header += f" (ALTN: {', '.join(point.icao for point in alternates)})"
    lines.append(header)
    lines.append(f"Коридор: ±{int(CORRIDOR_WIDTH_KM)} км")
    lines.append("")

    lines.append("<b>Участки</b>:")
    for segment in segment_reports:
        severity = segment.severity or "нет данных"
        segment_title = (
            f"• {segment.start.icao}→{segment.end.icao} "
            f"(~{segment.distance_km:.0f} км) — {severity}"
        )
        lines.append(segment_title)
        station_summary = _format_segment_stations(segment, conditions)
        if station_summary:
            lines.append(f"  {station_summary}")
        else:
            lines.append("  Станции не найдены в коридоре")

    sigmet_lines = await _build_sigmet_section(all_airports)
    if sigmet_lines:
        lines.append("")
        lines.extend(sigmet_lines)

    return "\n".join(lines)


def _resolve_point(icao: str) -> RoutePoint:
    airport = get_airport(icao)
    if airport is None:
        raise ValueError(f"Аэродром {icao} не найден в справочнике")
    if airport.latitude_deg is None or airport.longitude_deg is None:
        raise ValueError(f"Отсутствуют координаты для {icao}")
    return RoutePoint(icao=icao.upper(), airport=airport)


def _build_segments(points: Sequence[RoutePoint]) -> tuple[list[SegmentReport], list[AirportRecord]]:
    reports: list[SegmentReport] = []
    used_airports: dict[str, AirportRecord] = {point.airport.ident: point.airport for point in points}

    for start, end in zip(points, points[1:]):
        segment_stations = _collect_segment_stations(start.airport, end.airport)
        for station in segment_stations:
            used_airports[station.airport.ident] = station.airport
        distance_km = _haversine_distance(start.airport, end.airport)
        reports.append(
            SegmentReport(
                start=start,
                end=end,
                distance_km=distance_km,
                stations=segment_stations,
                severity=None,
            )
        )

    return reports, list(used_airports.values())


def _collect_segment_stations(start: AirportRecord, end: AirportRecord) -> list[SegmentStation]:
    ref_lat = math.radians((start.latitude_deg + end.latitude_deg) / 2)
    ref_lon = math.radians((start.longitude_deg + end.longitude_deg) / 2)
    start_xy = _to_xy(start, ref_lat, ref_lon)
    end_xy = _to_xy(end, ref_lat, ref_lon)

    stations: dict[str, SegmentStation] = {}

    def add_station(record: AirportRecord, projection: float, distance: float) -> None:
        ident = record.ident.upper()
        current = stations.get(ident)
        candidate = SegmentStation(record, projection, distance)
        if current is None or candidate.distance_km < current.distance_km:
            stations[ident] = candidate

    add_station(start, 0.0, 0.0)
    add_station(end, 1.0, 0.0)

    for airport in iter_airports():
        if airport.ident in {start.ident, end.ident}:
            continue
        if airport.latitude_deg is None or airport.longitude_deg is None:
            continue
        distance, projection = _distance_to_segment(
            start_xy,
            end_xy,
            _to_xy(airport, ref_lat, ref_lon),
        )
        if distance <= CORRIDOR_WIDTH_KM:
            add_station(airport, projection, distance)

    return sorted(stations.values(), key=lambda item: item.projection)


def _to_xy(airport: AirportRecord, ref_lat: float, ref_lon: float) -> tuple[float, float]:
    lat_rad = math.radians(airport.latitude_deg)
    lon_rad = math.radians(airport.longitude_deg)
    x = EARTH_RADIUS_KM * (lon_rad - ref_lon) * math.cos(ref_lat)
    y = EARTH_RADIUS_KM * (lat_rad - ref_lat)
    return x, y


def _distance_to_segment(
    start_xy: tuple[float, float],
    end_xy: tuple[float, float],
    point_xy: tuple[float, float],
) -> tuple[float, float]:
    ax, ay = start_xy
    bx, by = end_xy
    px, py = point_xy

    dx = bx - ax
    dy = by - ay
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq == 0.0:
        distance = math.hypot(px - ax, py - ay)
        return distance, 0.0

    t = ((px - ax) * dx + (py - ay) * dy) / seg_len_sq
    t_clamped = max(0.0, min(1.0, t))
    nearest_x = ax + t_clamped * dx
    nearest_y = ay + t_clamped * dy
    distance = math.hypot(px - nearest_x, py - nearest_y)
    return distance, t_clamped


def _haversine_distance(a: AirportRecord, b: AirportRecord) -> float:
    lat1 = math.radians(a.latitude_deg)
    lat2 = math.radians(b.latitude_deg)
    dlat = lat2 - lat1
    dlon = math.radians(b.longitude_deg - a.longitude_deg)
    sin_dlat = math.sin(dlat / 2)
    sin_dlon = math.sin(dlon / 2)
    hav = sin_dlat * sin_dlat + math.cos(lat1) * math.cos(lat2) * sin_dlon * sin_dlon
    return 2 * EARTH_RADIUS_KM * math.asin(min(1.0, math.sqrt(hav)))


def _apply_segment_severity(
    segments: Iterable[SegmentReport],
    conditions: dict[str, StationConditions],
) -> None:
    for segment in segments:
        categories = []
        for station in segment.stations:
            station_conditions = conditions.get(station.airport.ident, StationConditions(None, None))
            categories.append(station_conditions.category)
        segment.severity = _worst_category(categories)


def _worst_category(categories: Iterable[str | None]) -> str | None:
    worst: tuple[int, str] | None = None
    for category in categories:
        if category is None:
            continue
        rank = CATEGORY_ORDER.get(category, 0)
        candidate = (rank, category)
        if worst is None or candidate[0] > worst[0]:
            worst = candidate
    return worst[1] if worst else None


def _format_segment_stations(
    segment: SegmentReport,
    conditions: dict[str, StationConditions],
) -> str:
    entries: list[str] = []
    for station in segment.stations:
        ident = station.airport.ident
        condition = conditions.get(ident)
        if condition and condition.category:
            entries.append(f"{ident}[{condition.category}]")
        else:
            entries.append(f"{ident}[нет METAR]")
    return ", ".join(entries)


async def _build_sigmet_section(airports: Iterable[AirportRecord]) -> list[str]:
    airport_map: dict[str, AirportRecord] = {airport.ident: airport for airport in airports}
    airport_list = list(airport_map.values())
    if not airport_list:
        return []

    min_lat = min(airport.latitude_deg for airport in airport_list)
    max_lat = max(airport.latitude_deg for airport in airport_list)
    min_lon = min(airport.longitude_deg for airport in airport_list)
    max_lon = max(airport.longitude_deg for airport in airport_list)
    avg_lat = sum(airport.latitude_deg for airport in airport_list) / len(airport_list)

    lat_margin = CORRIDOR_WIDTH_KM / 111.0
    lon_margin = CORRIDOR_WIDTH_KM / max(10.0, 111.0 * math.cos(math.radians(avg_lat)))

    bounds = GeoBounds(min_lat, max_lat, min_lon, max_lon).expand(lat_margin, lon_margin)

    try:
        sigmets = await _SIGMET_PROVIDER.fetch_sigmet(
            [airport.ident for airport in airport_list],
            bounds=bounds,
        )
    except SigmetServiceError:
        return ["SIGMET: источник недоступен"]

    if not sigmets:
        return ["SIGMET: активных сообщений не обнаружено"]

    lines = ["<b>SIGMET</b>:"]
    for item in sigmets:
        summary = html.escape(item.summary())
        details = html.escape(item.raw_text) if item.raw_text else ""
        if details:
            lines.append(f"• {summary} — {details}")
        else:
            lines.append(f"• {summary}")
    return lines


__all__ = ["build_route_message", "CORRIDOR_WIDTH_KM"]
