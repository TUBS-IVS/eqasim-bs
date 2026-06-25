import numpy as np
from braunschweig.calibration import metrics, circuity


def test_apply_detour_constant_mode_matches_legacy():
    d = np.array([0.0, 10.0])
    np.testing.assert_allclose(
        metrics.apply_detour(d, mode="constant"), d * metrics.DETOUR_FACTOR)


def test_apply_detour_curve_mode_uses_circuity():
    d = np.array([1.0, 20.0])
    np.testing.assert_allclose(
        metrics.apply_detour(d, network="car", mode="curve"),
        circuity.euclidean_to_routed(d, "car"))


def test_detour_factor_is_legacy_constant():
    assert metrics.DETOUR_FACTOR == circuity.LEGACY_DETOUR_FACTOR
