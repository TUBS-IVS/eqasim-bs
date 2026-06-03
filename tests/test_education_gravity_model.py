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
    rng = np.random.RandomState(0)
    pupils = np.zeros((500, 2))
    schools = np.array([[0.0, 0.0], [0.0, 0.0]])
    capacity = np.array([1000.0, 10.0])
    choice, fallback = assign_by_capacity_gravity(
        pupils, schools, capacity, slope=-0.2, max_radius_km=50.0,
        max_iterations=200, tolerance=1e-9, rng=rng,
    )
    big_share = np.mean(choice == 0)
    assert big_share > 0.95
    assert fallback.sum() == 0


def test_capacity_gravity_prefers_closer_at_equal_capacity():
    rng = np.random.RandomState(1)
    pupils = np.zeros((400, 2))
    schools = np.array([[0.0, 0.0], [30_000.0, 0.0]])
    capacity = np.array([200.0, 200.0])
    choice, _ = assign_by_capacity_gravity(
        pupils, schools, capacity, slope=-0.2, max_radius_km=60.0,
        max_iterations=200, tolerance=1e-9, rng=rng,
    )
    assert np.mean(choice == 0) > 0.7


def test_capacity_gravity_radius_fallback_to_nearest():
    rng = np.random.RandomState(2)
    pupils = np.zeros((10, 2))
    schools = np.array([[40_000.0, 0.0]])
    capacity = np.array([100.0])
    choice, fallback = assign_by_capacity_gravity(
        pupils, schools, capacity, slope=-0.2, max_radius_km=10.0,
        max_iterations=50, tolerance=1e-6, rng=rng,
    )
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
