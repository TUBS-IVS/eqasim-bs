import numpy as np
from scripts.calibrate_education_slopes import (
    mean_distance_for_slope, secant_calibrate_slope, calibrate_level_per_rs7,
)
from braunschweig.synthesis.locations.education_gravity_model import (
    assign_by_capacity_gravity,
)


def test_calibrate_level_per_rs7_differentiates_and_hits_targets():
    # Two RS7 groups share the same uniform 0..20 km spatial spread and the same
    # 11 schools (every 2 km). Group 72 has a short target (3 km), group 74 a long
    # one (7 km). The coupled full-level calibration must give each group a slope
    # that hits its own target, with the shorter-target group getting the steeper
    # (more negative) slope.
    rng = np.random.RandomState(0)
    n = 400
    x = rng.uniform(0, 20000, 2 * n)
    pupil_xy = np.column_stack([x, np.zeros(2 * n)])
    pupil_rs7 = np.array([72] * n + [74] * n)
    schools = np.column_stack([np.arange(0, 20001, 2000, dtype=float),
                               np.zeros(11)])
    capacity = np.full(11, 1000.0)
    targets = {72: 3.0, 74: 7.0}
    slopes = calibrate_level_per_rs7(
        pupil_xy, pupil_rs7, schools, capacity, targets,
        max_radius_km=60.0, seed=1, rounds=25, tol=0.4)

    assert slopes[72] < slopes[74]   # shorter target -> steeper slope

    svec = np.array([slopes[c] for c in pupil_rs7], dtype=float)
    ch, _ = assign_by_capacity_gravity(
        pupil_xy, schools, capacity, slope=svec, max_radius_km=60.0,
        max_iterations=200, tolerance=1e-6, rng=np.random.RandomState(1))
    d = np.sqrt(((pupil_xy - schools[ch]) ** 2).sum(axis=1)) / 1000.0
    assert abs(d[pupil_rs7 == 72].mean() - 3.0) < 1.0
    assert abs(d[pupil_rs7 == 74].mean() - 7.0) < 1.0


def _setup():
    rng = np.random.RandomState(0)
    pupils = np.column_stack([rng.uniform(0, 20000, 200), np.zeros(200)])
    schools = np.array([[0.0, 0.0], [20000.0, 0.0]])
    capacity = np.array([1000.0, 1000.0])
    return pupils, schools, capacity, rng


def test_mean_distance_decreases_with_steeper_slope():
    pupils, schools, capacity, rng = _setup()
    flat = mean_distance_for_slope(-0.02, pupils, schools, capacity,
                                   max_radius_km=60.0, rng=np.random.RandomState(1))
    steep = mean_distance_for_slope(-1.0, pupils, schools, capacity,
                                    max_radius_km=60.0, rng=np.random.RandomState(1))
    assert steep < flat


def test_secant_calibrate_hits_target():
    pupils, schools, capacity, rng = _setup()
    target = 5.0
    slope = secant_calibrate_slope(
        target, pupils, schools, capacity, max_radius_km=60.0,
        seed=2, lo=-3.0, hi=-0.001, max_iter=40, tol=0.2)
    got = mean_distance_for_slope(slope, pupils, schools, capacity,
                                  max_radius_km=60.0, rng=np.random.RandomState(2))
    assert abs(got - target) < 0.5


def test_mean_distance_decay_decreases_with_steeper_slope():
    from scripts.calibrate_education_slopes import mean_distance_decay
    rng = np.random.RandomState(0)
    pupils = np.zeros((300, 2))
    unis = np.array([[0.0, 0.0], [40000.0, 0.0]])
    weight = np.array([5000.0, 20000.0])
    flat = mean_distance_decay(-0.02, pupils, unis, weight, 150.0,
                               np.random.RandomState(1))
    steep = mean_distance_decay(-0.5, pupils, unis, weight, 150.0,
                                np.random.RandomState(1))
    assert steep < flat
