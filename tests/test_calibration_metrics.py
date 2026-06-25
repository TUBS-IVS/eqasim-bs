import numpy as np
from braunschweig.calibration.metrics import (
    DETOUR_FACTOR, apply_detour, band_shares, emd_on_bands,
)


def test_apply_detour_scales_euclidean_to_routed():
    d = np.array([0.0, 10.0])
    np.testing.assert_allclose(apply_detour(d), d * DETOUR_FACTOR)


def test_band_shares_normalised():
    d = np.array([1.0, 2.0, 7.0, 250.0])
    s = band_shares(d)
    np.testing.assert_allclose(s.sum(), 1.0)
    assert s[0] == 0.5 and s[1] == 0.25 and s[6] == 0.25


def test_emd_zero_for_identical():
    p = np.array([0.2, 0.2, 0.2, 0.1, 0.1, 0.1, 0.1])
    assert emd_on_bands(p, p) == 0.0


def test_emd_increases_with_displacement():
    p = np.array([1.0, 0, 0, 0, 0, 0, 0])
    near = np.array([0, 1.0, 0, 0, 0, 0, 0])
    far = np.array([0, 0, 0, 0, 0, 0, 1.0])
    assert emd_on_bands(p, far) > emd_on_bands(p, near) > 0.0
