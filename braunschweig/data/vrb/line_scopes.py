"""Tariff scope and mode class per transit line, derived from the cleaned GTFS.

The Deutschlandticket is valid on all local and regional services and never on long-distance
trains or coaches (DB Fernverkehr, FlixTrain, FlixBus, ...). GTFS extended route types 101
(high-speed rail) and 102 (long-distance rail) and an agency name matching
``fernverkehr|flix`` mark a line as ``long_distance``; everything else is ``regional``
(ADR-0133 D6). Mode classes follow the GTFS route type families. pt2matsim names each MATSim
transit line after the GTFS route id, so ``line_id`` equals ``route_id``.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pandas as pd

log = logging.getLogger(__name__)

COLUMNS = ["line_id", "agency_id", "agency_name", "route_type", "mode_class", "tariff_scope"]
DEFAULT_LONG_DISTANCE_ROUTE_TYPES = frozenset({101, 102})
DEFAULT_LONG_DISTANCE_AGENCY_PATTERN = r"fernverkehr|flix"
SCOPE_REGIONAL = "regional"
SCOPE_LONG_DISTANCE = "long_distance"


def mode_class(route_type: int) -> str:
    """Coarse mode class of a GTFS basic (0-7) or extended (100-1700) route type."""
    if route_type == 0 or 900 <= route_type <= 906:
        return "tram"
    if route_type == 1 or 400 <= route_type <= 405:
        return "subway"
    if route_type == 2 or 100 <= route_type <= 117:
        return "rail"
    if route_type == 3 or 700 <= route_type <= 716:
        return "bus"
    if route_type == 4 or 1000 <= route_type <= 1200:
        return "ferry"
    return "other"


def build_line_scopes(cleaned_output_dir, *, long_distance_route_types=DEFAULT_LONG_DISTANCE_ROUTE_TYPES,
                      long_distance_agency_pattern=DEFAULT_LONG_DISTANCE_AGENCY_PATTERN) -> pd.DataFrame:
    """One row per GTFS route of the cleaned feed; fails early on an unknown agency id."""
    directory = Path(cleaned_output_dir)
    routes = pd.read_csv(directory / "routes.txt", dtype={"route_id": str, "agency_id": str})
    agencies = pd.read_csv(directory / "agency.txt", dtype={"agency_id": str})
    names = dict(zip(agencies["agency_id"], agencies["agency_name"].astype(str)))
    missing = sorted(set(routes["agency_id"].dropna()) - set(names))
    if missing:
        raise ValueError(f"routes.txt references agency_id {missing[0]!r} that is absent from agency.txt")
    pattern = re.compile(long_distance_agency_pattern, re.IGNORECASE)
    rows = []
    for route in routes.itertuples(index=False):
        route_type = int(route.route_type)
        agency_name = names.get(route.agency_id, "")
        long_distance = route_type in long_distance_route_types or bool(pattern.search(agency_name))
        rows.append({"line_id": route.route_id, "agency_id": route.agency_id, "agency_name": agency_name,
                     "route_type": route_type, "mode_class": mode_class(route_type),
                     "tariff_scope": SCOPE_LONG_DISTANCE if long_distance else SCOPE_REGIONAL})
    frame = pd.DataFrame(rows, columns=COLUMNS)
    counts = {f"{scope}/{mode}": int(n) for (scope, mode), n in frame.groupby(["tariff_scope", "mode_class"]).size().items()}
    other = int((frame["mode_class"] == "other").sum())
    log.info("[vrb-fares] line scopes: lines=%d by scope/mode=%s unknown_mode_class=%d (%.2f%%)",
             len(frame), counts, other, 100.0 * other / max(len(frame), 1))
    return frame


def write_line_scopes(frame: pd.DataFrame, path) -> Path:
    path = Path(path)
    frame[COLUMNS].to_csv(path, index=False)
    return path
