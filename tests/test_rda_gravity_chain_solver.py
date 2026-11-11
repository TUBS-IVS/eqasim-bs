"""GravityChainSolver must honour its alpha and eps constructor arguments."""
import numpy as np

from synthesis.population.spatial.secondary.rda import GravityChainSolver


def _problem():
    return {
        "origin": np.array([[0.0, 0.0]]),
        "destination": np.array([[1000.0, 0.0]]),
        "size": 2,
    }


def _solve(alpha, eps, seed=7):
    solver = GravityChainSolver(
        np.random.RandomState(seed), alpha=alpha, eps=eps, lateral_deviation=10
    )
    return solver.solve(_problem(), np.array([400.0, 400.0, 400.0]))


def test_huge_eps_converges_without_iterating():
    result = _solve(alpha=0.3, eps=1e6)
    assert result["valid"]
    assert result["iterations"] == 0


def test_alpha_changes_the_result():
    slow = _solve(alpha=0.05, eps=1e-2)
    fast = _solve(alpha=0.5, eps=1e-2)
    assert not np.allclose(slow["locations"], fast["locations"])
