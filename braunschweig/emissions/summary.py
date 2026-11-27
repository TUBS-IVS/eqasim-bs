"""synpp stage: totals, shares and provenance of a post-run emissions calculation (#688).

Reads the outputs of ``braunschweig.emissions.events`` and the view and pre-check of
``braunschweig.emissions.inputs``; writes into its own stage directory:

* ``emissions_by_pollutant.csv``: per pollutant the total in kg (whole simulated time), the part
  before 24:00, the after-24:00 share and the derived grams per vehicle-km;
* ``emissions_by_vehicle_group.csv``: kg per pollutant and vehicle group, with the vehicle-km of
  the group (groups: ``braunschweig.emissions.totals``);
* ``emissions_summary_report.json``: both tables plus what is needed to reproduce and to judge
  the result: archive, the sha256 and ``Case`` of each HBEFA table, the settings, the sample
  size, the fallback share by vehicle-km and the excluded groups.

All values are MODEL output computed with the configured HBEFA tables. The stage compares them
to nothing: there is no reference value in the repository, and a plausible total is not a
validated one.

The fallback share (fleet vehicle-km answered below the detailed table) is logged and, when
above zero, logged as a warning. It is bounded before the Java run, by vehicle types, in
``braunschweig.emissions.inputs``. Driving by a vehicle that is in no vehicles file, by an
excluded type, and by an emitting type without any emission are each warned about.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import time

from braunschweig import synpp_tokens
from braunschweig.emissions import totals as _totals
from braunschweig.emissions import vehicle_km as _vehicle_km

logger = logging.getLogger(__name__)

_LOG_TAG = "[emissions.summary]"

INPUTS_STAGE = "braunschweig.emissions.inputs"
EVENTS_STAGE = "braunschweig.emissions.events"
BY_POLLUTANT_FILE_NAME = "emissions_by_pollutant.csv"
BY_GROUP_FILE_NAME = "emissions_by_vehicle_group.csv"
REPORT_FILE_NAME = "emissions_summary_report.json"

#: Own-package helpers whose source shapes this stage's output (hashed by validate()).
_HELPER_MODULES = (_totals, _vehicle_km)
_DEFERRED_HELPER_MODULE_NAMES = ()


def validate(context):
    """synpp validation token over the helper sources."""
    return synpp_tokens.source_token(
        _HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES, stage_label=__name__ + ".validate()")


def configure(context):
    context.stage(INPUTS_STAGE)
    context.stage(EVENTS_STAGE)


def _write_csv(path: str, header: list, rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(header)
        writer.writerows(rows)


def execute(context):
    started = time.monotonic()
    inputs = context.stage(INPUTS_STAGE)
    events = context.stage(EVENTS_STAGE)
    events_dir = context.path(EVENTS_STAGE)
    grams = _totals.read_per_vehicle_type(os.path.join(events_dir, events["files"]["per_vehicle_type"]))
    before_day_end = _totals.read_total(os.path.join(events_dir, events["files"]["total"]))
    driven = _vehicle_km.vehicle_km_by_type(os.path.join(context.path(INPUTS_STAGE), inputs["view_path"]))
    summary = _totals.summarize(grams, before_day_end, driven, inputs["lookups"]["types"], events["sample_size"])

    groups = summary["groups"]
    if summary["fallback_vehicle_km_share"] > 0:
        logger.warning(
            "%s %.1f%% of the fleet vehicle-km are answered below the detailed HBEFA table (types: %s)",
            _LOG_TAG, 100.0 * summary["fallback_vehicle_km_share"], inputs["lookups"]["fallback_types"][:10])
    if groups["excluded"]["vehicle_km_sample"] > 0:
        logger.warning(
            "%s excluded vehicle types drive %.1f sample vehicle-km and emit nothing by assumption",
            _LOG_TAG, groups["excluded"]["vehicle_km_sample"])
    if summary["unknown_vehicle_link_entries"] or groups["unknown"]["vehicle_km_sample"] > 0:
        logger.warning(
            "%s %d link entries (%.1f sample vehicle-km) come from vehicles in no vehicles file",
            _LOG_TAG, summary["unknown_vehicle_link_entries"], summary["unknown_vehicle_km_sample"])
    if summary["types_driving_without_emissions"]:
        logger.warning(
            "%s %d emitting vehicle types drive but have no emission in the tool's output: %s",
            _LOG_TAG, len(summary["types_driving_without_emissions"]), summary["types_driving_without_emissions"][:10])

    stage_dir = context.path()
    _write_csv(
        os.path.join(stage_dir, BY_POLLUTANT_FILE_NAME),
        ["pollutant", "total_kg", "before_24h_kg", "after_24h_share", "gram_per_vehicle_km"],
        [[pollutant, entry["total_kg"], entry["before_24h_kg"], entry["after_24h_share"], entry["gram_per_vehicle_km"]]
         for pollutant, entry in summary["pollutants"].items()],
    )
    _write_csv(
        os.path.join(stage_dir, BY_GROUP_FILE_NAME),
        ["vehicle_group", "pollutant", "total_kg", "vehicle_km_scaled"],
        [[group, pollutant, kilograms, entry["vehicle_km_scaled"]]
         for group, entry in groups.items() for pollutant, kilograms in sorted(entry["kilograms"].items())],
    )
    report = {
        "stage": __name__,
        "classification": "model output; compared to no reference value",
        "archive": inputs["archive"],
        "hbefa_tables": inputs["tables"],
        "settings": inputs["settings"],
        "grid_size_meters": events["grid_size_meters"],
        "excluded_types": sorted(e["type_id"] for e in inputs["lookups"]["types"] if e["excluded"]),
        "fallback_types": inputs["lookups"]["fallback_types"],
        "units": {
            "total_kg": "kg, whole simulated time, already scaled by 1 / sample size by the tool",
            "before_24h_kg": "kg before 24:00 (emissions_total.csv of the tool)",
            "vehicle_km_sample": "vehicle-km of the sample, full link length per 'entered link' event",
            "vehicle_km_scaled": "vehicle_km_sample / sample size",
            "gram_per_vehicle_km": "derived: total over the scaled vehicle-km of all emitting types",
        },
        "summary": summary,
    }
    with open(os.path.join(stage_dir, REPORT_FILE_NAME), "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    logger.info("%s %d pollutants summarised in %.0f s; report %s", _LOG_TAG, len(summary["pollutants"]),
                time.monotonic() - started, os.path.join(stage_dir, REPORT_FILE_NAME))
    return {
        "summary": summary,
        "files": {"by_pollutant": BY_POLLUTANT_FILE_NAME, "by_vehicle_group": BY_GROUP_FILE_NAME},
        "report_path": REPORT_FILE_NAME,
    }
