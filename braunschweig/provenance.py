"""Crash-proof run provenance at pipeline launch (issue #125).

The only provenance record used to be ``documentation/meta_output.py``
(upstream eqasim), written when its stage COMPLETES -- a killed multi-hour run
left no trace of which commit / config / seed produced the partial cache, and
``RUNS.md`` documents a server ``meta.json`` that was wrong for its output.
The commit of the sibling ``eqasim-java-bs`` repo (``eqasim_source_path``,
which changes MATSim behaviour independently) was recorded nowhere.

This module is called by ``scripts/run_synpp.py`` BEFORE synpp starts: it
logs one INFO banner and writes a timestamped ``run_provenance_*.json`` into
the run's ``working_directory``, so even a killed run is traceable
(CLAUDE.md scientific-reproducibility section). The upstream
``meta_output.py`` stage is deliberately NOT modified (fork-divergence
minimisation); its ``meta.json`` can be cross-checked against this record.
"""
from __future__ import annotations

import datetime
import json
import logging
import os
import platform
import subprocess

import yaml

logger = logging.getLogger(__name__)

# Config keys copied verbatim into the provenance record. sampling_rate / hts /
# random_seed mirror meta_output.py; braunschweig.population.method is the
# actual producer switch (an ``hts: entd`` entry alone is misleading for a
# popsim_mid run -- exactly the documented wrong-meta.json failure).
PROVENANCE_CONFIG_KEYS = (
    "sampling_rate",
    "hts",
    "random_seed",
    "braunschweig.population.method",
)


def git_commit(repo_path: str) -> str:
    """Short commit hash of ``repo_path``, with a ``+dirty`` suffix.

    Returns ``"unknown"`` (and logs a warning -- no silent fallback) when the
    path is not a git repository or git is unavailable. Never raises: the
    provenance banner must not be able to kill a run.
    """
    try:
        commit = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=repo_path, stderr=subprocess.DEVNULL,
        ).strip().decode("utf-8")
        status = subprocess.check_output(
            ["git", "status", "--porcelain"],
            cwd=repo_path, stderr=subprocess.DEVNULL,
        ).strip().decode("utf-8")
        return commit + ("+dirty" if status else "")
    except (OSError, subprocess.CalledProcessError) as exc:
        logger.warning(
            "[provenance] cannot determine git commit for %r (%s); recording "
            "'unknown'.", repo_path, exc,
        )
        return "unknown"


def collect_run_provenance(config_path: str) -> dict:
    """Assemble the launch-time provenance record for a synpp config.

    Reads the YAML config (never raises on a malformed one -- the record then
    carries an ``error`` note instead, and the run proceeds).
    """
    record: dict = {
        "launched_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "config_path": os.path.abspath(config_path),
        "python_version": platform.python_version(),
        "pipeline_commit": git_commit(os.path.dirname(os.path.dirname(
            os.path.abspath(__file__)))),
    }
    try:
        with open(config_path, encoding="utf-8") as f:
            doc = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("[provenance] cannot read config %r (%s).", config_path, exc)
        record["error"] = f"config unreadable: {exc}"
        return record

    cfg = doc.get("config", {}) or {}
    record["working_directory"] = doc.get("working_directory")
    for key in PROVENANCE_CONFIG_KEYS:
        record[key] = cfg.get(key)

    # The sibling Java repo changes MATSim behaviour independently of this
    # repo's commit -- record it whenever the config points at one.
    eqasim_source_path = cfg.get("eqasim_source_path")
    record["eqasim_source_path"] = eqasim_source_path
    if eqasim_source_path:
        java_path = eqasim_source_path
        if not os.path.isabs(java_path):
            java_path = os.path.join(os.path.dirname(os.path.abspath(config_path)),
                                     eqasim_source_path)
        record["eqasim_java_commit"] = (
            git_commit(java_path) if os.path.isdir(java_path) else "unknown"
        )
        if record["eqasim_java_commit"] == "unknown" and not os.path.isdir(java_path):
            logger.warning(
                "[provenance] eqasim_source_path %r does not exist relative to "
                "the config; recording eqasim_java_commit='unknown'.", eqasim_source_path,
            )
    return record


def log_and_write_run_provenance(config_path: str) -> dict:
    """Log the provenance banner and persist it next to the run's cache.

    Writes ``run_provenance_<UTC-stamp>.json`` into the config's
    ``working_directory`` (created if needed) so a killed run still leaves the
    record; a missing working_directory downgrades to log-only (warned).
    Never raises.
    """
    record = collect_run_provenance(config_path)
    logger.info(
        "[provenance] pipeline_commit=%s eqasim_java_commit=%s config=%s "
        "sampling_rate=%s hts=%s random_seed=%s population.method=%s python=%s",
        record.get("pipeline_commit"), record.get("eqasim_java_commit", "n/a"),
        record.get("config_path"), record.get("sampling_rate"),
        record.get("hts"), record.get("random_seed"),
        record.get("braunschweig.population.method"),
        record.get("python_version"),
    )
    working_directory = record.get("working_directory")
    if not working_directory:
        logger.warning(
            "[provenance] config has no working_directory; provenance is "
            "logged only, not persisted."
        )
        return record
    try:
        os.makedirs(working_directory, exist_ok=True)
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_path = os.path.join(working_directory, f"run_provenance_{stamp}.json")
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(record, f, indent=2)
        logger.info("[provenance] record written to %s", out_path)
    except OSError as exc:
        logger.warning("[provenance] cannot persist record (%s); logged only.", exc)
    return record
