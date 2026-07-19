"""Run the synpp pipeline with timestamped logging.

Equivalent to ``python -m synpp <config.yml>`` but installs a logging format that
prefixes every synpp log line with an ISO timestamp (asctime). This lets
``braunschweig.analysis.runtime`` compute per-stage wall-clock durations from the
run log, so the slowest stages (secondary locations / location choice, gravity,
...) are visible and settings can be tuned with evidence.

Used by ``scripts/run_pipeline.sh`` in place of ``python -m synpp``. MATSim's Java
stdout keeps its own timestamps; braunschweig ``print()`` diagnostics are
unchanged. ``force=True`` ensures this format wins even if synpp configures
logging itself.
"""
from __future__ import annotations

import logging
import os
import sys

# Running this file as a script puts scripts/ on sys.path[0] (NOT the repo root),
# so synpp could not import the stage packages (synthesis, matsim, braunschweig)
# and failed with "<stage> is not a supported object". Prepend the repo root (the
# parent of scripts/) so stage modules resolve exactly as with `python -m synpp`.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

import synpp
import yaml

from braunschweig import cache_share

# Conservative default set of stages eligible for shared-cache priming: the freight
# chain is sampling-independent, expensive (~3 h Java routing), and has a
# machine-independent hash. Widen via the config key `cache_share_stages` once other
# stages' hash stability is confirmed (see docs/superpowers/specs/2026-06-22-...).
DEFAULT_CACHE_SHARE_STAGES = [
    "braunschweig.data.freight.german_wide",
    "braunschweig.freight.extraction",
]


def prime_from_config(config_path):
    """Prime the run's working_directory from the shared cache store before synpp runs.

    Reads ``cache_share_*`` keys from the YAML config and copies matching stage
    artifacts from the store into the working_directory, so synpp finds them as cache
    hits. Returns the prime report dict, or ``None`` when ``cache_share_enabled`` is
    false (then this is a pure no-op -> byte-identical to a plain synpp run). A
    primed entry whose hash does not match the target config is simply ignored by
    synpp (recomputed) -- never a corruption.
    """
    with open(config_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    cfg = doc.get("config", {}) or {}
    if not cfg.get("cache_share_enabled", True):
        logging.getLogger("braunschweig").info("[cache_share] disabled (no-op).")
        return None
    working_directory = doc.get("working_directory")
    store = cfg.get("cache_share_store", "eqasim-data/cache_shared")
    stages = cfg.get("cache_share_stages", DEFAULT_CACHE_SHARE_STAGES)
    recompute = cfg.get("cache_share_recompute", []) or []
    if not working_directory or not os.path.isdir(store):
        logging.getLogger("braunschweig").info(
            "[cache_share] store '%s' absent or no working_directory -> nothing to prime.",
            store)
        return {"primed": [], "skipped_present": [], "forced": [], "missing_in_store": list(stages)}
    os.makedirs(working_directory, exist_ok=True)
    return cache_share.prime(working_directory, stages, store, recompute)


def export_to_store_from_config(config_path):
    """Export the run's shareable stage caches into the shared store AFTER the run.

    Symmetric to :func:`prime_from_config`. Reads the same ``cache_share_*`` keys and
    copies the configured ``cache_share_stages`` from the working_directory into the
    store, so a completed run seeds the store for the next one. Gated by BOTH
    ``cache_share_enabled`` (master switch) and ``cache_share_export`` (default True):
    either being false makes this a logged no-op, so a throwaway config can prime from
    the store without writing its own stages back into it.

    Uses ``skip_existing=True`` so an entry already in the store is never overwritten;
    a different config/content has a different hash and is stored alongside. Returns the
    export report, or ``None`` when disabled. Called only after a successful run (a
    failed/partial run raises before reaching this, so it never seeds the store).
    """
    log = logging.getLogger("braunschweig")
    with open(config_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f) or {}
    cfg = doc.get("config", {}) or {}
    if not cfg.get("cache_share_enabled", True):
        log.info("[cache_share] auto-export disabled (cache_share_enabled false).")
        return None
    if not cfg.get("cache_share_export", True):
        log.info("[cache_share] auto-export disabled (cache_share_export false).")
        return None
    working_directory = doc.get("working_directory")
    store = cfg.get("cache_share_store", "eqasim-data/cache_shared")
    stages = cfg.get("cache_share_stages", DEFAULT_CACHE_SHARE_STAGES)
    if not working_directory:
        log.info("[cache_share] auto-export: no working_directory -> nothing to export.")
        return None
    os.makedirs(store, exist_ok=True)
    report = cache_share.export(working_directory, stages, store, skip_existing=True)
    log.info(
        "[cache_share] auto-export: exported %d, already-in-store %d, not-in-cache %s",
        len(report["exported"]), len(report["skipped_present"]), report["skipped"] or "[]",
    )
    return report


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        print("usage: python scripts/run_synpp.py <config.yml>", file=sys.stderr)
        return 1
    from braunschweig.logging_setup import setup_logging

    log_path = setup_logging(level="INFO")
    logging.getLogger("braunschweig").info("Run log: %s", log_path)
    prime_from_config(argv[0])
    synpp.run_from_yaml(argv[0], None, [], {})
    # Export the shareable stage caches into the shared store ONLY after a successful
    # run (run_from_yaml raises on failure, so a failed/partial run never seeds the
    # store). Gated by cache_share_enabled + cache_share_export inside the helper.
    export_to_store_from_config(argv[0])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
