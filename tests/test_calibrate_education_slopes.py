import numpy as np
from scripts.calibrate_education_slopes import (
    mean_distance_for_slope, secant_calibrate_slope,
)


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
