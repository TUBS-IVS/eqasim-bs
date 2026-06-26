import numpy as np
from braunschweig.calibration import metrics, circuity


def test_apply_detour_default_mode_is_constant():
    """Default (no mode) must equal d * DETOUR_FACTOR (1.3) — byte-identical to pre-Tier-3."""
    d = np.array([0.0, 10.0])
    np.testing.assert_allclose(
        metrics.apply_detour(d), d * metrics.DETOUR_FACTOR)


def test_apply_detour_constant_mode_matches_legacy():
    d = np.array([0.0, 10.0])
    np.testing.assert_allclose(
        metrics.apply_detour(d, mode="constant"), d * metrics.DETOUR_FACTOR)


def test_apply_detour_curve_mode_uses_circuity():
    """Explicit mode="curve" uses the fitted circuity curve (opt-in)."""
    d = np.array([1.0, 20.0])
    np.testing.assert_allclose(
        metrics.apply_detour(d, network="car", mode="curve"),
        circuity.euclidean_to_routed(d, "car", mode="curve"))


def test_detour_factor_is_legacy_constant():
    assert metrics.DETOUR_FACTOR == circuity.LEGACY_DETOUR_FACTOR
