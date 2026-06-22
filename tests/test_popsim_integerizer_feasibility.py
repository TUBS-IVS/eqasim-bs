"""Tests for the PopulationSim integerizer feasibility summary (no-silent-fallback).

``summarize_integerizer_feasibility`` parses the per-batch ``populationsim.log``
files and aggregates how often the LP integerizer fell back to smart-rounding
(status INFEASIBLE), so the popsim stage can surface that rate instead of leaving
it buried in the batch logs.
"""
from braunschweig.popsim import mid


# Minimal log fragments mirroring the real PopulationSim wording.
_OPTIMAL_LINE = (
    "DEBUG - populationsim.integerizing.wrappers - Integerizer status for "
    "unbackstopped STAAT_1_ZENSUS1km_CRS3035RES1000mN3215000E4349000: OPTIMAL\n"
)
_INFEASIBLE_LINE = (
    "ERROR - populationsim.integerizing.wrappers - Integerizer failed for "
    "STAAT_1_ZENSUS1km_CRS3035RES1000mN3215000E4342000 status INFEASIBLE. "
    "Returning smart-rounded original weights\n"
)
_RETRY_FAILED_LINE = (
    "ERROR - populationsim.integerizing.wrappers - do_simul_integerizing retry "
    "failed for STAAT_1 status INFEASIBLE.\n"
)


def _write_batch_log(work_dir, batch_name, *, n_optimal, n_infeasible, n_retry):
    out_dir = work_dir / batch_name / "output"
    out_dir.mkdir(parents=True, exist_ok=True)
    text = (
        _OPTIMAL_LINE * n_optimal
        + _INFEASIBLE_LINE * n_infeasible
        + _RETRY_FAILED_LINE * n_retry
    )
    (out_dir / "populationsim.log").write_text(text, encoding="utf-8")


def test_counts_optimal_infeasible_and_retry_across_batches(tmp_path):
    _write_batch_log(tmp_path, "batch_000", n_optimal=90, n_infeasible=10, n_retry=1)
    _write_batch_log(tmp_path, "batch_001", n_optimal=100, n_infeasible=0, n_retry=0)

    feas = mid.summarize_integerizer_feasibility(tmp_path)

    assert feas["n_logs"] == 2
    assert feas["n_optimal"] == 190
    assert feas["n_infeasible"] == 10
    assert feas["n_simul_retry_failed"] == 1
    assert feas["n_total"] == 200
    assert abs(feas["infeasible_rate"] - 10 / 200) < 1e-12


def test_no_batches_yields_zero_rate(tmp_path):
    feas = mid.summarize_integerizer_feasibility(tmp_path)
    assert feas["n_logs"] == 0
    assert feas["n_total"] == 0
    assert feas["infeasible_rate"] == 0.0


def test_all_optimal_is_zero_rate(tmp_path):
    _write_batch_log(tmp_path, "batch_000", n_optimal=50, n_infeasible=0, n_retry=0)
    feas = mid.summarize_integerizer_feasibility(tmp_path)
    assert feas["n_infeasible"] == 0
    assert feas["infeasible_rate"] == 0.0


def test_warn_threshold_is_a_fraction():
    # Sanity: the warn threshold is a sensible fraction in (0, 1).
    assert 0.0 < mid.INTEGERIZER_INFEASIBLE_WARN_RATE < 1.0
