"""Tests for the fast-path rate of the PopulationSim balancers
(``braunschweig.popsim.stage.fast_path_rate``, issue #656).

With ``fast_paths`` on, the fork ends each batch log with one summary line of its balancer calls.
The log files are written by hand here.
"""
from __future__ import annotations

import logging

from braunschweig.popsim import batch
from braunschweig.popsim.stage import fast_path_rate


def _summary(fast, calls, value_cap=0, svml=0, non_finite=0, rerun=0):
    share = 100.0 * fast / calls
    return (
        "INFO - populationsim.balancing.balancers_numba - balancer fast path: fast "
        f"{fast} of {calls} calls ({share:.1f} %), fallback value cap {value_cap}, fallback SVML "
        f"{svml}, fallback non-finite {non_finite}, re-run after the delta guard {rerun}")


def _batch(work_dir, name, log_lines, *, completed=True):
    out = work_dir / name / "output"
    out.mkdir(parents=True)
    if completed:
        (work_dir / name / batch.COMPLETION_MARKER).write_text("x", encoding="utf-8")
    (out / "populationsim.log").write_text("\n".join(log_lines) + "\n", encoding="utf-8")


def _warnings(caplog):
    return [r for r in caplog.records
            if r.name == fast_path_rate.logger.name and r.levelno >= logging.WARNING]


def test_fast_path_rate_sums_the_completed_batches(tmp_path, caplog):
    _batch(tmp_path, "batch_001", ["INFO - x - start", _summary(32, 32)])
    _batch(tmp_path, "batch_002", [_summary(28, 30, value_cap=1, rerun=1), "INFO - x - end"])
    _batch(tmp_path, "batch_003", [_summary(5, 9, svml=4)], completed=False)  # not completed: ignored
    with caplog.at_level(logging.INFO):
        totals = fast_path_rate.check_fast_path(tmp_path, True)
    assert totals == {
        "enabled": True, "batches": 2, "batches_without_summary": [], "calls": 62, "fast_calls": 60,
        "fallback_value_cap": 1, "fallback_svml": 0, "fallback_non_finite": 0,
        "rerun_after_delta_guard": 1, "fallback_share": 2 / 62}
    # 2 of 62 calls off the fast kernel is below the warning threshold of the rate helper.
    assert not _warnings(caplog)


def test_a_completed_batch_without_the_summary_is_reported(tmp_path, caplog):
    # fast_paths is on but one batch never used the fast kernel (numba off, or an older build).
    _batch(tmp_path, "batch_001", [_summary(32, 32)])
    _batch(tmp_path, "batch_002", ["INFO - x - no balancer summary here"])
    with caplog.at_level(logging.INFO):
        totals = fast_path_rate.check_fast_path(tmp_path, True)
    assert totals["batches"] == 2 and totals["batches_without_summary"] == ["batch_002"]
    assert totals["calls"] == 32 and totals["fast_calls"] == 32
    assert _warnings(caplog)


def test_most_calls_off_the_fast_kernel_is_a_warning(tmp_path, caplog):
    _batch(tmp_path, "batch_001", [_summary(2, 32, svml=30)])
    with caplog.at_level(logging.INFO):
        totals = fast_path_rate.check_fast_path(tmp_path, True)
    assert totals["fallback_svml"] == 30 and totals["fallback_share"] == 30 / 32
    assert _warnings(caplog)


def test_with_the_option_off_no_log_is_read(tmp_path, caplog):
    with caplog.at_level(logging.INFO):
        totals = fast_path_rate.check_fast_path(tmp_path / "missing", False)
    assert totals["enabled"] is False and totals["calls"] == 0 and totals["batches"] == 0
    assert not caplog.records
