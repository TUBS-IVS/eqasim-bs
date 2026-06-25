import numpy as np
from braunschweig.calibration import detour_fit as df


def test_fit_recovers_known_params():
    rng = np.random.RandomState(0)
    d = rng.uniform(0.2, 60.0, size=5000)
    c = 1.15 + 0.6 * np.exp(-d / 2.0)
    routed = d * c * (1.0 + rng.normal(0, 0.01, size=d.size))
    out = df.fit_circuity_curve(d, routed)
    assert abs(out["c_inf"] - 1.15) < 0.05
    assert abs(out["a"] - 0.6) < 0.1
    assert abs(out["tau"] - 2.0) < 0.5
    assert out["r2"] > 0.9


def test_tracker_does_not_converge_before_min_samples():
    t = df.ConvergenceTracker(min_samples=8000, tol=0.01, patience=2)
    p = {"c_inf": 1.15, "a": 0.6, "tau": 2.0}
    assert t.update(2000, p) is False
    assert t.update(4000, p) is False     # stable but below floor -> not converged


def test_tracker_converges_after_floor_when_stable():
    t = df.ConvergenceTracker(min_samples=4000, tol=0.01, patience=2)
    p = {"c_inf": 1.15, "a": 0.60, "tau": 2.00}
    t.update(4000, p)
    assert t.update(6000, {"c_inf": 1.151, "a": 0.601, "tau": 2.00}) is False  # 1st stable
    assert t.update(8000, {"c_inf": 1.151, "a": 0.601, "tau": 2.00}) is True   # 2nd stable -> converged


def test_tracker_resets_patience_on_move():
    t = df.ConvergenceTracker(min_samples=2000, tol=0.01, patience=2)
    t.update(2000, {"c_inf": 1.15, "a": 0.6, "tau": 2.0})
    t.update(4000, {"c_inf": 1.15, "a": 0.6, "tau": 2.0})       # 1st stable
    t.update(6000, {"c_inf": 1.30, "a": 0.6, "tau": 2.0})       # moved -> reset
    assert t.update(8000, {"c_inf": 1.30, "a": 0.6, "tau": 2.0}) is False  # only 1st stable again
