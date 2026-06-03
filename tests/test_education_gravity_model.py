import numpy as np
from braunschweig.synthesis.locations.education_gravity_model import (
    balance_doubly_constrained, assign_by_capacity_gravity, assign_by_radius,
)


def test_furness_hits_both_margins():
    friction = np.array([[1.0, 0.5],
                         [0.5, 1.0],
                         [1.0, 1.0]])
    production = np.array([1.0, 1.0, 1.0])
    attraction = np.array([2.0, 1.0])
    T = balance_doubly_constrained(production, attraction, friction,
                                   max_iterations=200, tolerance=1e-9)
    assert np.allclose(T.sum(axis=1), production, atol=1e-6)
    assert np.allclose(T.sum(axis=0), attraction, atol=1e-6)


def test_capacity_gravity_fills_big_school_not_tiny_one():
    # two schools at the SAME location; capacities 1000 vs 10 -> ~99% to big.
    rng = np.random.RandomState(0)
    pupils = np.zeros((500, 2))
    schools = np.array([[0.0, 0.0], [0.0, 0.0]])
    capacity = np.array([1000.0, 10.0])
    choice, fallback = assign_by_capacity_gravity(
        pupils, schools, capacity, slope=-0.2, max_radius_km=50.0,
        max_iterations=200, tolerance=1e-9, rng=rng)
    assert np.mean(choice == 0) > 0.95
    assert fallback.sum() == 0


def test_capacity_constraint_keeps_tiny_close_school_from_overfilling():
    # Core feature: a tiny-capacity school sits ON TOP of 1000 pupils; a big
    # school is 10 km away. Proximity alone would dump everyone into the tiny
    # school; the doubly-constrained balancing must hold its intake near its
    # scaled capacity target (50), NOT ~1000. The "no 2-vs-10000" guarantee.
    rng = np.random.RandomState(7)
    pupils = np.zeros((1000, 2))
    schools = np.array([[0.0, 0.0], [10_000.0, 0.0]])
    capacity = np.array([50.0, 950.0])
    choice, _ = assign_by_capacity_gravity(
        pupils, schools, capacity, slope=-0.2, max_radius_km=60.0,
        max_iterations=500, tolerance=1e-9, rng=rng)
    n_tiny = int(np.sum(choice == 0))
    assert 20 <= n_tiny <= 110


def test_capacity_gravity_prefers_closer_when_capacity_allows():
    # Distance preference shows up with HETEROGENEOUS pupil locations: half the
    # pupils sit at school A, half at school B; equal capacities matching the
    # co-located counts. The model must route (almost) everyone to their nearest
    # (co-located) school -- distance preference within capacity feasibility.
    rng = np.random.RandomState(1)
    near_a = np.zeros((200, 2))
    near_b = np.tile(np.array([30_000.0, 0.0]), (200, 1))
    pupils = np.vstack([near_a, near_b])
    schools = np.array([[0.0, 0.0], [30_000.0, 0.0]])
    capacity = np.array([200.0, 200.0])
    choice, _ = assign_by_capacity_gravity(
        pupils, schools, capacity, slope=-0.2, max_radius_km=60.0,
        max_iterations=500, tolerance=1e-9, rng=rng)
    assert np.mean(choice[:200] == 0) > 0.9
    assert np.mean(choice[200:] == 1) > 0.9


def test_capacity_gravity_radius_fallback_to_nearest():
    rng = np.random.RandomState(2)
    pupils = np.zeros((10, 2))
    schools = np.array([[40_000.0, 0.0]])
    capacity = np.array([100.0])
    choice, fallback = assign_by_capacity_gravity(
        pupils, schools, capacity, slope=-0.2, max_radius_km=10.0,
        max_iterations=50, tolerance=1e-6, rng=rng)
    assert (choice == 0).all()
    assert fallback.all()


def test_assign_by_radius_weighted_and_fallback():
    rng = np.random.RandomState(3)
    pupils = np.zeros((200, 2))
    schools = np.array([[0.0, 0.0], [0.0, 0.0]])
    weight = np.array([9.0, 1.0])
    choice = assign_by_radius(pupils, schools, weight, radius_m=2000.0, rng=rng)
    assert np.mean(choice == 0) > 0.8
    assert set(np.unique(choice)) <= {0, 1}
