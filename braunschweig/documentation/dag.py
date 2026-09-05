"""Extract the ACTUAL synpp dependency graph without executing any stage.

The synpp DAG -- not any hand-maintained list -- is the authoritative source for
stage existence and dependencies (docs/DOCUMENTATION_GOVERNANCE.md). synpp 1.6.2
resolves the full stage registry (running every ``configure()``) before touching
data, and ``synpp.run(..., dryrun=True)`` returns the name-level dependency graph
as networkx ``node_link_data`` without executing anything. This module wraps that
call for the project's composed/fixture configs and persists deterministic JSON
snapshots under ``docs/registry/dag/`` so that metadata-only environments (CI,
checkouts without the scientific stack) can still resolve stages against the DAG.

Snapshot freshness is verified by ``braunschweig.documentation.checks`` whenever
synpp is importable; without synpp the DAG-dependent checks report SKIP, never a
silent pass.

synpp and the stage modules (pandas etc.) are imported lazily: importing THIS
module must work with PyYAML alone.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Dict, List, Optional

import yaml

logger = logging.getLogger("braunschweig")

DAG_DIRECTORY = os.path.join("docs", "registry", "dag")

#: The tracked pipeline snapshots: name -> (base config, overlay | None).
#: ``production`` is the canonical production configuration (ADR-0077): the fixed
#: feature base composed with the 100% scale overlay -- feature flags live ONLY in
#: the base, so flag values are scale-invariant across overlays (checked by K5).
PIPELINE_CONFIGS: Dict[str, Dict[str, Optional[str]]] = {
    "production": {
        "base": os.path.join("configs", "base_bs.yml"),
        "overlay": os.path.join("configs", "overlays", "test_100pct.yml"),
    },
    "popsim_open": {
        "base": os.path.join("configs", "fixtures", "config_popsim_open_braunschweig.yml"),
        "overlay": None,
    },
    "simple_ipf_open": {
        # The 25% fixture is the committed real-data configuration of the legacy
        # IPF workflow (the one issue #255 designates as where the enriched-stage
        # attribute features actually execute), not the 1% laptop smoke.
        "base": os.path.join("configs", "fixtures", "config_local_braunschweig_25pct.yml"),
        "overlay": None,
    },
}


def _load_document(repo_root: str, base: str, overlay: Optional[str]) -> dict:
    """Load a run config document, composing base + overlay when an overlay is given."""
    base_path = os.path.join(repo_root, base)
    if overlay is not None:
        from braunschweig import config_compose
        return config_compose.compose(base_path, os.path.join(repo_root, overlay))
    with open(base_path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def extract(repo_root: str, base: str, overlay: Optional[str] = None) -> dict:
    """Resolve the full stage graph of one run config via ``synpp.run(dryrun=True)``.

    Returns a deterministic snapshot dict: sorted ``nodes`` (stage descriptors --
    every node is required by the config's ``run`` targets, because synpp builds
    the registry by expanding dependencies from exactly those definitions),
    sorted ``edges`` (``[dependency, dependent]``), the ``targets`` (the ``run``
    list) and the source config identity. Raises with a clear message when synpp
    or the stage modules are not importable in this environment.
    """
    import sys

    if repo_root not in sys.path:
        # Stage modules (braunschweig, synthesis, matsim, ...) resolve relative to
        # the repository root, exactly as scripts/run_synpp.py sets it up.
        sys.path.insert(0, repo_root)

    try:
        import synpp
    except ImportError as error:
        raise RuntimeError(
            "synpp is not importable in this environment; DAG extraction needs the "
            "project 'eqasim' environment (conda). Metadata-only checks still run "
            "without it (DAG checks report SKIP).") from error

    document = _load_document(repo_root, base, overlay)

    run_targets: List = document.get("run") or []
    if not run_targets:
        raise ValueError(f"config {base} (+ {overlay}) has no non-empty 'run' list")

    definitions = []
    for item in run_targets:
        parameters = {}
        if isinstance(item, dict):
            key = list(item.keys())[0]
            parameters = item[key]
            item = key
        definitions.append({"descriptor": item, "config": parameters})

    from braunschweig import synpp_deterministic

    # The name-level graph does not depend on stage hashes, but install the same
    # deterministic propagation the production entry point uses so that a dryrun
    # exercises exactly the code path of a real run.
    synpp_deterministic.install()
    previous_directory = os.getcwd()
    os.chdir(repo_root)  # relative paths inside configure() resolve like a real run
    try:
        graph = synpp.run(
            definitions,
            config=document.get("config", {}),
            working_directory=None,
            dryrun=True,
            externals=document.get("externals", {}) or {},
            aliases=document.get("aliases", {}) or {},
        )
    finally:
        os.chdir(previous_directory)

    nodes = sorted(str(node["id"]) for node in graph.get("nodes", []))
    edges = sorted(
        [str(edge["source"]), str(edge["target"])] for edge in graph.get("edges", []))
    targets = sorted(
        str(list(item.keys())[0]) if isinstance(item, dict) else str(item)
        for item in run_targets)

    logger.info("[documentation] extracted DAG for %s (+%s): %d stages, %d edges",
                base, overlay, len(nodes), len(edges))
    return {
        "config": {"base": base.replace(os.sep, "/"),
                   "overlay": overlay.replace(os.sep, "/") if overlay else None},
        "targets": targets,
        "nodes": nodes,
        "edges": edges,
    }


def snapshot_path(repo_root: str, pipeline: str) -> str:
    return os.path.join(repo_root, DAG_DIRECTORY, f"{pipeline}.json")


def write_snapshots(repo_root: str, pipelines: Optional[List[str]] = None) -> List[str]:
    """Extract and persist the tracked pipeline snapshots; returns written paths."""
    written = []
    for pipeline in pipelines or sorted(PIPELINE_CONFIGS):
        spec = PIPELINE_CONFIGS[pipeline]
        data = extract(repo_root, spec["base"], spec["overlay"])
        path = snapshot_path(repo_root, pipeline)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, indent=1, sort_keys=True)
            f.write("\n")
        logger.info("[documentation] wrote %s", path)
        written.append(path)
    return written


def load_snapshot(repo_root: str, pipeline: str) -> Optional[dict]:
    """Load a committed DAG snapshot; ``None`` when it does not exist."""
    path = snapshot_path(repo_root, pipeline)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_all_snapshots(repo_root: str) -> Dict[str, dict]:
    """Load every committed snapshot present under ``docs/registry/dag/``."""
    snapshots = {}
    for pipeline in PIPELINE_CONFIGS:
        data = load_snapshot(repo_root, pipeline)
        if data is not None:
            snapshots[pipeline] = data
    return snapshots
