"""Work-dir batch cache invalidation for popsim_mid (config-signature guard).

- :data:`WORK_DIR_SIGNATURE_FILE` -- filename of the per-work_dir config
  signature used to detect a config change and purge stale batch folders.
- :func:`purge_stale_batches_on_config_change` -- remove stale ``batch_*``
  folders when the popsim config/control set changed since the last run in
  this ``work_dir``.
- :func:`_frame_content_signature` -- content signature of a seed/target
  frame (row values + column layout), used by
  :func:`compute_batch_config_signature`.
- :func:`compute_batch_config_signature` -- compute the work-dir batch-input
  signature (sha256 hex) that captures everything determining a batch's
  inputs.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

import hashlib
import json
import logging
import shutil
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# Filename of the per-work_dir config signature used to detect a config change and
# purge stale batch folders (see purge_stale_batches_on_config_change).
WORK_DIR_SIGNATURE_FILE = ".popsim_config_signature"


def purge_stale_batches_on_config_change(work_dir, signature: str) -> int:
    """Remove stale ``batch_*`` folders when the popsim config/control set changed.

    The PopulationSim ``work_dir`` persists across runs (it lives OUTSIDE synpp's
    stage cache), and the batch runner SKIPS any batch whose completion marker
    (``output/final_expanded_household_ids.csv``) already exists. If the config changed
    since the run that produced those outputs (e.g. tier3 / employment_grid controls
    were added, changing the per-batch inputs), skipping them would merge an
    old-config population for those cells -- a silent correctness bug.

    Guard: a signature file in ``work_dir`` records the config that produced the
    current batches. On a MISMATCH (or first run with pre-existing folders) every
    ``batch_*`` folder is removed so all batches re-run with the current config. On a
    MATCH (same config -- e.g. a resumed interrupted run) the folders are kept, so the
    skip-completed-batches resume optimisation still works. Returns the number of
    batch folders purged.
    """
    work_dir = Path(work_dir)
    sig_path = work_dir / WORK_DIR_SIGNATURE_FILE
    previous = sig_path.read_text(encoding="utf-8").strip() if sig_path.is_file() else None
    if previous == signature:
        return 0
    purged = 0
    for batch_folder in sorted(work_dir.glob("batch_*")):
        if batch_folder.is_dir():
            shutil.rmtree(batch_folder)
            purged += 1
    work_dir.mkdir(parents=True, exist_ok=True)
    sig_path.write_text(signature, encoding="utf-8")
    if purged:
        logger.warning(
            "[popsim.stage] popsim config changed since the last run in this work_dir "
            "(or signature was absent) -> purged %d stale batch folder(s) so every batch "
            "re-runs with the CURRENT config (prevents stale-batch skips).", purged)
    else:
        logger.info("[popsim.stage] wrote work_dir config signature (no stale batches).")
    return purged


def _frame_content_signature(df):
    """Content signature of a seed/target frame (row values + column layout).

    Hashes the actual VALUES (via pandas' row hash) plus the column names and dtypes.
    Returns ``None`` for ``None`` (an inactive optional input). Used by
    :func:`compute_batch_config_signature` so that any change to the seed tables or the
    per-Kreis control target counts flips the work-dir signature.
    """
    if df is None:
        return None
    row_hash = hashlib.sha256(
        pd.util.hash_pandas_object(df, index=True).values.tobytes()
    ).hexdigest()
    return {
        "columns": [str(c) for c in df.columns],
        "dtypes": [str(t) for t in df.dtypes],
        "n_rows": int(len(df)),
        "rows": row_hash,
    }


def compute_batch_config_signature(*, controls_df, settings_text, max_cells,
                                   stratify_regiostar, source_name, employment_grid_on,
                                   kreis_controls_map, seed_day_filter, seed_households,
                                   seed_persons, kreis_table, active_entries=None,
                                   status_prior_n=0.0) -> str:
    """Compute the work-dir batch-input signature (sha256 hex).

    The signature captures EVERYTHING that determines a batch's inputs, so that a change
    since the last run in the same ``work_dir`` purges the stale completed batches
    (:func:`purge_stale_batches_on_config_change`). Beyond the control set, settings,
    batching and source, it hashes the CONTENT of the seed frames and the per-Kreis
    target table: the seed content captures the full seed identity (weekend_plan_match /
    complete_members / e-bike column / imputation seed all flow into the seed VALUES), and
    ``kreis_table`` captures the per-Kreis control target COUNTS. Hashing content (not just
    the config-knob names) closes the audit gap where editing a ``target2026_*`` CSV or a
    seed toggle in the same work_dir left completed batches silently reused with outdated
    inputs (2026-07-09).
    """
    # NOTE (one-time signature change): hashing (key, census_source) PAIRS instead of
    # just the sorted keys means a catalog composition change (same control names,
    # different census_source columns) now invalidates a persistent work_dir's
    # completed batches exactly once on the next run. Before this fix, a KEY-only
    # hash could not see such a change and silently reused stale batches built
    # against the OLD census_source composition.
    kreis_controls_signature = (
        sorted((key, list(census_source)) for key, census_source in kreis_controls_map.items())
        if kreis_controls_map else None
    )
    payload = {
        "controls": controls_df.to_csv(index=False),
        "settings": settings_text,
        "max_cells": max_cells,
        "stratify_regiostar": stratify_regiostar,
        "source": source_name,
        "employment_grid": employment_grid_on,
        "kreis_controls": kreis_controls_signature,
        "seed_day_filter": str(seed_day_filter),
        "seed_households": _frame_content_signature(seed_households),
        "seed_persons": _frame_content_signature(seed_persons),
        "kreis_targets": _frame_content_signature(kreis_table),
    }
    if active_entries:
        payload["kreis_attribute_controls"] = {
            c.name: (status_prior_n if c.name == "economic_status" else 0.0)
            for c in active_entries
        }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()
