"""Solver-path gate of the popsim_mid stage (design goal G5, issue #645).

The group's PopulationSim fork records, with ``INTEGERIZER_PATH_RECORD: true`` (the stage
always switches it on), one file per batch, ``<batch>/output/integerizer_paths.csv``: one
``call`` row per integerizer LP call and one ``outcome`` row per parent zone of the
simultaneous integerizer. This module turns those files into one primary-vs-fallback rate per
geography (``braunschweig.popsim.fallback_rates``) and, when the configured error rate is
set, fails the run above it. The log-based ``_log_integerizer_feasibility`` of the stage
stays as it is; this gate adds the solver PATH (which fallback, per geography).

Definitions (the WP1 definition): PRIMARY = ``simultaneous`` + ``retry_simultaneous``;
FALLBACK (``non_simultaneous``) = the three ``sequential_*`` outcomes +
``smart_round_all_subzones``. ``single_failed_smart_round`` (single integerizer, final
status not a success) is counted separately and not part of the rate. It counts only plain
single-integerizer failures: the fork does not write it for sub-zone failures inside the
simultaneous fallback, those are in ``rounded_zones``.

Reads files only and may raise; the config key is volatile (a changed threshold takes effect
only when the stage executes).
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

import pandas as pd

from braunschweig.popsim import batch
from braunschweig.popsim.fallback_rates import log_fallback_rate

logger = logging.getLogger(__name__)

STAGE_TAG = "popsim.solver_paths"
RECORD_RELATIVE_PATH = "output/integerizer_paths.csv"
PRIMARY_OUTCOMES = ("simultaneous", "retry_simultaneous")
SEQUENTIAL_OUTCOMES = (
    "sequential_no_infeasible_subzones",
    "sequential_single_feasible_subzone",
    "sequential_retry_failed",
)
FALLBACK_OUTCOMES = SEQUENTIAL_OUTCOMES + ("smart_round_all_subzones",)
SINGLE_FAILED_OUTCOME = "single_failed_smart_round"
ALL_OUTCOMES = PRIMARY_OUTCOMES + FALLBACK_OUTCOMES + (SINGLE_FAILED_OUTCOME,)
NOT_SOLVED_STATUS = "NOT_SOLVED"
ROW_TYPES = ("call", "outcome")
_COLUMNS = (
    "row_type", "kind", "geography", "trace_label", "status", "zones", "rows", "build_s",
    "solve_s", "outcome", "rounded_zones", "draw", "random_seed",
)


def validate_error_rate(error_rate) -> Optional[float]:
    """Return ``error_rate`` as float, or ``None``; ``ValueError`` outside (0, 1]."""
    if error_rate is None:
        return None
    try:
        value = float(error_rate)
    except (TypeError, ValueError):
        raise ValueError(
            f"solver_fallback_error_rate must be null or a float in (0, 1], got {error_rate!r}"
        ) from None
    if not 0.0 < value <= 1.0:
        raise ValueError(f"solver_fallback_error_rate must be null or in (0, 1], got {error_rate!r}")
    return value


def read_records(work_dir: Path) -> pd.DataFrame:
    """Concatenate the records of all completed batches; raise when one is missing or empty."""
    frames = []
    for folder in sorted(Path(work_dir).glob("batch_*")):
        if not folder.is_dir() or not batch.is_completed(folder):
            continue
        record = folder / RECORD_RELATIVE_PATH
        if not record.is_file():
            raise RuntimeError(
                f"[{STAGE_TAG}] completed batch {folder.name} has no {RECORD_RELATIVE_PATH}: the "
                "stage always switches INTEGERIZER_PATH_RECORD on, so the installed PopulationSim "
                "is not the group's fork build or the record could not be written")
        frame = pd.read_csv(record, dtype=str, keep_default_na=False)
        missing = [c for c in _COLUMNS if c not in frame.columns]
        if missing:
            raise RuntimeError(
                f"[{STAGE_TAG}] {folder.name}/{RECORD_RELATIVE_PATH} lacks columns {missing}")
        if frame.empty:
            raise RuntimeError(
                f"[{STAGE_TAG}] completed batch {folder.name} has an empty {RECORD_RELATIVE_PATH}: "
                "no integerizer call was recorded")
        bad_type = sorted(set(frame["row_type"]) - set(ROW_TYPES))
        if bad_type:
            raise RuntimeError(
                f"[{STAGE_TAG}] {folder.name}/{RECORD_RELATIVE_PATH} has unknown row_type "
                f"{bad_type}: expected one of {list(ROW_TYPES)}")
        bad_outcome = sorted(set(frame.loc[frame["row_type"] == "outcome", "outcome"])
                             - set(ALL_OUTCOMES))
        if bad_outcome:
            raise RuntimeError(
                f"[{STAGE_TAG}] {folder.name}/{RECORD_RELATIVE_PATH} has unknown outcome "
                f"{bad_outcome}: expected one of {list(ALL_OUTCOMES)} (fork record changed?)")
        outcomes = frame[frame["row_type"] == "outcome"]
        # Each parent zone has exactly one outcome per run (single failures: keyed by trace_label
        # alone, no geography). The fork appends to the file, so a repeated or resumed run into
        # a non-empty output dir shows up as a duplicate key.
        key_geography = outcomes["geography"].where(
            outcomes["outcome"] != SINGLE_FAILED_OUTCOME, "")
        duplicated = outcomes.assign(_geo=key_geography).duplicated(
            subset=["_geo", "trace_label"], keep=False)
        if duplicated.any():
            first = outcomes[duplicated].iloc[0]
            raise RuntimeError(
                f"[{STAGE_TAG}] {folder.name}/{RECORD_RELATIVE_PATH} has {int(duplicated.sum())} "
                f"outcome rows sharing a (geography, trace_label) key, e.g. "
                f"({first['geography']!r}, {first['trace_label']!r}): the fork appends to the file, "
                "so PopulationSim ran more than once into this output dir; delete the batch "
                "output and re-run it")
        frame["batch"] = folder.name
        frames.append(frame)
    if not frames:
        return pd.DataFrame(columns=list(_COLUMNS))
    return pd.concat(frames, ignore_index=True)


def summarize_call_totals(records: pd.DataFrame) -> dict:
    """Run totals and per-batch counts of NOT_SOLVED call rows and single integerizer failures.

    Call rows carry no reliable geography (the single integerizer does not know it), so these
    are never grouped by geography. Returns ``{"not_solved_calls", "single_failed_smart_round",
    "per_batch": {batch: {"not_solved_calls", "single_failed_smart_round"}}}``.
    """
    per_batch = {}
    for name, group in records.groupby("batch"):
        per_batch[name] = {
            "not_solved_calls": int(((group["row_type"] == "call")
                                     & (group["status"] == NOT_SOLVED_STATUS)).sum()),
            SINGLE_FAILED_OUTCOME: int(((group["row_type"] == "outcome")
                                        & (group["outcome"] == SINGLE_FAILED_OUTCOME)).sum()),
        }
    return {
        "not_solved_calls": sum(v["not_solved_calls"] for v in per_batch.values()),
        SINGLE_FAILED_OUTCOME: sum(v[SINGLE_FAILED_OUTCOME] for v in per_batch.values()),
        "per_batch": per_batch,
    }


def summarize_solver_paths(records: pd.DataFrame) -> pd.DataFrame:
    """One row per ``geography`` from the OUTCOME rows of ``records`` (see :func:`read_records`).

    Columns: ``geography``, ``parents`` (outcome rows of the simultaneous integerizer), one
    count column per outcome (incl. ``single_failed_smart_round``), ``rounded_zones`` and
    ``non_simultaneous``. Raises ``RuntimeError`` when there is no outcome row at all: production
    always runs the simultaneous integerizer, so none means a wrong PopulationSim build or
    USE_SIMUL_INTEGERIZER off, indistinguishable from a broken record.
    """
    outcome_rows = records[records["row_type"] == "outcome"]
    if outcome_rows.empty:
        raise RuntimeError(
            f"[{STAGE_TAG}] the simultaneous integerizer wrote no parent outcome in any completed "
            "batch: wrong PopulationSim build or USE_SIMUL_INTEGERIZER off")
    rows = []
    for geography in sorted(set(outcome_rows["geography"])):
        outcomes = outcome_rows[outcome_rows["geography"] == geography]
        counts = {name: int((outcomes["outcome"] == name).sum()) for name in ALL_OUTCOMES}
        rounded = pd.to_numeric(outcomes["rounded_zones"], errors="coerce").fillna(0)
        rows.append({
            "geography": geography,
            "parents": sum(counts[n] for n in PRIMARY_OUTCOMES + FALLBACK_OUTCOMES),
            **counts,
            "rounded_zones": int(rounded.sum()),
            "non_simultaneous": sum(counts[n] for n in FALLBACK_OUTCOMES),
        })
    columns = ["geography", "parents", *ALL_OUTCOMES, "rounded_zones", "non_simultaneous"]
    return pd.DataFrame(rows, columns=columns)


def check_solver_paths(work_dir, error_rate: Optional[float]) -> tuple:
    """Log one rate line per geography and fail above ``error_rate`` (``None``: warn only).

    Returns ``(summary, call_totals)``: the :func:`summarize_solver_paths` frame and the
    :func:`summarize_call_totals` dict. Raises ``RuntimeError`` when a geography's
    non-simultaneous share exceeds ``error_rate`` or a completed batch has no usable record,
    ``ValueError`` for an ``error_rate`` outside (0, 1].
    """
    error_rate = validate_error_rate(error_rate)
    records = read_records(Path(work_dir))
    summary = summarize_solver_paths(records)
    totals = summarize_call_totals(records)
    exceeded = []
    for row in summary.itertuples(index=False):
        if row.parents == 0:
            logger.warning("[%s] %s: no parent zone recorded (only single-integerizer outcomes)",
                           STAGE_TAG, row.geography)
            continue
        what = (f"{row.geography}: parent zones not integerized simultaneously "
                f"(sequential or smart-rounded; {row.rounded_zones} sub zones smart-rounded)")
        share = log_fallback_rate(
            logger, STAGE_TAG, row.non_simultaneous, row.parents, what=what)
        if error_rate is not None and share > error_rate:
            exceeded.append(f"{row.geography} {100.0 * share:.1f}%")
    logger.info(
        "[%s] NOT_SOLVED integerizer calls: %d; single_failed_smart_round "
        "(single integerizer ended without success): %d", STAGE_TAG, totals["not_solved_calls"],
        totals[SINGLE_FAILED_OUTCOME])
    if exceeded:
        raise RuntimeError(
            f"[{STAGE_TAG}] non-simultaneous share above solver_fallback_error_rate "
            f"{100.0 * error_rate:.1f}%: {', '.join(exceeded)} -- the simultaneous integerizer "
            "is not the path most zones took; inspect <batch>/output/integerizer_paths.csv")
    return summary, totals
