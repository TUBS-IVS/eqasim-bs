import numpy as np
from braunschweig.calibration import metrics


def test_apply_detour_default_mode_is_constant():
    """Default (no mode) must equal d * DETOUR_FACTOR (1.3) — byte-identical to pre-Tier-3."""
    d = np.array([0.0, 10.0])
    np.testing.assert_allclose(
        metrics.apply_detour(d), d * metrics.DETOUR_FACTOR)


def test_apply_detour_constant_mode_matches_legacy():
    d = np.array([0.0, 10.0])
    np.testing.assert_allclose(
        metrics.apply_detour(d, mode="constant"), d * metrics.DETOUR_FACTOR)


