"""GTFS supply stage with next-day departures (eqasim-bs#465).

Overrides ``matsim.scenario.supply.gtfs``: pt2matsim builds the schedule and vehicles as before, then
the first-day departures before ``braunschweig.transit_schedule.next_day_hours`` are repeated at +24 h
(see :mod:`braunschweig.matsim.scenario.transit_next_day`) so late pt trips of the 30 h day find a
connection. The untouched pt2matsim output is kept as ``*_service_day`` files for traceability.
``next_day_hours: 0`` reproduces the pt2matsim output byte for byte.
"""
import logging
import os

import matsim.scenario.supply.gtfs as delegate

from braunschweig import synpp_tokens
from braunschweig.matsim.scenario import transit_next_day
from braunschweig.matsim.scenario.transit_next_day import (
    DEFAULT_NEXT_DAY_HOURS, KEY_NEXT_DAY_HOURS, estimated_service_day, extend_schedule)

logger = logging.getLogger(__name__)

SCHEDULE_FILE = "transit_schedule.xml.gz"
VEHICLES_FILE = "transit_vehicles.xml.gz"
SERVICE_DAY_SCHEDULE_FILE = "transit_schedule_service_day.xml.gz"
SERVICE_DAY_VEHICLES_FILE = "transit_vehicles_service_day.xml.gz"
#: Default of the declared key ``gtfs_date`` (matsim.scenario.supply.gtfs): pt2matsim picks the busiest day.
GTFS_DATE_AUTO = "dayWithMostServices"

#: ``delegate`` runs pt2matsim; ``transit_next_day`` rewrites its output. Both change the result
#: without touching this file (closure-hash rule, ADR-0136).
_HELPER_MODULES = (delegate, transit_next_day)
_DEFERRED_HELPER_MODULE_NAMES = (
    # transit_next_day reads parse_time_s from plan_check, which reads MAX_PLAN_TIME_SECONDS from
    # braunschweig.popsim.plan_validation (closure_dwell, data.hts.hts, population.methods, resources).
    "braunschweig.matsim.simulation.plan_check",
    "braunschweig.popsim.closure_dwell",
    "braunschweig.popsim.plan_validation",
    "braunschweig.population.methods",
    "braunschweig.resources",
    "data.hts.hts",
    # The delegate runs pt2matsim, whose helpers shape the produced schedule.
    "matsim.runtime.git",
    "matsim.runtime.java",
    "matsim.runtime.maven",
    "matsim.runtime.process_watchdog",
    "matsim.runtime.pt2matsim",
)


def configure(context):
    delegate.configure(context)
    context.config(KEY_NEXT_DAY_HOURS, DEFAULT_NEXT_DAY_HOURS)


def validate(context):
    """Digest per helper module, so an edit of the extension (or pt2matsim wrapper) recomputes."""
    return synpp_tokens.source_token(
        _HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES, stage_label=__name__ + ".validate()")


def _estimated_service_day(context):
    """Estimate pt2matsim's service day for the log; never fails the stage (logged when not possible).

    Returns None without an estimate when ``gtfs_date`` pins a date: pt2matsim then uses that date, so the
    ``dayWithMostServices`` rule does not apply (logged).
    """
    pinned = context.config("gtfs_date")
    if pinned not in (None, GTFS_DATE_AUTO):
        logger.info("[supply_gtfs] service day not estimated: gtfs_date pinned to %s", pinned)
        return None
    gtfs_path = "%s/output" % context.path("data.gtfs.cleaned")
    try:
        return estimated_service_day(gtfs_path)
    except (OSError, ValueError, KeyError) as error:
        logger.warning("[supply_gtfs] could not estimate the service day from %s (%s: %s)",
                       gtfs_path, type(error).__name__, error)
        return None


def execute(context):
    hours = float(context.config(KEY_NEXT_DAY_HOURS))
    if hours < 0:
        raise ValueError(f"{KEY_NEXT_DAY_HOURS} must be >= 0, got {hours}")
    result = delegate.execute(context)
    root = context.path()

    service_day_schedule = os.path.join(root, SERVICE_DAY_SCHEDULE_FILE)
    service_day_vehicles = os.path.join(root, SERVICE_DAY_VEHICLES_FILE)
    os.replace(os.path.join(root, result["schedule_path"]), service_day_schedule)
    os.replace(os.path.join(root, result["vehicles_path"]), service_day_vehicles)

    next_day = extend_schedule(
        service_day_schedule, service_day_vehicles,
        os.path.join(root, SCHEDULE_FILE), os.path.join(root, VEHICLES_FILE),
        next_day_hours=hours)

    estimate = _estimated_service_day(context)
    if estimate is not None:
        logger.info("[supply_gtfs] estimated service day (dayWithMostServices rule, computed from the "
                    "cleaned GTFS calendar; pt2matsim's own choice is authoritative): %s with %d services",
                    estimate[0], estimate[1])

    return {
        "schedule_path": SCHEDULE_FILE,
        "vehicles_path": VEHICLES_FILE,
        "service_day_schedule_path": SERVICE_DAY_SCHEDULE_FILE,
        "service_day_vehicles_path": SERVICE_DAY_VEHICLES_FILE,
        "next_day": next_day,
        "estimated_service_day": estimate,
    }
