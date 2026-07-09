"""Run catalog: manifests (authoritative) + legacy artifact directories.

Three origins, merged by run_id / directory name:
  manifest    rc_*.manifest.json written by runcontrol launches
  db          runs known to the local SQLite (queued ones without manifest yet)
  legacy_dir  output_*/cache_* directories from pre-runcontrol runs -- fields
              are 'unknown', a sampling hint is derived from the directory
              name and labelled as such, flags=['no_manifest'].
`scan()` performs exactly ONE `listdir(data_dir)` call and no per-directory I/O:
legacy entries are built purely from the directory-listing fields already
returned by that single call (name, mtime). Over an ssh target, one round-trip
per legacy directory made the catalog list take multiple seconds (issue #119),
so any check that needs to read inside a directory (e.g. whether a
`*_meta.json`'s sampling rate contradicts the directory-name hint --
'meta_inconsistent', a known server issue, see RUNS.md) is deferred to
`collectors/enrich.py`, which is only invoked lazily for a single artifact's
detail view and already reads that artifact's meta.json for other reasons.
Counts of manifest vs legacy are returned AND logged (no silent degradation)."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from ..models import RunManifest

logger = logging.getLogger(__name__)

_SAMPLING_RE = re.compile(r"(\d+)pct")


@dataclass
class CatalogResult:
    runs: list[dict]
    n_manifest: int
    n_legacy: int


def _sampling_hint(dirname: str) -> str:
    m = _SAMPLING_RE.search(dirname)
    return f"{m.group(1)}pct" if m else "unknown"


def _legacy_entry(target, entry: dict) -> dict:
    # Built ONLY from the directory-listing entry already returned by the single
    # bulk listdir(data_dir) call in scan() below -- no per-directory I/O here.
    name = entry["name"]
    return {"run_id": name, "target": target.name, "label": name, "origin": "legacy_dir",
            "status": "unknown", "git_commit": "unknown", "config_path": "unknown",
            "sampling_hint": _sampling_hint(name), "mtime": entry["mtime"],
            "kind": "output" if name.startswith("output_") else "cache", "flags": ["no_manifest"]}


def scan(target, db_runs: list[dict]) -> CatalogResult:
    runs: list[dict] = []
    seen: set[str] = set()

    for mpath in target.manifest_glob():
        m = RunManifest.from_json(target.read_text(mpath))
        runs.append({"run_id": m.run_id, "target": m.target, "label": m.label,
                     "origin": "manifest", "status": "unknown",       # daemon reconciles status
                     "git_commit": m.git_commit, "config_path": m.config_path,
                     "sampling_hint": _sampling_hint(m.label), "mtime": None,
                     "kind": "run", "flags": [], "log_path": m.log_path,
                     "started_at_iso": m.started_at_iso})
        seen.add(m.run_id)

    for row in db_runs:
        if row["run_id"] not in seen:
            runs.append({**row, "origin": "db", "sampling_hint": _sampling_hint(row["label"]),
                         "kind": "run", "flags": []})
            seen.add(row["run_id"])

    n_legacy = 0
    for entry in target.listdir(target.cfg.data_dir):
        name = entry["name"]
        if not (name.startswith("output_") or name.startswith("cache_")) or name in seen:
            continue
        runs.append(_legacy_entry(target, entry))
        n_legacy += 1

    n_manifest = sum(1 for r in runs if r["origin"] == "manifest")
    logger.info("catalog[%s]: %d with manifest, %d from db, %d legacy dirs (fields unknown)",
                target.name, n_manifest, len(runs) - n_manifest - n_legacy, n_legacy)
    return CatalogResult(runs=runs, n_manifest=n_manifest, n_legacy=n_legacy)
