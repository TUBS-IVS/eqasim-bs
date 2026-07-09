"""Reconstruct an artifact's effective config + timeline from real sources.

The "effective config" is the UNION of the per-stage `config` dicts recorded in
the synpp `pipeline.json` at the cache root -- only stages still cached
contribute, so it is always labelled partial (N contributing stages). Head
facts (sampling/seed/commit/date) come from the output dir's
`braunschweig_*_meta.json`. The stage timeline is derived from consecutive
`updated` epochs (synpp runs stages sequentially); the first stage's duration
is unknown. Every source's read status is recorded in `sources` -- a
partially-readable artifact yields a partial Enrichment, never an exception.
This module is read-only: it never launches, mutates or deletes anything.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

from .. import registry

_META_RE = re.compile(r"_meta\.json$")
_SAMPLING_RE = re.compile(r"(\d+)pct")
# Mirrors the hint->fraction mapping catalog.py uses for its directory-name-derived
# sampling_hint; kept local to avoid a cross-module dependency on private names.
_META_SAMPLING = {"1pct": 0.01, "10pct": 0.10, "25pct": 0.25, "100pct": 1.0}


def _sampling_hint(name: str) -> str:
    m = _SAMPLING_RE.search(name)
    return f"{m.group(1)}pct" if m else "unknown"


@dataclass
class Enrichment:
    name: str
    kind: str
    paired_name: str | None = None
    meta: dict | None = None
    run_date_iso: str | None = None
    effective_config: dict = field(default_factory=dict)
    effective_config_stage_count: int = 0
    curated_view: dict = field(default_factory=dict)
    uncurated: dict = field(default_factory=dict)
    config_conflicts: list = field(default_factory=list)
    timeline: list = field(default_factory=list)
    presence: dict = field(default_factory=dict)
    flags: list = field(default_factory=list)
    sources: dict = field(default_factory=dict)
    size_bytes: int | None = None


def paired_artifact_name(name: str) -> str | None:
    if name.startswith("output_"):
        return "cache_" + name[len("output_"):]
    if name.startswith("cache_"):
        return "output_" + name[len("cache_"):]
    return None


def newest_activity_mtime(target, name: str) -> float | None:
    """Newest top-level child mtime across an artifact's cache/output dir pair.

    Watching the artifact directory's OWN mtime (as `listdir` on its parent
    reports it) is unreliable liveness: a directory entry's mtime only advances
    when its DIRECT children are added or removed, not when files deep inside it
    grow. A long synpp stage or MATSim iteration can write for many minutes
    without ever touching the top-level child set of `cache_<name>` or
    `output_<name>`, which makes a genuinely running run look stale.

    Instead this looks at the newest mtime among the TOP-LEVEL children of both
    the cache dir and the paired output dir (two cheap `listdir` calls, never a
    recursive scan): the cache dir gets a fresh top-level `*.cache` file (and an
    updated `pipeline.json`) each time a synpp stage completes, and the output
    dir gets fresh per-iteration files written directly into its root while
    MATSim runs. Together these two signals track real activity without
    walking either tree.

    Returns None only when neither directory has any top-level child (nothing
    to observe yet, e.g. right after the run started).
    """
    data_dir = target.cfg.data_dir
    paired = paired_artifact_name(name)
    newest: float | None = None
    for candidate in (name, paired):
        if candidate is None:
            continue
        for entry in target.listdir(f"{data_dir}/{candidate}"):
            mtime = float(entry["mtime"])
            if newest is None or mtime > newest:
                newest = mtime
    return newest


def merge_stage_configs(pipeline: dict) -> tuple[dict, list[str], int]:
    merged: dict = {}
    conflicts: set[str] = set()
    stages = [e for e in pipeline.values() if isinstance(e, dict) and isinstance(e.get("config"), dict)]
    # Deterministic order: by `updated` epoch so "last writer" is the latest stage.
    for entry in sorted(stages, key=lambda e: e.get("updated", 0)):
        for k, v in entry["config"].items():
            if k in merged and merged[k] != v:
                conflicts.add(k)
            merged[k] = v
    return merged, sorted(conflicts), len(stages)


def timeline_from_pipeline(pipeline: dict) -> list[dict]:
    rows = []
    for full, entry in pipeline.items():
        if not isinstance(entry, dict) or "updated" not in entry:
            continue
        rows.append((float(entry["updated"]), re.sub(r"__[0-9a-f]+$", "", full)))
    rows.sort()
    out = []
    prev = None
    for epoch, short in rows:
        dur = None if prev is None else round(epoch - prev, 1)
        out.append({"stage_short": short,
                    "completed_at_iso": datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds"),
                    "approx_duration_s": dur})
        prev = epoch
    return out


def curated_split(config: dict) -> tuple[dict, dict]:
    by = registry.by_key()
    groups: dict[str, list[dict]] = {g: [] for g in registry.groups()}
    for f in registry.FLAGS:
        if f.key in config:
            groups[f.group].append(
                {"key": f.key, "value": config[f.key], "unit": f.unit,
                 "type": f.type, "description": f.description})
    groups = {g: rows for g, rows in groups.items() if rows}
    rest = {k: v for k, v in config.items() if k not in by}
    return groups, rest


def _read_json(target, relpath: str) -> tuple[dict | None, str]:
    if not target.exists(relpath):
        return None, "missing"
    try:
        return json.loads(target.read_text(relpath)), "ok"
    except (ValueError, OSError, RuntimeError) as exc:
        return None, f"error:{exc}"


def enrich_artifact(target, name: str, kind: str) -> Enrichment:
    e = Enrichment(name=name, kind=kind)
    data_dir = target.cfg.data_dir
    paired = paired_artifact_name(name)
    e.paired_name = paired

    cache_name = name if name.startswith("cache_") else paired
    output_name = name if name.startswith("output_") else paired

    # pipeline.json lives in the cache dir
    pj, pj_status = (None, "missing")
    if cache_name:
        pj, pj_status = _read_json(target, f"{data_dir}/{cache_name}/pipeline.json")
    e.sources["pipeline_json"] = pj_status
    if pj:
        e.effective_config, e.config_conflicts, e.effective_config_stage_count = merge_stage_configs(pj)
        e.timeline = timeline_from_pipeline(pj)
        for k in e.config_conflicts:
            e.flags.append(f"config_conflict:{k}")

    e.curated_view, e.uncurated = curated_split(e.effective_config)

    # meta.json + presence live in the output dir
    meta_status = "missing"
    if output_name and target.exists(f"{data_dir}/{output_name}"):
        listing = target.listdir(f"{data_dir}/{output_name}")
        names = {c["name"] for c in listing}
        e.presence = {
            "matsim_config": any(n.endswith("_config.xml") for n in names),
            "analysis": "analysis" in names,
            "simwrapper": "simwrapper" in names,
        }
        meta_file = next((n for n in names if _META_RE.search(n)), None)
        if meta_file:
            e.meta, meta_status = _read_json(target, f"{data_dir}/{output_name}/{meta_file}")
    else:
        e.presence = {"matsim_config": False, "analysis": False, "simwrapper": False}
    e.sources["meta_json"] = meta_status

    # A legacy directory name implying one sampling rate (e.g. '..._25pct...') whose
    # meta.json records a different sampling_rate is a known server-side data issue
    # (see RUNS.md) -- flagged, not fixed, and not silently trusted either way. This
    # check needs meta.json anyway, which is already read above, so it adds no I/O.
    if e.meta is not None:
        hint = _sampling_hint(output_name or name)
        meta_rate = e.meta.get("sampling_rate")
        if hint in _META_SAMPLING and meta_rate is not None and float(meta_rate) != _META_SAMPLING[hint]:
            e.flags.append("meta_inconsistent")

    # run date: meta.created, else latest timeline epoch, else None
    if e.meta and e.meta.get("created"):
        e.run_date_iso = str(e.meta["created"])
    elif e.timeline:
        e.run_date_iso = e.timeline[-1]["completed_at_iso"]

    # pairing flags
    if paired is None:
        e.flags.append("unpaired_name")
    elif not target.exists(f"{data_dir}/{paired}"):
        e.flags.append("no_paired_output" if name.startswith("cache_") else "no_paired_cache")

    return e
