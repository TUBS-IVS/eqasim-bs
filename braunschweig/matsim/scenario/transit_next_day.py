"""Extend a MATSim transit schedule with next-day departures (eqasim-bs#465).

The simulated day is 30 h long, but a GTFS-derived schedule usually ends shortly after midnight, so
late pt trips find no timetable connection and fall back to very long walks. This module copies every
departure that leaves before ``next_day_hours`` (on the first day) once more, shifted by +24 h, onto a
fresh vehicle id (``<id>_d2``) so a vehicle is never in two places at the same time.

Assumptions (explicit):
- The first-day timetable is repeated unchanged on the next day (same lines, routes, vehicle type).
- Departures already at or after ``day_s`` are left alone and are never duplicated again.
- The interval is half-open: a departure at exactly ``next_day_hours`` h is NOT duplicated.
- ElementTree drops the DOCTYPE line, but MATSim's readers (and pt2matsim's PublicTransitMapper, which reads this
  file in matsim.scenario.supply.processed) select their format delegate from it: the DOCTYPE of the input is
  captured and re-inserted right after the XML declaration of the output (schedule and vehicles).
- ``next_day_hours == 0`` is the OFF path: both files are copied byte for byte.
"""
import copy
import csv
import datetime
import gzip
import io
import logging
import os
import re
import shutil
import xml.etree.ElementTree as ET
from collections import Counter

from braunschweig.matsim.simulation.plan_check import parse_time_s

logger = logging.getLogger(__name__)

NEXT_DAY_VEHICLE_SUFFIX = "_d2"
KEY_NEXT_DAY_HOURS = "braunschweig.transit_schedule.next_day_hours"
DEFAULT_NEXT_DAY_HOURS = 6.0

_VEHICLE_NS = "http://www.matsim.org/files/dtd"
_XSI_NS = "http://www.w3.org/2001/XMLSchema-instance"

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


_DOCTYPE = re.compile(rb"<!DOCTYPE[^>\[]*(?:\[[^\]]*\])?\s*>")


def _parse(path):
    """Parse a (gzipped) XML file; return ``(tree, DOCTYPE bytes or None)`` (ElementTree drops the DOCTYPE)."""
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "rb") as handle:
        raw = handle.read()
    # The DOCTYPE is only looked for before the root element. Comments are blanked out (same length) before
    # locating the root so that tag-like text inside a prolog comment (e.g. "<by>") cannot end the window early.
    masked = re.sub(rb"<!--.*?-->", lambda m: b" " * len(m.group(0)), raw, flags=re.S)
    root_start = re.search(rb"<[A-Za-z]", masked)
    match = _DOCTYPE.search(raw[:root_start.start()] if root_start else raw)
    return ET.ElementTree(ET.fromstring(raw)), (match.group(0) if match else None)


def _write(tree, doctype, path):
    """Write the tree; a captured DOCTYPE goes on its own line right after the XML declaration."""
    buffer = io.BytesIO()
    tree.write(buffer, encoding="utf-8", xml_declaration=True)
    data = buffer.getvalue()
    if doctype is not None:
        declaration_end = data.index(b"?>") + 2
        data = data[:declaration_end] + b"\n" + doctype + data[declaration_end:]
    opener = gzip.open if str(path).endswith(".gz") else open
    with opener(path, "wb") as handle:
        handle.write(data)


def _histogram(times_s):
    return dict(sorted(Counter(int(s // 3600) for s in times_s).items()))


def _log_summary(n_dup, n_total, hours, n_vehicles, before, after):
    share = 100.0 * n_dup / n_total if n_total else 0.0
    logger.info("[transit_next_day] departures duplicated %d/%d (%.1f %%) up to %g h, vehicles added %d",
                n_dup, n_total, share, hours, n_vehicles)
    logger.info("[transit_next_day] departures per hour before %s, after %s", before, after)


def extend_schedule(schedule_in: str, vehicles_in: str, schedule_out: str, vehicles_out: str, *,
                    next_day_hours: float, day_s: float = 86400.0) -> dict:
    """Write schedule and vehicles with first-day departures before ``next_day_hours`` repeated at +``day_s``."""
    if next_day_hours < 0:
        raise ValueError(f"next_day_hours must be >= 0, got {next_day_hours}")
    bound_s = next_day_hours * 3600.0

    schedule_tree, schedule_doctype = _parse(schedule_in)
    departure_times = [parse_time_s(d.get("departureTime"))
                       for d in schedule_tree.getroot().iter("departure")]
    before = _histogram(departure_times)

    if next_day_hours == 0:
        shutil.copyfile(schedule_in, schedule_out)
        shutil.copyfile(vehicles_in, vehicles_out)
        _log_summary(0, len(departure_times), next_day_hours, 0, before, before)
        return {"n_departures": len(departure_times), "n_duplicated": 0, "n_vehicles_added": 0,
                "departures_per_hour_before": before, "departures_per_hour_after": dict(before),
                "max_departure_h": max(departure_times, default=0.0) / 3600.0,
                "next_day_hours": 0.0}

    existing_departure_ids = {d.get("id") for d in schedule_tree.getroot().iter("departure")}
    shifted_times = []
    duplicated_vehicles = []  # insertion-ordered distinct vehicle ids
    for departures in schedule_tree.getroot().iter("departures"):
        originals = list(departures.findall("departure"))
        for dep in originals:
            time_s = parse_time_s(dep.get("departureTime"))
            if time_s >= day_s or time_s >= bound_s:
                continue
            new_id = dep.get("id") + NEXT_DAY_VEHICLE_SUFFIX
            if new_id in existing_departure_ids:
                raise ValueError(f"generated departure id {new_id} already exists in {schedule_in}")
            clone = ET.Element("departure", dict(dep.attrib))
            clone.set("id", new_id)
            clone.set("departureTime", format_time(time_s + day_s))
            vehicle = dep.get("vehicleRefId")
            if vehicle is None:
                raise ValueError(f"departure {dep.get('id')} has no vehicleRefId")
            clone.set("vehicleRefId", vehicle + NEXT_DAY_VEHICLE_SUFFIX)
            if vehicle not in duplicated_vehicles:
                duplicated_vehicles.append(vehicle)
            clone.tail = dep.tail
            departures.append(clone)
            shifted_times.append(time_s + day_s)

    ET.register_namespace("", _VEHICLE_NS)
    ET.register_namespace("xsi", _XSI_NS)
    vehicles_tree, vehicles_doctype = _parse(vehicles_in)
    vroot = vehicles_tree.getroot()
    by_id = {v.get("id"): v for v in vroot if v.tag.endswith("}vehicle") or v.tag == "vehicle"}
    for vehicle in duplicated_vehicles:
        if vehicle not in by_id:
            raise ValueError(f"vehicle {vehicle} referenced by a departure is missing in {vehicles_in}")
        new_id = vehicle + NEXT_DAY_VEHICLE_SUFFIX
        if new_id in by_id:
            raise ValueError(f"generated vehicle id {new_id} already exists in {vehicles_in}")
        new = copy.deepcopy(by_id[vehicle])
        new.set("id", new_id)
        new.tail = by_id[vehicle].tail
        vroot.append(new)

    _write(schedule_tree, schedule_doctype, schedule_out)
    _write(vehicles_tree, vehicles_doctype, vehicles_out)

    after = _histogram(departure_times + shifted_times)
    max_h = max(departure_times + shifted_times, default=0.0) / 3600.0
    _log_summary(len(shifted_times), len(departure_times), next_day_hours, len(duplicated_vehicles),
                 before, after)
    if not shifted_times:
        # Fallback transparency: the extension is configured on but found nothing to repeat.
        logger.warning("[transit_next_day] next_day_hours is %g but 0 of %d departures were duplicated: the "
                       "schedule gets no next-day service (no departure before that hour on the first day?)",
                       next_day_hours, len(departure_times))
    return {"n_departures": len(departure_times), "n_duplicated": len(shifted_times),
            "n_vehicles_added": len(duplicated_vehicles), "departures_per_hour_before": before,
            "departures_per_hour_after": after, "max_departure_h": max_h,
            "next_day_hours": next_day_hours}


_WEEKDAY_COLUMNS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _read_csv(path):
    with open(path, newline="", encoding="utf-8-sig") as handle:
        return list(csv.DictReader(handle))


def estimated_service_day(gtfs_path: str) -> tuple:
    """Return ``(ISO date, number of active services)`` of the date with the most services.

    Re-implements the ``dayWithMostServices`` rule of pt2matsim from the GTFS calendar for the
    log only (pt2matsim's own choice stays authoritative): services of ``calendar.txt`` count on
    the weekdays flagged between ``start_date`` and ``end_date``; ``calendar_dates.txt`` adds
    (exception type 1) or removes (type 2) a service on one date. Ties go to the earliest date.
    Raises FileNotFoundError when neither file exists and ValueError when no service is active.
    """
    calendar_path = os.path.join(gtfs_path, "calendar.txt")
    dates_path = os.path.join(gtfs_path, "calendar_dates.txt")
    if not os.path.exists(calendar_path) and not os.path.exists(dates_path):
        raise FileNotFoundError(f"neither calendar.txt nor calendar_dates.txt in {gtfs_path}")
    active = {}  # date -> set of service ids
    if os.path.exists(calendar_path):
        for row in _read_csv(calendar_path):
            day = datetime.datetime.strptime(row["start_date"], "%Y%m%d").date()
            end = datetime.datetime.strptime(row["end_date"], "%Y%m%d").date()
            while day <= end:
                if row[_WEEKDAY_COLUMNS[day.weekday()]].strip() == "1":
                    active.setdefault(day, set()).add(row["service_id"])
                day += datetime.timedelta(days=1)
    if os.path.exists(dates_path):
        for row in _read_csv(dates_path):
            day = datetime.datetime.strptime(row["date"], "%Y%m%d").date()
            if row["exception_type"].strip() == "1":
                active.setdefault(day, set()).add(row["service_id"])
            elif row["exception_type"].strip() == "2":
                active.setdefault(day, set()).discard(row["service_id"])
    counts = {day: len(services) for day, services in active.items() if services}
    if not counts:
        raise ValueError(f"no active service on any date in {gtfs_path}")
    best = min(counts, key=lambda day: (-counts[day], day))
    return best.isoformat(), counts[best]
