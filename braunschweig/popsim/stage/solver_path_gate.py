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

With ``integerizer_rounding: "controlled"`` (issue #646) the gate also proves, per completed
batch, that the controlled path ran (the extra columns, ``rounding == "controlled"`` on every
simultaneous call, a controlled call wherever there is a parent outcome) and reports what the
method of each call (``search`` or ``lp``) counted; with ``"round"`` a record marked controlled
is refused (:func:`check_solver_paths`).

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
RECORD_RELATIVE_PATH = batch.INTEGERIZER_PATH_RECORD
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
# Controlled rounding (fork release 0.10.0+bs.2, issue #646). The fork adds these columns to the
# record only in controlled mode: the rounding mode and method of a call and, per simultaneous
# call, the counts its method reports. A ``simul`` call row of a controlled run carries
# ``rounding == "controlled"``; the counts are summed in :func:`summarize_controlled_calls`.
# A column that the method of a call does not produce is empty in its row.
CONTROLLED = "controlled"
ROUNDINGS = ("round", CONTROLLED)
METHOD_SEARCH = "search"
METHOD_LP = "lp"
CONTROLLED_METHODS = (METHOD_SEARCH, METHOD_LP)
CONTROLLED_COLUMNS = (
    "rounding", "method", "solver", "attempts", "x_count", "swaps", "sweeps", "converged",
    "distance_start", "distance_final", "flow_wanted", "flow_achieved", "zone_shortfall",
    "row_shortfall", "fractional_x", "repaired_x", "fractional_after_repair", "repair_failed",
    "slack_cell", "slack_parent_sub", "slack_parent", "slack_conserve", "hh_unreachable",
    "hh_outside_lp", "hh_excess", "objective_lp", "objective_repaired",
)
# The numeric columns the gate requires of a call that ended OPTIMAL or FEASIBLE, per method
# (``solver`` is text and not read). The fork leaves a value it could not compute empty (its
# ``populationsim.integerizing.path_record`` module says which): the two LP objectives can be
# empty after a solved LP, so they are not required and never read, and the numbers of a call
# that did not succeed are not read at all (:func:`summarize_controlled_calls`).
_METHOD_NUMERIC_COLUMNS = {
    METHOD_SEARCH: (
        "x_count", "swaps", "sweeps", "converged", "distance_start", "distance_final",
        "flow_wanted", "flow_achieved", "zone_shortfall", "row_shortfall", "hh_unreachable",
        "hh_outside_lp", "hh_excess"),
    METHOD_LP: (
        "attempts", "x_count", "fractional_x", "repaired_x", "fractional_after_repair",
        "repair_failed", "slack_cell", "slack_parent_sub", "slack_parent", "slack_conserve",
        "hh_unreachable", "hh_outside_lp", "hh_excess"),
}
# Totals of summarize_controlled_calls whose positive value makes the rate line a WARNING
# (together with any non-success call): parent weight not placed, round-ups a zone still needed,
# households that no row of the parent can supply, households the final rounding places on rows
# without a residual in that zone (beyond their parent weight), households the integer parts
# already hold beyond a household control (no rounding can remove them: the household controls of
# the levels do not add up, and the household total of the run exceeds its target), and an LP
# whose repair step failed or left fractional values.
_DEGRADED_CONTROLLED_TOTALS = (
    "controlled_non_success_calls", "search_not_converged_calls", "row_shortfall",
    "zone_shortfall", "hh_unreachable", "hh_outside_lp", "hh_excess", "repair_failed",
    "fractional_after_repair")
SUCCESS_STATUSES = ("OPTIMAL", "FEASIBLE")
SIMUL_KIND = "simul"


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
    "single_failed_trace_labels", "call_rows", "per_batch": {batch: {"not_solved_calls",
    "single_failed_smart_round"}}}``; ``call_rows`` is the denominator of the NOT_SOLVED rate
    :func:`check_solver_paths` logs and ``single_failed_trace_labels`` the sorted
    ``<batch>/<trace_label>`` of every single failure. The single failures get no rate: the
    ``single`` call rows include the backstopped sub-zone calls of the simultaneous fallback,
    so they are no denominator of seed-level integerizations.
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
        "single_failed_trace_labels": sorted(
            f"{row.batch}/{row.trace_label}" for row in records[
                (records["row_type"] == "outcome")
                & (records["outcome"] == SINGLE_FAILED_OUTCOME)].itertuples()),
        "call_rows": int((records["row_type"] == "call").sum()),
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


def _percent(count: int, total: int) -> float:
    return 100.0 * count / total if total else 0.0


def validate_expected_rounding(expected_rounding) -> str:
    """Return ``expected_rounding`` when it is ``"round"`` or ``"controlled"``, else ``ValueError``."""
    if expected_rounding not in ROUNDINGS:
        raise ValueError(
            f"expected_rounding must be one of {list(ROUNDINGS)}, got {expected_rounding!r}")
    return expected_rounding


def _batch_missing_controlled_columns(group: pd.DataFrame) -> list:
    """Controlled columns a batch lacks; a column absent from one batch is all-NaN after the concat."""
    return [c for c in CONTROLLED_COLUMNS if c not in group.columns or group[c].isna().all()]


def _prove_controlled_batch(name: str, group: pd.DataFrame) -> None:
    """Raise ``RuntimeError`` naming ``name`` unless this one batch provably ran controlled.

    The batch needs the controlled columns, ``rounding == "controlled"`` on every ``simul`` call
    row, and at least one controlled ``simul`` call when it has a parent outcome row (any outcome
    but ``single_failed_smart_round``).
    """
    missing = _batch_missing_controlled_columns(group)
    if missing:
        raise RuntimeError(
            f"[{STAGE_TAG}] integerizer_rounding is 'controlled' but the integerizer record of "
            f"batch {name} lacks the columns {missing}: the installed PopulationSim is not a build "
            "with controlled rounding (fork release 0.10.0+bs.2 or later) or "
            "INTEGERIZER_ROUNDING never reached it")
    simul = group[(group["row_type"] == "call") & (group["kind"] == SIMUL_KIND)]
    not_controlled = simul[simul["rounding"].fillna("") != CONTROLLED]
    if not not_controlled.empty:
        raise RuntimeError(
            f"[{STAGE_TAG}] integerizer_rounding is 'controlled' but {len(not_controlled)} of "
            f"{len(simul)} simultaneous integerizer calls of batch {name} were not recorded as "
            f"controlled (first: trace_label {not_controlled.iloc[0]['trace_label']!r}, rounding "
            f"{not_controlled.iloc[0]['rounding']!r}): the controlled path did not run")
    parents = group[(group["row_type"] == "outcome") & (group["outcome"] != SINGLE_FAILED_OUTCOME)]
    if len(parents) and simul.empty:
        raise RuntimeError(
            f"[{STAGE_TAG}] integerizer_rounding is 'controlled' but batch {name} has "
            f"{len(parents)} parent outcome rows and no simultaneous call recorded as controlled: "
            "the controlled path did not run")


def validate_expected_method(expected_method) -> Optional[str]:
    """Return ``expected_method`` when it is ``None`` or a controlled method, else ``ValueError``."""
    if expected_method is not None and expected_method not in CONTROLLED_METHODS:
        raise ValueError(
            f"expected_method must be None or one of {list(CONTROLLED_METHODS)}, "
            f"got {expected_method!r}")
    return expected_method


def _prove_controlled_method(name: str, calls: pd.DataFrame, expected_method: str) -> None:
    """Raise ``RuntimeError`` naming batch ``name`` when a controlled call recorded another method."""
    other = calls[calls["method"].fillna("") != expected_method]
    if not other.empty:
        raise RuntimeError(
            f"[{STAGE_TAG}] controlled_method is {expected_method!r} but {len(other)} of "
            f"{len(calls)} controlled simultaneous integerizer calls of batch {name} recorded "
            f"another method (first: trace_label {other.iloc[0]['trace_label']!r}, method "
            f"{other.iloc[0]['method']!r}, expected {expected_method!r}): the configured method "
            "did not run")


def summarize_controlled_calls(
    records: pd.DataFrame, expected_rounding: str = "round",
    expected_method: Optional[str] = None,
) -> dict:
    """Totals of the controlled simultaneous calls; fail when the mode and the record disagree.

    With ``expected_rounding == "controlled"`` EVERY batch must prove it (``RuntimeError`` naming
    the batch otherwise): it carries the controlled columns, every ``simul`` call row has
    ``rounding == "controlled"``, and it has at least one controlled ``simul`` call when it has a
    parent outcome row (an older PopulationSim build, or a setting that never arrived, fails here).
    Each controlled call row must name a known ``method``. A call that ended OPTIMAL or FEASIBLE
    (a "successful" call) must carry a number in every column of ``_METHOD_NUMERIC_COLUMNS`` for
    its method (``converged`` is 0 or 1); a column the method does not produce is empty and never
    read. A violation is a damaged record (``RuntimeError``). A call with any other status is
    counted and nothing else: PopulationSim does not use its result (the sequential path
    integerizes that parent, which the outcome rows show), so its numbers are not read and enter
    no sum and no share.
    With ``"round"`` a row marked ``controlled`` is a mode mismatch (the tracked config says round,
    the tool ran controlled) and raises, naming the batch; a record without the columns (bs.1
    shape) gives zeros.
    With ``expected_rounding == "controlled"`` and an ``expected_method`` (``"search"`` or
    ``"lp"``), every controlled simultaneous call row of every batch must record that method
    (``RuntimeError`` naming the batch otherwise); ``None`` does not check the method. An
    ``expected_method`` that is neither raises ``ValueError``.

    Returns the keys of :func:`_empty_controlled_totals`. Counts over all controlled calls:
    ``controlled_simul_calls``, ``search_calls``, ``lp_calls`` and
    ``controlled_non_success_calls`` (status neither OPTIMAL nor FEASIBLE). Over the successful
    calls only: ``search_not_converged_calls``, the sums over the ``search`` calls (``swaps``,
    ``flow_wanted``, ``flow_achieved``, ``zone_shortfall``, ``row_shortfall``, ``distance_start``,
    ``distance_final``), the sums over the ``lp`` calls (``x_count``, ``fractional_x``,
    ``fractional_after_repair``, ``repair_failed``, ``slack_cell``, ``slack_parent_sub``,
    ``slack_parent``, ``slack_conserve``, ``lp_calls_with_retries`` = calls with ``attempts`` > 1),
    the sums over both (``hh_unreachable``, ``hh_outside_lp``, ``hh_excess``) and three shares:
    ``conservation_loss_share`` = ``row_shortfall`` / ``flow_wanted`` (search calls, parent weight
    that could not be placed), ``lp_conserve_slack_share`` = ``slack_conserve`` / ``x_count`` (lp
    calls) and ``not_converged_share`` = not converged / successful search calls; each is 0.0
    when its denominator is 0.
    """
    expected_rounding = validate_expected_rounding(expected_rounding)
    expected_method = validate_expected_method(expected_method)
    totals = _empty_controlled_totals()
    if expected_rounding != CONTROLLED:
        if "rounding" in records.columns:
            marked = records[records["rounding"].fillna("") == CONTROLLED]
            if not marked.empty:
                raise RuntimeError(
                    f"[{STAGE_TAG}] integerizer_rounding is 'round' but the integerizer record has "
                    f"{len(marked)} rows recorded as controlled in batch(es) "
                    f"{sorted(set(marked['batch']))}: the tool ran a different rounding mode than "
                    "the tracked config")
        return totals
    for name, group in records.groupby("batch", sort=True):
        _prove_controlled_batch(name, group)
    simul = records[(records["row_type"] == "call") & (records["kind"] == SIMUL_KIND)]
    controlled = simul[simul["rounding"].fillna("") == CONTROLLED]
    if controlled.empty:
        raise RuntimeError(
            f"[{STAGE_TAG}] integerizer_rounding is 'controlled' but the integerizer record has no "
            "controlled simultaneous call row: the controlled path did not run")
    method = controlled["method"].fillna("")
    unknown = controlled[~method.isin(CONTROLLED_METHODS)]
    if not unknown.empty:
        raise RuntimeError(
            f"[{STAGE_TAG}] controlled call rows with an unknown method in {RECORD_RELATIVE_PATH} "
            f"(first: batch {unknown.iloc[0]['batch']}, trace_label "
            f"{unknown.iloc[0]['trace_label']!r}, method {unknown.iloc[0]['method']!r}; expected "
            f"one of {list(CONTROLLED_METHODS)}): the fork's controlled record changed or is damaged")
    if expected_method is not None:
        for name, calls in controlled.groupby("batch", sort=True):
            _prove_controlled_method(name, calls, expected_method)
    # Only a successful call has a result PopulationSim used; the numbers of the others are not
    # read (the fork leaves some of them empty) and the call is counted as a non-success.
    succeeded = controlled["status"].isin(SUCCESS_STATUSES)
    numbers = {
        name: _parse_method_numbers(controlled[succeeded & (method == name)], name)
        for name in CONTROLLED_METHODS}
    search, lp = numbers[METHOD_SEARCH], numbers[METHOD_LP]
    searched = len(search)
    not_converged = int((search["converged"] == 0).sum())
    totals.update({
        "controlled_simul_calls": len(controlled),
        "search_calls": int((method == METHOD_SEARCH).sum()),
        "lp_calls": int((method == METHOD_LP).sum()),
        "controlled_non_success_calls": int((~succeeded).sum()),
        "search_not_converged_calls": not_converged,
        "swaps": int(search["swaps"].sum()),
        "flow_wanted": float(search["flow_wanted"].sum()),
        "flow_achieved": float(search["flow_achieved"].sum()),
        "zone_shortfall": float(search["zone_shortfall"].sum()),
        "row_shortfall": float(search["row_shortfall"].sum()),
        "distance_start": float(search["distance_start"].sum()),
        "distance_final": float(search["distance_final"].sum()),
        "x_count": float(lp["x_count"].sum()),
        "fractional_x": float(lp["fractional_x"].sum()),
        "fractional_after_repair": float(lp["fractional_after_repair"].sum()),
        "repair_failed": float(lp["repair_failed"].sum()),
        "slack_cell": float(lp["slack_cell"].sum()),
        "slack_parent_sub": float(lp["slack_parent_sub"].sum()),
        "slack_parent": float(lp["slack_parent"].sum()),
        "slack_conserve": float(lp["slack_conserve"].sum()),
        "lp_calls_with_retries": int((lp["attempts"] > 1).sum()),
        "hh_unreachable": int(search["hh_unreachable"].sum() + lp["hh_unreachable"].sum()),
        "hh_outside_lp": int(search["hh_outside_lp"].sum() + lp["hh_outside_lp"].sum()),
        "hh_excess": int(search["hh_excess"].sum() + lp["hh_excess"].sum()),
    })
    if totals["flow_wanted"] > 0:
        totals["conservation_loss_share"] = totals["row_shortfall"] / totals["flow_wanted"]
    if totals["x_count"] > 0:
        totals["lp_conserve_slack_share"] = totals["slack_conserve"] / totals["x_count"]
    if searched:
        totals["not_converged_share"] = not_converged / searched
    return totals


def _empty_controlled_totals() -> dict:
    """The totals of :func:`summarize_controlled_calls` for a record without controlled calls."""
    return {
        "controlled_simul_calls": 0, "search_calls": 0, "lp_calls": 0,
        "controlled_non_success_calls": 0, "search_not_converged_calls": 0, "swaps": 0,
        "flow_wanted": 0.0, "flow_achieved": 0.0, "zone_shortfall": 0.0, "row_shortfall": 0.0,
        "distance_start": 0.0, "distance_final": 0.0, "x_count": 0.0, "fractional_x": 0.0,
        "fractional_after_repair": 0.0, "repair_failed": 0.0, "slack_cell": 0.0,
        "slack_parent_sub": 0.0, "slack_parent": 0.0, "slack_conserve": 0.0,
        "lp_calls_with_retries": 0, "hh_unreachable": 0, "hh_outside_lp": 0, "hh_excess": 0,
        "conservation_loss_share": 0.0, "lp_conserve_slack_share": 0.0,
        "not_converged_share": 0.0,
    }


def _parse_method_numbers(rows: pd.DataFrame, method: str) -> pd.DataFrame:
    """Numeric columns of the successful controlled call rows of one ``method``.

    Only the columns of ``_METHOD_NUMERIC_COLUMNS`` for ``method`` are read (the others are empty
    by contract or not required). An empty or non-numeric value in one of them, or a
    ``converged`` that is not 0 or 1, is a damaged record (``RuntimeError``). ``rows`` holds only
    calls that ended OPTIMAL or FEASIBLE and may be empty.
    """
    columns = _METHOD_NUMERIC_COLUMNS[method]
    numbers = pd.DataFrame(
        {c: pd.to_numeric(rows[c].fillna(""), errors="coerce") for c in columns},
        index=rows.index)
    bad = numbers.isna()
    if "converged" in numbers.columns:
        bad["converged"] = bad["converged"] | ~numbers["converged"].isin([0, 1])
    if bad.any().any():
        column = str(bad.columns[bad.any()][0])
        first = rows[bad[column]].iloc[0]
        raise RuntimeError(
            f"[{STAGE_TAG}] controlled call rows of method {method!r} with a missing or invalid "
            f"{column!r} in {RECORD_RELATIVE_PATH} (first: batch {first['batch']}, trace_label "
            f"{first['trace_label']!r}, value {first[column]!r}): the fork's controlled record "
            "changed or is damaged")
    return numbers


def check_solver_paths(work_dir, error_rate: Optional[float],
                       expected_rounding: str = "round",
                       expected_method: Optional[str] = None) -> tuple:
    """Log one rate line per geography and fail above ``error_rate`` (``None``: warn only).

    Returns ``(summary, call_totals)``: the :func:`summarize_solver_paths` frame and the
    :func:`summarize_call_totals` dict extended by the :func:`summarize_controlled_calls`
    totals. With ``expected_rounding="controlled"`` it also logs one line with the controlled
    calls by method, the non-success and non-converged counts, the conservation loss, the sums of
    the search and of the LP method and the household counts the method could not place (WARNING
    when a controlled call is neither OPTIMAL nor FEASIBLE, a ``search`` call did not converge, or
    ``row_shortfall``, ``zone_shortfall``, ``hh_unreachable``, ``hh_outside_lp``, ``hh_excess``,
    ``repair_failed`` or ``fractional_after_repair`` is positive, else INFO). Raises
    ``RuntimeError`` when a geography's non-simultaneous share exceeds ``error_rate``, a completed
    batch has no usable record, a batch of a controlled run cannot prove the controlled path, a
    controlled call recorded another method than ``expected_method`` (``None``: not checked), or a
    ``round`` run has a record marked controlled; ``ValueError`` for an ``error_rate`` outside
    (0, 1], an unknown ``expected_rounding`` or an unknown ``expected_method``.
    """
    error_rate = validate_error_rate(error_rate)
    expected_rounding = validate_expected_rounding(expected_rounding)
    expected_method = validate_expected_method(expected_method)
    records = read_records(Path(work_dir))
    summary = summarize_solver_paths(records)
    totals = summarize_call_totals(records)
    controlled = summarize_controlled_calls(records, expected_rounding, expected_method)
    totals.update(controlled)
    if expected_rounding == CONTROLLED:
        degraded = any(controlled[name] for name in _DEGRADED_CONTROLLED_TOTALS)
        logger.log(
            logging.WARNING if degraded else logging.INFO,
            "[%s] controlled rounding: %d controlled simultaneous calls (search %d, lp %d), "
            "%d not OPTIMAL/FEASIBLE (counted only, the sums are over the successful calls); "
            "search not converged %d (%.1f%% of the successful search calls); conservation loss "
            "%.2f%% (row_shortfall %g of flow_wanted %g); zone_shortfall %g; swaps %d; "
            "distance %g -> %g; flow achieved %g; lp: x %g, fractional %g, after repair %g, "
            "repair_failed %g, attempts>1 %d, conserve slack %.1f%% (%g), slack cell %g, "
            "parent_sub %g, parent %g; hh_unreachable %d, hh_outside_lp %d, hh_excess %d",
            STAGE_TAG, controlled["controlled_simul_calls"], controlled["search_calls"],
            controlled["lp_calls"], controlled["controlled_non_success_calls"],
            controlled["search_not_converged_calls"], 100.0 * controlled["not_converged_share"],
            100.0 * controlled["conservation_loss_share"], controlled["row_shortfall"],
            controlled["flow_wanted"], controlled["zone_shortfall"], controlled["swaps"],
            controlled["distance_start"], controlled["distance_final"],
            controlled["flow_achieved"], controlled["x_count"], controlled["fractional_x"],
            controlled["fractional_after_repair"], controlled["repair_failed"],
            controlled["lp_calls_with_retries"], 100.0 * controlled["lp_conserve_slack_share"],
            controlled["slack_conserve"], controlled["slack_cell"], controlled["slack_parent_sub"],
            controlled["slack_parent"], controlled["hh_unreachable"], controlled["hh_outside_lp"],
            controlled["hh_excess"])
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
    # Fallback transparency: a call that ended NOT_SOLVED or a single integerizer that ended
    # in smart rounding is a degraded result; any occurrence is a WARNING with its rate.
    not_solved, single_failed = totals["not_solved_calls"], totals[SINGLE_FAILED_OUTCOME]
    logger.log(
        logging.WARNING if (not_solved or single_failed) else logging.INFO,
        "[%s] NOT_SOLVED integerizer calls: %d/%d call rows (%.1f%%); single_failed_smart_round "
        "(single integerizer ended without success; a count, no rate): %d %s",
        STAGE_TAG, not_solved, totals["call_rows"],
        _percent(not_solved, totals["call_rows"]), single_failed,
        totals["single_failed_trace_labels"])
    if exceeded:
        raise RuntimeError(
            f"[{STAGE_TAG}] non-simultaneous share above solver_fallback_error_rate "
            f"{100.0 * error_rate:.1f}%: {', '.join(exceeded)} -- the simultaneous integerizer "
            "is not the path most zones took; inspect <batch>/output/integerizer_paths.csv")
    return summary, totals
