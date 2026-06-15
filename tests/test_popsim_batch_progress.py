"""Regression guard: run_batches aggregation must be unchanged after progress_parallel wiring.

BatchResult has 4 fields: folder, status, message, duration_s.
"""
from braunschweig.popsim import batch as B


def _fake_run_one(folder):
    status = "succeeded" if "ok" in folder else ("skipped" if "skip" in folder else "failed")
    return B.BatchResult(folder, status, "msg", None)


def test_run_batches_aggregation_unchanged_with_progress(capsys):
    folders = ["ok1", "ok2", "skip1", "failX"]
    summary = B.run_batches(folders, _fake_run_one, num_workers=2)
    assert summary.n_total == 4
    assert summary.n_succeeded == 2 and summary.n_skipped == 1 and summary.n_failed == 1
    assert len(summary.results) == 4
