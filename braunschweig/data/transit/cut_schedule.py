"""synpp stage: the cut public-transport timetable, built by the SAME Java cutter prepare uses (#574, R9).

The portal pt gate check must route on the timetable MATSim runs. The scenario cut of ``prepare`` is done by eqasim's
Java ``TransitScheduleCutter``; this stage runs the standalone CLI of that cutter
(``RunTransitScheduleCutter``) on the pre-cut schedule and the cordon extent, so the timetable the synthesis sees and
the timetable of the simulation are one and the same (``docs/superpowers/specs/2026-11-06-pt-cut-consistency-and-hubs-design.md``).

Modes (``braunschweig.cutter.transit_cut_mode``):

* ``extent``: today's cut, a route is kept iff it serves a stop inside the cordon extent; no hubs.
* ``kept-stops``: additionally the configured hub stops (exact stop names) anchor the kept sub-sequence of a route.

Returns ``{schedule_path, hub_stops_path, kept_stop_ids, hub_stop_ids, n_stops_pre_cut, n_stops_kept, n_hub_stops,
mode}``; both paths are relative to the stage directory. Fail-fast, no silent fallback: a configured hub without a row
in the hub table, or a cut schedule without any route, raises ``ValueError``.
"""
from __future__ import annotations

import logging
import os

import pandas as pd

from braunschweig import synpp_tokens
from braunschweig.data.cordon import extent as _extent
from braunschweig.data.spatial import cordon as _cordon
from braunschweig.data.transit import schedule as _schedule
import matsim.runtime.eqasim as eqasim
import matsim.runtime.java as java

logger = logging.getLogger(__name__)

_LOG_TAG = "[cut_schedule]"

KEY_TRANSIT_CUT_MODE = "braunschweig.cutter.transit_cut_mode"
#: Region-neutral default: today's cut by extent only.
DEFAULT_TRANSIT_CUT_MODE = "extent"
MODES = ("extent", "kept-stops")
KEY_HUB_STOP_NAMES = "braunschweig.cutter.transit_hub_stop_names"
DEFAULT_HUB_STOP_NAMES: list = []

CUTTER_CLASS = "org.eqasim.core.scenario.cutter.transit.RunTransitScheduleCutter"
SCHEDULE_FILE_NAME = "cut_schedule.xml.gz"
HUBS_FILE_NAME = "hub_stops.csv"
HUB_NAMES_FILE_NAME = "hub_stop_names.txt"
EXTENT_FILE_NAME = "cordon_extent.gpkg"
HUB_TABLE_COLUMNS = ("name", "stop_id", "x", "y", "inside_extent")

#: Own-package helpers whose source shapes this stage's output (hashed by validate()).
_HELPER_MODULES = (_schedule, _cordon, _extent, eqasim)
#: The schedule reader imports ``plan_check.parse_time_s``; the audit requires its import closure to be hashed too.
_DEFERRED_HELPER_MODULE_NAMES = ("braunschweig.matsim.simulation.plan_check", "braunschweig.popsim.closure_dwell",
                                 "braunschweig.popsim.plan_validation", "data.hts.hts")


def validate_mode(value) -> str:
    """Return ``value`` when it is a known cut mode, else raise ``ValueError``."""
    if value not in MODES:
        raise ValueError(f"{KEY_TRANSIT_CUT_MODE}={value!r} is not one of {MODES}")
    return value


def write_hub_names(path: str, names) -> str:
    """Write the hub stop names, one per line, UTF-8 (a file so umlauts survive the Windows code page)."""
    with open(path, "w", encoding="utf-8", newline="\n") as handle:
        for name in names:
            handle.write(f"{name}\n")
    return path


def read_hub_table(path: str) -> pd.DataFrame:
    """Read the hub table of the Java CLI: ``name, stop_id, x, y, inside_extent`` (``stop_id`` str, flag bool)."""
    # keep_default_na=False: a stop named "NA" or "None" must stay a string, not become NaN; an empty
    # inside_extent cell then reads as "" and is rejected below.
    table = pd.read_csv(path, dtype=str, keep_default_na=False, encoding="utf-8")
    for column in ("x", "y"):
        if column in table.columns:
            table[column] = pd.to_numeric(table[column], errors="raise")
    for column in ("name", "stop_id"):
        if column in table.columns and (table[column] == "").any():
            row = int((table[column] == "").to_numpy().argmax()) + 2  # 1-based file line, header is line 1
            raise ValueError(f"{path}: empty {column} in line {row} of the hub table")
    missing = [column for column in HUB_TABLE_COLUMNS if column not in table.columns]
    if missing:
        raise ValueError(f"{path}: hub table lacks the columns {missing}")
    flags = table["inside_extent"].str.lower()
    unknown = sorted(set(flags) - {"true", "false"})
    if unknown:
        raise ValueError(f"{path}: inside_extent must be true/false, found {unknown}")
    table["inside_extent"] = flags == "true"
    return table[list(HUB_TABLE_COLUMNS)]


def validate(context):
    """synpp validation token over the helper sources (same mechanism as the portal stage)."""
    return synpp_tokens.source_token(_HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES,
                                     stage_label=__name__ + ".validate()")


def configure(context):
    context.stage("data.spatial.municipalities")
    context.stage("matsim.scenario.supply.processed")
    context.stage("matsim.runtime.eqasim")
    context.stage("matsim.runtime.java")
    context.config("cordon_network_buffer_fraction", 0.10)
    # eqasim.run -> java.run reads java_binary, java_memory and the hang-watchdog keys from this stage's context.
    java.configure(context)
    mode = context.config(KEY_TRANSIT_CUT_MODE, DEFAULT_TRANSIT_CUT_MODE)
    hub_names = context.config(KEY_HUB_STOP_NAMES, DEFAULT_HUB_STOP_NAMES)
    validate_mode(mode)  # an explicit YAML null is rejected here, not later in execute
    if hub_names and mode == "extent":
        raise ValueError(f"{KEY_HUB_STOP_NAMES} is set but {KEY_TRANSIT_CUT_MODE} is 'extent': hubs only apply in "
                         "'kept-stops' mode (the two would silently disagree)")


def execute(context):
    mode = validate_mode(context.config(KEY_TRANSIT_CUT_MODE))
    hub_names = list(context.config(KEY_HUB_STOP_NAMES))
    df_muni = context.stage("data.spatial.municipalities")
    polygon = _cordon.cordon_polygon_from_fraction(df_muni, float(context.config("cordon_network_buffer_fraction")))
    stage_dir = context.path()
    # The Java call runs with the stage directory as cwd (java.run), so the stage's own files are given relative.
    _extent.write_cordon_extent(os.path.join(stage_dir, EXTENT_FILE_NAME), polygon, crs=str(df_muni.crs))

    supply = context.stage("matsim.scenario.supply.processed")
    pre_cut_path = os.path.join(context.path("matsim.scenario.supply.processed"), supply["schedule_path"])
    arguments = ["--schedule-path", pre_cut_path, "--extent-path", EXTENT_FILE_NAME]
    if mode == "kept-stops":
        write_hub_names(os.path.join(stage_dir, HUB_NAMES_FILE_NAME), hub_names)
        arguments += ["--hub-stop-names-path", HUB_NAMES_FILE_NAME]
    arguments += ["--output-schedule-path", SCHEDULE_FILE_NAME, "--output-hubs-path", HUBS_FILE_NAME]
    eqasim.run(context, CUTTER_CLASS, arguments)

    hubs = read_hub_table(os.path.join(stage_dir, HUBS_FILE_NAME))
    unmatched = [name for name in hub_names if name not in set(hubs["name"])]
    if unmatched:
        raise ValueError(f"{_LOG_TAG} configured hub stop names without a row in the hub table: {unmatched}")
    pre_cut = _schedule.read_schedule(pre_cut_path)
    cut = _schedule.read_schedule(os.path.join(stage_dir, SCHEDULE_FILE_NAME))
    if not cut.routes:
        raise ValueError(f"{_LOG_TAG} the cut timetable has no route (extent {EXTENT_FILE_NAME}, mode {mode}); "
                         "the cutter or the extent is wrong")
    kept_stop_ids = frozenset(cut.stops)
    hub_stop_ids = frozenset(hubs["stop_id"].astype(str))
    n_pre, n_kept = len(pre_cut.stops), len(kept_stop_ids)
    # A hub facility on no kept route is not in the cut timetable (R2); it is reported, not an error.
    n_hubs_kept = len(hub_stop_ids & kept_stop_ids)
    logger.info("%s kept %d/%d stops (%.1f %%), hubs kept %d/%d facilities (%.1f %%; %s), routes %d/%d (%.1f %%), "
                "mode %s", _LOG_TAG, n_kept, n_pre, 100.0 * n_kept / max(n_pre, 1), n_hubs_kept, len(hub_stop_ids),
                100.0 * n_hubs_kept / max(len(hub_stop_ids), 1), ", ".join(hub_names) or "none", len(cut.routes), len(pre_cut.routes), 100.0 * len(cut.routes) / max(len(pre_cut.routes), 1), mode)
    return {"schedule_path": SCHEDULE_FILE_NAME, "hub_stops_path": HUBS_FILE_NAME, "kept_stop_ids": kept_stop_ids,
            "hub_stop_ids": hub_stop_ids, "n_stops_pre_cut": n_pre, "n_stops_kept": n_kept,
            "n_hub_stops": len(hub_stop_ids), "mode": mode}
