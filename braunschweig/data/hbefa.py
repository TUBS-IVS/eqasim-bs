"""synpp stage: the four HBEFA emission-factor tables, checked and indexed (post-run emissions, #688).

HBEFA is licensed. The tables are never downloaded, never committed and never copied: the user
exports them from the HBEFA software and names the four files in the configuration. The four
keys have NO default on purpose, so a run without tables stops at configuration time instead of
computing emissions from something else (the upstream eqasim stage downloads the MATSim sample
tables, whose values are deliberately altered).

The stage

* refuses a missing, empty, wrong or MATSim-sample table (``braunschweig.emissions.hbefa_tables``);
* indexes the KEY columns of the tables (``braunschweig.emissions.table_keys``) and refuses a table
  whose rows repeat a key, because the emissions tool would silently keep the later row;
* writes ``hbefa_report.json`` into the stage directory: per table the path, sha256, size, row
  count and ``Case`` value, and the counts of the key index. No emission factor is read into the
  report or the result.

Returns ``{"tables": {kind: {path, sha256, size_bytes, data_rows, case}}, "keys": <key index>,
"report_path": "hbefa_report.json"}``; ``report_path`` is relative to the stage directory. The key
index is what the emissions input stage checks the vehicle types and road types against.

The ``validate()`` token holds the sha256 of each table, so replacing a table re-runs this stage
and everything computed from it.
"""
from __future__ import annotations

import json
import logging
import os
import time

from braunschweig import synpp_tokens
from braunschweig.emissions import hbefa_tables as _hbefa_tables
from braunschweig.emissions import table_keys as _table_keys

logger = logging.getLogger(__name__)

_LOG_TAG = "[data.hbefa]"

#: Config key per table kind. No defaults: see the module docstring.
KEY_TABLE_PATHS = {
    "cold_average": "hbefa_cold_average_path",
    "warm_average": "hbefa_warm_average_path",
    "cold_detailed": "hbefa_cold_detailed_path",
    "warm_detailed": "hbefa_warm_detailed_path",
}
REPORT_FILE_NAME = "hbefa_report.json"

#: Own-package helpers whose source shapes this stage's output (hashed by validate()).
_HELPER_MODULES = (_hbefa_tables, _table_keys)
#: Import closure of the helpers (ADR-0136): ``table_keys`` imports the vehicle-type names.
_DEFERRED_HELPER_MODULE_NAMES = ("braunschweig.emissions.vehicle_types",)


def table_paths(context) -> dict:
    """The four configured table paths by kind; an unset key raises ``ValueError`` naming it."""
    paths = {}
    unset = []
    for kind in _hbefa_tables.TABLE_KINDS:
        value = context.config(KEY_TABLE_PATHS[kind])
        if value is None or not str(value).strip():
            unset.append(KEY_TABLE_PATHS[kind])
        paths[kind] = value
    if unset:
        raise ValueError(
            f"{_LOG_TAG} config keys {unset} are not set. HBEFA is licensed: export the four tables from "
            "the HBEFA software and set their paths; they are never downloaded and have no default."
        )
    return paths


def validate(context):
    """synpp validation token: helper sources plus the sha256 of each configured table."""
    token = synpp_tokens.source_token(
        _HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES, stage_label=__name__ + ".validate()")
    token["files"] = {
        kind: synpp_tokens.input_file_sha256(path, f"HBEFA table '{kind}' ({KEY_TABLE_PATHS[kind]})")
        for kind, path in table_paths(context).items()
    }
    return token


def configure(context):
    for key in KEY_TABLE_PATHS.values():
        context.config(key)


def execute(context):
    started = time.monotonic()
    tables = _hbefa_tables.inspect_hbefa_tables(table_paths(context))
    index = _table_keys.read_table_keys({kind: entry["path"] for kind, entry in tables.items()})
    report = {
        "stage": __name__,
        "note": "Key columns only; no emission factor is read into this report.",
        "tables": tables,
        "keys": _table_keys.summarize_table_keys(index),
    }
    report_path = os.path.join(context.path(), REPORT_FILE_NAME)
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, sort_keys=True)
    # After the report is written, so a refused table leaves its counts on disk.
    _table_keys.check_table_keys(index)
    logger.info(
        "%s four tables accepted in %.1f s: %s; report %s", _LOG_TAG, time.monotonic() - started,
        ", ".join(f"{kind} {entry['data_rows']} rows (case {entry['case']!r})" for kind, entry in tables.items()),
        report_path,
    )
    return {"tables": tables, "keys": index, "report_path": REPORT_FILE_NAME}
