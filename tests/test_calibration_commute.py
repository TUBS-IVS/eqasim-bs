import numpy as np
from braunschweig.calibration.commute import (
    furness_update, shrink_sparse_factors, build_validation_report,
)
from braunschweig.calibration.metrics import emd_on_bands, band_shares


def test_furness_update_moves_toward_target():
    target = np.array([0.4, 0.3, 0.1, 0.1, 0.05, 0.03, 0.02])
    factors = np.ones(7)
    for _ in range(50):
        model = factors / factors.sum()
        factors = furness_update(factors, target, model)
    final = factors / factors.sum()
    assert emd_on_bands(final, target) < 1e-3


def test_shrink_sparse_blends_low_count_cells():
    factors = {71: np.array([2.0, 1.0]), 77: np.array([10.0, 0.0])}
    counts = {71: np.array([500, 500]), 77: np.array([3, 2])}
    pooled = np.array([1.0, 1.0])
    shrunk, rate = shrink_sparse_factors(factors, counts, pooled,
                                         min_count=10, weight=0.5)
    np.testing.assert_allclose(shrunk[71], np.array([2.0, 1.0]))
    np.testing.assert_allclose(shrunk[77], np.array([5.5, 0.5]))
    assert rate == 0.5  # 2 of 4 cells shrunk


def test_validation_report_has_distance_and_fill_blocks():
    realised = {"03101": np.array([2.0, 8.0, 5.0, 0, 0, 0, 0])}  # km values
    target = {"03101": np.array([0.3, 0.3, 0.2, 0.1, 0.05, 0.03, 0.02])}
    jobs = {"03101000": 100.0}
    svb = {"03101000": 120.0}
    rep = build_validation_report(realised, target, jobs, svb)
    assert "distance_fit" in rep and "attraction_fill" in rep
    assert "03101" in rep["distance_fit"]
    assert 0.0 <= rep["distance_fit"]["03101"]["emd"] <= 1.0
    assert rep["attraction_fill"]["03101000"]["fill_ratio"] == 100.0 / 120.0
