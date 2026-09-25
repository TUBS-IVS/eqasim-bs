import numpy as np
from braunschweig.calibration import metrics


def test_apply_detour_default_and_constant_mode_reproduce_the_legacy_factor_1_3():
    """The default (no mode) and mode="constant" both return euclidean x 1.3, byte-identical
    to pre-Tier-3. The literal is the contract: comparing against the module constant would
    let a changed factor pass."""
    d = np.array([0.0, 10.0])
    np.testing.assert_allclose(metrics.apply_detour(d), [0.0, 13.0])
    np.testing.assert_allclose(metrics.apply_detour(d, mode="constant"), [0.0, 13.0])


