"""Extend a MATSim transit schedule with next-day departures (eqasim-bs#465).

The simulated day is 30 h long, but a GTFS-derived schedule usually ends shortly after midnight, so
late pt trips find no timetable connection and fall back to very long walks. This module copies every
departure that leaves before ``next_day_hours`` (on the first day) once more, shifted by +24 h, onto a
fresh vehicle id (``<id>_d2``) so a vehicle is never in two places at the same time.

Assumptions (explicit):
- The first-day timetable is repeated unchanged on the next day (same lines, routes, vehicle type).
- Departures already at or after ``day_s`` are left alone and are never duplicated again.
- ``next_day_hours == 0`` is the OFF path: both files are copied byte for byte.
"""
import gzip
import logging
import shutil
import xml.etree.ElementTree as ET
from collections import Counter

from braunschweig.matsim.simulation.plan_check import parse_time_s

logger = logging.getLogger(__name__)

NEXT_DAY_VEHICLE_SUFFIX = "_d2"
KEY_NEXT_DAY_HOURS = "braunschweig.transit_schedule.next_day_hours"
DEFAULT_NEXT_DAY_HOURS = 6.0

_VEHICLE_NS = "http://www.matsim.org/files/dtd"

__all__ = [
    "NEXT_DAY_VEHICLE_SUFFIX",
    "KEY_NEXT_DAY_HOURS",
    "DEFAULT_NEXT_DAY_HOURS",
    "parse_time_s",
    "format_time",
    "extend_schedule",
]


def format_time(seconds: float) -> str:
    """Format seconds as ``HH:MM:SS``; hours may exceed 24."""
    total = int(round(seconds))
    return f"{total // 3600:02d}:{(total % 3600) // 60:02d}:{total % 60:02d}"


def _parse(path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as handle:
        return ET.parse(handle)


def _write(tree, path):
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "wb") as handle:
        tree.write(handle, encoding="utf-8", xml_declaration=True)


def _histogram(times_s):
    return dict(sorted(Counter(int(s // 3600) for s in times_s).items()))


def extend_schedule(schedule_in: str, vehicles_in: str, schedule_out: str, vehicles_out: str, *,
                    next_day_hours: float, day_s: float = 86400.0) -> dict:
    """Write schedule and vehicles with first-day departures before ``next_day_hours`` repeated at +``day_s``."""
    if next_day_hours < 0:
        raise ValueError(f"next_day_hours must be >= 0, got {next_day_hours}")
    bound_s = next_day_hours * 3600.0

    schedule_tree = _parse(schedule_in)
    departure_times = [parse_time_s(d.get("departureTime"))
                       for d in schedule_tree.getroot().iter("departure")]
    before = _histogram(departure_times)

    if next_day_hours == 0:
        shutil.copyfile(schedule_in, schedule_out)
        shutil.copyfile(vehicles_in, vehicles_out)
        logger.info("[transit_next_day] next_day_hours=0: schedule and vehicles copied unchanged "
                    "(%d departures)", len(departure_times))
        return {"n_departures": len(departure_times), "n_duplicated": 0, "n_vehicles_added": 0,
                "departures_per_hour_before": before, "departures_per_hour_after": dict(before),
                "max_departure_h": max(departure_times, default=0.0) / 3600.0}

    shifted_times = []
    duplicated_vehicles = []  # insertion-ordered distinct vehicle ids
    for departures in schedule_tree.getroot().iter("departures"):
        originals = list(departures.findall("departure"))
        for dep in originals:
            time_s = parse_time_s(dep.get("departureTime"))
            if time_s >= day_s or time_s >= bound_s:
                continue
            copy = ET.Element("departure", dict(dep.attrib))
            copy.set("id", dep.get("id") + NEXT_DAY_VEHICLE_SUFFIX)
            copy.set("departureTime", format_time(time_s + day_s))
            vehicle = dep.get("vehicleRefId")
            if vehicle is None:
                raise ValueError(f"departure {dep.get('id')} has no vehicleRefId")
            copy.set("vehicleRefId", vehicle + NEXT_DAY_VEHICLE_SUFFIX)
            if vehicle not in duplicated_vehicles:
                duplicated_vehicles.append(vehicle)
            copy.tail = dep.tail
            departures.append(copy)
            shifted_times.append(time_s + day_s)

    ET.register_namespace("", _VEHICLE_NS)
    vehicles_tree = _parse(vehicles_in)
    vroot = vehicles_tree.getroot()
    by_id = {v.get("id"): v for v in vroot if v.tag.endswith("}vehicle") or v.tag == "vehicle"}
    for vehicle in duplicated_vehicles:
        if vehicle not in by_id:
            raise ValueError(f"vehicle {vehicle} referenced by a departure is missing in {vehicles_in}")
        new = ET.Element(by_id[vehicle].tag, dict(by_id[vehicle].attrib))
        new.set("id", vehicle + NEXT_DAY_VEHICLE_SUFFIX)
        vroot.append(new)

    _write(schedule_tree, schedule_out)
    _write(vehicles_tree, vehicles_out)

    after = _histogram(departure_times + shifted_times)
    max_h = max(departure_times + shifted_times, default=0.0) / 3600.0
    logger.info("[transit_next_day] %d departures, %d duplicated (+%d vehicles), max departure %.2f h; "
                "per hour before %s, after %s", len(departure_times), len(shifted_times),
                len(duplicated_vehicles), max_h, before, after)
    return {"n_departures": len(departure_times), "n_duplicated": len(shifted_times),
            "n_vehicles_added": len(duplicated_vehicles), "departures_per_hour_before": before,
            "departures_per_hour_after": after, "max_departure_h": max_h}
