"""synpp stage: the emissions view of the run archive and its pre-check (post-run emissions, #688).

Reads the MATSim run archive ``<output_path>/matsim_output`` (read-only, ADR-0101) and the HBEFA
key index of ``braunschweig.data.hbefa``, and writes into its own stage directory:

* ``view/``: the folder ``AirPollutionAnalysis`` loads (``braunschweig.emissions.view``): vehicle
  types with their HBEFA keys (``braunschweig.emissions.vehicle_types``), every link with its
  HBEFA road type (``braunschweig.emissions.road_types``), the config with the ``emissions``
  module, events and schedule linked;
* ``emissions_inputs_report.json``: what was mapped, replaced and excluded, the road-type
  fallback, and per vehicle type the table level that answers its lookups
  (``braunschweig.emissions.table_keys``).

The stage stops BEFORE the Java run when the tool would not find a factor, when more link length
than allowed has no HBEFA road class, or when more fleet types than allowed are answered below
the detailed table. Both bounds default to 0: a fallback has to be accepted on purpose.

Config keys (all ``emissions_*``): the computation method has NO default while the choice between
``StopAndGoFraction`` and ``AverageSpeed`` is open (issue #688), so a run names the method it
used. The lookup behaviour defaults to the full order (detailed, technology average, average
table), which the owner assumptions need: ``default_car`` and ``heavy_truck`` are average types.

A missing archive raises: the emissions stages are requested explicitly, so there is nothing to
skip to. The ``validate()`` token holds the archive's identity (``ARCHIVE_INFO.json`` and the
size of the events file), so a new simulation under the same ``output_path`` re-runs the chain.
In an invocation that also runs the simulation the archive does not exist at validation time;
the next invocation then re-runs the chain once.

Returns ``{"view_path", "report_path", "archive", "lookups", "settings", "tables"}``; both
paths are relative to the stage directory.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from pathlib import Path

from braunschweig import synpp_tokens
from braunschweig.analysis import matsim_archive as _matsim_archive
from braunschweig.emissions import road_types as _road_types
from braunschweig.emissions import table_keys as _table_keys
from braunschweig.emissions import vehicle_types as _vehicle_types
from braunschweig.emissions import view as _view

logger = logging.getLogger(__name__)

_LOG_TAG = "[emissions.inputs]"

# Ordering only (ADR-0177): execute after the archive is written when the simulation is part of
# the same invocation; an emissions-only invocation does not pull the simulation in.
_ORDER_AFTER_STAGES = (_matsim_archive.ARCHIVE_PRODUCER_STAGE,)

HBEFA_STAGE = "braunschweig.data.hbefa"
VIEW_DIR_NAME = "view"
REPORT_FILE_NAME = "emissions_inputs_report.json"

#: ``emissionsComputationMethod``; no default (owner decision open, issue #688).
KEY_COMPUTATION_METHOD = "emissions_computation_method"
#: ``detailedVsAverageLookupBehavior``.
KEY_LOOKUP_BEHAVIOR = "emissions_lookup_behavior"
DEFAULT_LOOKUP_BEHAVIOR = "tryDetailedThenTechnologyAverageThenAverageTable"
#: ``hbefaTableConsistencyCheckingLevel``. ``allCombinations`` (the contrib default) cannot hold
#: for a detailed table with more than one vehicle category.
KEY_CONSISTENCY_CHECK = "emissions_table_consistency_check"
DEFAULT_CONSISTENCY_CHECK = "consistent"
CONSISTENCY_CHECKS = ("allCombinations", "consistent", "none")
#: ``handleHighAverageSpeeds``: false stops the tool on an average speed above the freespeed.
KEY_HANDLE_HIGH_SPEEDS = "emissions_handle_high_average_speeds"
DEFAULT_HANDLE_HIGH_SPEEDS = False
#: Network modes of the vehicles that emit; a link none of them may use cannot matter.
KEY_EMITTING_MODES = "emissions_emitting_network_modes"
DEFAULT_EMITTING_MODES = ["car", "truck"]
#: Road type written on a link whose OSM class has no HBEFA road class.
KEY_FALLBACK_ROAD_TYPE = "emissions_fallback_road_type"
DEFAULT_FALLBACK_ROAD_TYPE = "URB/Local/50"
#: Highest share of drivable link length allowed on the fallback road type, in [0, 1].
KEY_MAX_ROAD_TYPE_FALLBACK = "emissions_max_road_type_fallback_length_share"
DEFAULT_MAX_ROAD_TYPE_FALLBACK = 0.0
#: Highest share of fleet types (own technology) allowed below the detailed table, in [0, 1].
KEY_MAX_TYPE_FALLBACK = "emissions_max_detailed_fallback_type_share"
DEFAULT_MAX_TYPE_FALLBACK = 0.0
#: Coordinate system written into the view's config (the archive says "Atlantis").
KEY_COORDINATE_SYSTEM = "emissions_coordinate_system"
DEFAULT_COORDINATE_SYSTEM = "EPSG:25832"

#: Own-package helpers whose source shapes this stage's output (hashed by validate()).
_HELPER_MODULES = (_matsim_archive, _road_types, _table_keys, _vehicle_types, _view)
#: Import closure of the helpers (ADR-0136).
_DEFERRED_HELPER_MODULE_NAMES = ("braunschweig.emissions.hbefa_tables",)


def archive_identity(output_path) -> dict | None:
    """What identifies the archive without reading the events: its info file and the events size."""
    archive = Path(output_path) / _matsim_archive.ARCHIVE_DIR_NAME
    events = archive / _matsim_archive.ARCHIVE_SENTINEL
    if not events.is_file():
        return None
    info = archive / _matsim_archive.ARCHIVE_INFO_NAME
    return {
        "archive_info_sha256": synpp_tokens.text_file_sha256(info) if info.is_file() else None,
        "events_size_bytes": events.stat().st_size,
    }


def read_settings(context) -> dict:
    """The validated emission settings of this run; raises ``ValueError`` naming the config key."""
    method = context.config(KEY_COMPUTATION_METHOD)
    if method not in _table_keys.COMPUTATION_METHODS:
        raise ValueError(
            f"{KEY_COMPUTATION_METHOD}={method!r} is not one of {_table_keys.COMPUTATION_METHODS}; "
            "the key has no default, name the method of this run"
        )
    behavior = context.config(KEY_LOOKUP_BEHAVIOR)
    if behavior not in _table_keys.LOOKUP_BEHAVIORS:
        raise ValueError(f"{KEY_LOOKUP_BEHAVIOR}={behavior!r} is not one of {_table_keys.LOOKUP_BEHAVIORS}")
    consistency = context.config(KEY_CONSISTENCY_CHECK)
    if consistency not in CONSISTENCY_CHECKS:
        raise ValueError(f"{KEY_CONSISTENCY_CHECK}={consistency!r} is not one of {CONSISTENCY_CHECKS}")
    modes = context.config(KEY_EMITTING_MODES)
    if not isinstance(modes, (list, tuple)) or not modes or not all(isinstance(m, str) and m for m in modes):
        raise ValueError(f"{KEY_EMITTING_MODES} must be a non-empty list of network modes, got {modes!r}")
    settings = {
        "computation_method": method,
        "lookup_behavior": behavior,
        "consistency_check": consistency,
        "handle_high_average_speeds": bool(context.config(KEY_HANDLE_HIGH_SPEEDS)),
        "emitting_network_modes": sorted(modes),
        "fallback_road_type": str(context.config(KEY_FALLBACK_ROAD_TYPE)),
        "coordinate_system": context.config(KEY_COORDINATE_SYSTEM),
    }
    for name, key in (("max_road_type_fallback_length_share", KEY_MAX_ROAD_TYPE_FALLBACK),
                      ("max_detailed_fallback_type_share", KEY_MAX_TYPE_FALLBACK)):
        value = context.config(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= value <= 1.0:
            raise ValueError(f"{key}={value!r} must be a share in [0, 1]")
        settings[name] = float(value)
    return settings


def validate(context):
    """synpp validation token: helper sources, the committed mapping tables, the archive identity."""
    token = synpp_tokens.source_token(
        _HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES, stage_label=__name__ + ".validate()")
    token["files"] = {
        path.name: synpp_tokens.text_file_sha256(path)
        for path in (*_vehicle_types.COMMITTED_TABLE_FILES, *_road_types.COMMITTED_TABLE_FILES)
    }
    token["archive"] = archive_identity(context.config("output_path"))
    return token


def configure(context):
    context.stage(HBEFA_STAGE)
    context.config("output_path")
    context.config(KEY_COMPUTATION_METHOD)
    context.config(KEY_LOOKUP_BEHAVIOR, DEFAULT_LOOKUP_BEHAVIOR)
    context.config(KEY_CONSISTENCY_CHECK, DEFAULT_CONSISTENCY_CHECK)
    context.config(KEY_HANDLE_HIGH_SPEEDS, DEFAULT_HANDLE_HIGH_SPEEDS)
    context.config(KEY_EMITTING_MODES, DEFAULT_EMITTING_MODES)
    context.config(KEY_FALLBACK_ROAD_TYPE, DEFAULT_FALLBACK_ROAD_TYPE)
    context.config(KEY_MAX_ROAD_TYPE_FALLBACK, DEFAULT_MAX_ROAD_TYPE_FALLBACK)
    context.config(KEY_MAX_TYPE_FALLBACK, DEFAULT_MAX_TYPE_FALLBACK)
    context.config(KEY_COORDINATE_SYSTEM, DEFAULT_COORDINATE_SYSTEM)


def execute(context):
    started = time.monotonic()
    settings = read_settings(context)
    output_path = context.config("output_path")
    archive = _matsim_archive.resolve_matsim_archive(output_path)
    if archive is None:
        raise RuntimeError(f"{_LOG_TAG} {_matsim_archive.archive_missing_reason(output_path)}")
    hbefa = context.stage(HBEFA_STAGE)
    tables = {kind: entry["path"] for kind, entry in hbefa["tables"].items()}

    view_dir = Path(context.path()) / VIEW_DIR_NAME
    if view_dir.exists():
        # The stage directory is this stage's own; a view left by an earlier failed execution is rebuilt.
        logger.info("%s removing the view of an earlier execution: %s", _LOG_TAG, view_dir)
        shutil.rmtree(view_dir)
    tagger = _road_types.RoadTypeTagger(
        frozenset(settings["emitting_network_modes"]), settings["fallback_road_type"],
        settings["max_road_type_fallback_length_share"])
    view_report = _view.build_emissions_view(
        archive, view_dir,
        _view.EmissionsViewSettings(
            tables=tables,
            lookup_behavior=settings["lookup_behavior"],
            consistency_check_level=settings["consistency_check"],
            computation_method=settings["computation_method"],
            handle_high_average_speeds=settings["handle_high_average_speeds"],
            coordinate_system=settings["coordinate_system"],
        ),
        tagger,
    )
    resolutions = view_report.vehicle_types + view_report.transit_vehicle_types
    lookups = None
    if tagger.length_meters_by_road_type:
        lookups = _table_keys.precheck_lookups(
            resolutions, tagger.length_meters_by_road_type, hbefa["keys"],
            settings["lookup_behavior"], settings["computation_method"])
    report = {
        "stage": __name__,
        "archive": str(archive),
        "archive_identity": archive_identity(output_path),
        "settings": settings,
        "tables": {kind: {name: entry[name] for name in ("path", "sha256", "case")}
                   for kind, entry in hbefa["tables"].items()},
        "view": {
            "vehicle_types": len(view_report.vehicle_types),
            "transit_vehicle_types": len(view_report.transit_vehicle_types),
            "excluded_types": sorted(r.type_id for r in resolutions if r.excluded),
            "type_notes": {r.type_id: r.note for r in resolutions if r.note},
            "links": view_report.links,
            "archive_road_types_replaced": view_report.road_types_replaced,
            "events": view_report.events_link_mode,
        },
        "road_types": tagger.report(),
        "lookups": lookups,
    }
    report_path = os.path.join(context.path(), REPORT_FILE_NAME)
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    # The checks run after the report is written, so a refused run leaves its counts on disk.
    tagger.check()
    _table_keys.check_precheck(lookups, settings["max_detailed_fallback_type_share"])
    logger.info("%s view and pre-check done in %.1f s; report %s", _LOG_TAG, time.monotonic() - started, report_path)
    return {
        "view_path": VIEW_DIR_NAME,
        "report_path": REPORT_FILE_NAME,
        "archive": str(archive),
        "lookups": lookups,
        "settings": settings,
        "tables": report["tables"],
    }
