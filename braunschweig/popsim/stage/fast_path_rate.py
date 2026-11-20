"""Fast-path rate of the PopulationSim balancers of the popsim_mid stage (issue #656).

With ``fast_paths`` on, the group's PopulationSim fork runs lookup-table kernels of its numba
balancers and falls back to the original kernel, call by call, in the cases its README lists
(section "Run time and memory"). A fallback gives the same weights and only costs time, but an
unseen one would mean that the option silently does nothing. The fork ends the log of every run
with one summary line of its balancer calls; this module sums that line over the completed
batches and logs the primary-vs-fallback rate (``braunschweig.popsim.fallback_rates``).

Reads files only. It does not raise for a missing line: a completed batch without one is logged
as a WARNING and listed in the totals. It raises ``ValueError`` for a damaged line (more fast
calls than calls).
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

from braunschweig.popsim import batch
from braunschweig.popsim.fallback_rates import log_fallback_rate

logger = logging.getLogger(__name__)

STAGE_TAG = "popsim.fast_paths"
BATCH_LOG_RELATIVE_PATH = "output/populationsim.log"
# The summary line of populationsim.balancing.balancers_numba.log_fast_path_rate of the fork.
_SUMMARY = re.compile(
    r"balancer fast path: fast (\d+) of (\d+) calls \([^)]*\), fallback value cap (\d+), "
    r"fallback SVML (\d+), fallback non-finite (\d+), re-run after the delta guard (\d+)")
_COUNTS = ("fast_calls", "calls", "fallback_value_cap", "fallback_svml", "fallback_non_finite",
           "rerun_after_delta_guard")


def _empty_totals(enabled: bool) -> dict:
    return {"enabled": enabled, "batches": 0, "batches_without_summary": [], "calls": 0,
            "fast_calls": 0, "fallback_value_cap": 0, "fallback_svml": 0,
            "fallback_non_finite": 0, "rerun_after_delta_guard": 0, "fallback_share": 0.0}


def check_fast_path(work_dir, enabled: bool) -> dict:
    """Sum the balancer fast-path summaries of the completed batches and log the rate.

    With ``enabled`` false (``fast_paths`` off) nothing is read or logged and the totals are
    zero. Otherwise the LAST summary line of every completed batch's ``populationsim.log`` is
    read (a resumed PopulationSim run appends to its log). Returns ``enabled``, ``batches``
    (completed batches), ``batches_without_summary`` (their names, sorted), ``calls`` and
    ``fast_calls`` (balancer calls, and those whose result came from the fast kernel), the four
    fallback counts of the fork's line, and ``fallback_share`` = ``(calls - fast_calls) / calls``
    (0.0 without calls).

    One rate line is logged (WARNING above the threshold of
    :func:`braunschweig.popsim.fallback_rates.log_fallback_rate`), and one WARNING when a
    completed batch has no summary line: the fork writes it whenever a fast kernel was called,
    so its absence means numba is off in the PopulationSim settings or the installed build does
    not know the option.

    Raises
    ------
    ValueError
        If the summary line of a batch reports more fast calls than calls (damaged line or a
        changed format); the message names the batch and its log file.
    """
    totals = _empty_totals(bool(enabled))
    if not enabled:
        return totals
    for folder in sorted(Path(work_dir).glob("batch_*")):
        if not folder.is_dir() or not batch.is_completed(folder):
            continue
        totals["batches"] += 1
        log_path = folder / BATCH_LOG_RELATIVE_PATH
        matches = (_SUMMARY.findall(log_path.read_text(encoding="utf-8", errors="replace"))
                   if log_path.is_file() else [])
        if not matches:
            totals["batches_without_summary"].append(folder.name)
            continue
        counts = dict(zip(_COUNTS, (int(value) for value in matches[-1])))
        if counts["fast_calls"] > counts["calls"]:
            raise ValueError(
                f"[{STAGE_TAG}] batch {folder.name}: the balancer summary in {log_path} reports "
                f"{counts['fast_calls']} fast calls of {counts['calls']} calls: the line is "
                "damaged or its format changed")
        for name, value in counts.items():
            totals[name] += value
    not_fast = totals["calls"] - totals["fast_calls"]
    totals["fallback_share"] = log_fallback_rate(
        logger, STAGE_TAG, not_fast, totals["calls"],
        what=("PopulationSim balancer calls not answered by the fast kernel (value cap "
              f"{totals['fallback_value_cap']}, SVML {totals['fallback_svml']}, non-finite "
              f"{totals['fallback_non_finite']}, re-run after the delta guard "
              f"{totals['rerun_after_delta_guard']}); the original kernel ran, same weights"))
    if totals["batches_without_summary"]:
        logger.warning(
            "[%s] fast_paths is on but %d of %d completed batches report no fast balancer call "
            "(%s): numba is off in the PopulationSim settings or the installed build does not "
            "know the option", STAGE_TAG, len(totals["batches_without_summary"]),
            totals["batches"], totals["batches_without_summary"])
    return totals
