"""MATSim transit schedule reader that keeps the departures of every route (#465, WP-C).

``braunschweig.data.cordon.network.read_transit_stops_routes`` returns only stops and
stop sequences. Timetable-aware gate choice also needs WHEN vehicles leave, so this reader
additionally returns the route's stop offsets and its departure times (seconds after
midnight; values >= 86400 are next-day departures and are kept as they are).
"""
from __future__ import annotations

import gzip
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass

import numpy as np

from braunschweig.matsim.simulation.plan_check import parse_time_s

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RouteInfo:
    """One transit route: stop sequence, per-stop offsets and departures at the first stop."""

    line_id: str
    route_id: str
    mode: str
    stop_ids: tuple[str, ...]
    #: Offset of each stop from the route start: departureOffset, else arrivalOffset, else 0.
    departure_offsets_s: tuple[float, ...]
    departures_s: tuple[float, ...]


@dataclass
class Schedule:
    stops: dict[str, tuple[float, float]]
    routes: list[RouteInfo]


def read_schedule(schedule_path: str) -> Schedule:
    """Stream a MATSim transit schedule (XML or XML.gz) into stops and routes with departures.

    Elements are cleared after their end event (and a route after it is stored), so memory is
    reduced, not constant: the emptied element nodes stay attached to their parent, so memory still
    grows slowly with the number of elements in the file, on top of the stops and routes kept.

    A non-first route stop with neither ``departureOffset`` nor ``arrivalOffset`` gets offset 0.0
    (the vehicle is assumed at the route start there); such stops are counted and logged as a rate
    (WARN when any exist). Raises ``FileNotFoundError`` for a missing file and ``ValueError`` when a
    route references a stop that is not declared in ``transitStops``, a ``stopFacility`` lacks ``x`` or
    ``y``, or more than half of all non-first route stops lack both offsets (the offsets are then
    unusable and every served-window check would be wrong).
    """
    opener = gzip.open if str(schedule_path).endswith(".gz") else open
    stops: dict[str, tuple[float, float]] = {}
    routes: list[RouteInfo] = []
    line_id = None
    route_id = mode = None
    stop_ids: list[str] = []
    offsets: list[float] = []
    departures: list[float] = []
    in_route = False
    n_non_first = n_no_offset = 0
    with opener(schedule_path, "rb") as handle:
        for event, elem in ET.iterparse(handle, events=("start", "end")):
            tag = elem.tag.split("}")[-1]
            if event == "start":
                if tag == "transitLine":
                    line_id = elem.get("id")
                elif tag == "transitRoute":
                    in_route = True
                    route_id = elem.get("id")
                    mode = None
                    stop_ids, offsets, departures = [], [], []
                continue
            if tag == "stopFacility":
                if elem.get("x") is None or elem.get("y") is None:
                    raise ValueError(f"{schedule_path}: stopFacility {elem.get('id')!r} has no x/y coordinates")
                stops[elem.get("id")] = (float(elem.get("x")), float(elem.get("y")))
                elem.clear()
            elif not in_route:
                continue
            elif tag == "transportMode":
                mode = (elem.text or "").strip()
            elif tag == "stop" and elem.get("refId"):
                offset = elem.get("departureOffset") or elem.get("arrivalOffset")
                if stop_ids:  # the first stop of a route legitimately carries no offset
                    n_non_first += 1
                    n_no_offset += 0 if offset else 1
                stop_ids.append(elem.get("refId"))
                offsets.append(parse_time_s(offset) if offset else 0.0)
                elem.clear()
            elif tag == "departure" and elem.get("departureTime"):
                departures.append(parse_time_s(elem.get("departureTime")))
                elem.clear()
            elif tag == "transitRoute":
                routes.append(RouteInfo(line_id, route_id, mode, tuple(stop_ids),
                                        tuple(offsets), tuple(departures)))
                in_route = False
                elem.clear()

    if n_no_offset:
        message = (f"{schedule_path}: {n_no_offset}/{n_non_first} non-first route stops "
                   f"({100.0 * n_no_offset / n_non_first:.1f}%) have neither departureOffset nor arrivalOffset "
                   "and were given offset 0.0")
        if 2 * n_no_offset > n_non_first:
            raise ValueError(f"{message}; more than half of the stop offsets are missing, the schedule's "
                             "offset information is unusable")
        logger.warning("[schedule] %s", message)

    unknown = sorted({s for r in routes for s in r.stop_ids if s not in stops})
    if unknown:
        raise ValueError(f"{schedule_path}: routes reference stops missing from transitStops: "
                         f"{unknown[:10]}{' ...' if len(unknown) > 10 else ''}")
    return Schedule(stops=stops, routes=routes)


def departures_at_stop(schedule: Schedule, stop_id: str,
                       route_keys: set[tuple[str, str]] | None = None) -> np.ndarray:
    """Sorted departure times (s) of all vehicles at ``stop_id``.

    A route serving the stop contributes ``departure + offset of the stop``; a stop visited
    twice by one route contributes both times. ``route_keys`` restricts to ``(line_id, route_id)``.
    """
    times: list[float] = []
    for route in schedule.routes:
        if route_keys is not None and (route.line_id, route.route_id) not in route_keys:
            continue
        for sid, offset in zip(route.stop_ids, route.departure_offsets_s):
            if sid == stop_id:
                times.extend(d + offset for d in route.departures_s)
    return np.sort(np.asarray(times, dtype=float))
