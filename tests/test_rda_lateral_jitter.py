"""Regression test for the start-point jitter of ``GravityChainSolver.solve``.

Defect (audit 2026-11-11, U1-F2): the lateral start jitter was
``2.0 * (normal() - 0.5) * lateral_deviation``. The ``- 0.5`` centring belongs to a
uniform [0, 1) draw; with a standard normal draw it shifts every intermediate start
point by ``-lateral_deviation`` along the chain normal. The gravity relaxation keeps the
side the chain starts on, so about three quarters of all chains bent to the same side
of the origin-destination line instead of half.

The test asserts the behavioural contract: for a chain symmetric about the
origin-destination line, the solved chains bend to either side equally often.
"""

import numpy as np

from synthesis.population.spatial.secondary.rda import GravityChainSolver

# 1000 solves give a standard error of about 0.016 on a 0.5 share; the bound of 0.06
# is about four standard errors, while the defect produced a share of about 0.22-0.24.
NUMBER_OF_CHAINS = 1000
MAXIMUM_SIDE_SHARE_DEVIATION = 0.06


def test_gravity_chain_bends_to_both_sides_equally_often():
    random = np.random.RandomState(0)
    # The same lateral deviation as the production RDA fallback
    # (braunschweig.synthesis.locations.secondary_chainsolvers.fallback).
    solver = GravityChainSolver(random = random, lateral_deviation = 10.0)

    # Origin and destination on the x axis, so the chain normal is the y axis.
    problem = dict(origin = np.array([[0.0, 0.0]]), destination = np.array([[1000.0, 0.0]]), size = 2)
    distances = np.array([400.0, 400.0, 400.0])

    mean_lateral_offsets_meters = []
    for _ in range(NUMBER_OF_CHAINS):
        result = solver.solve(problem, distances)
        assert result["valid"]
        mean_lateral_offsets_meters.append(result["locations"][:, 1].mean())

    share_below_line = float(np.mean(np.array(mean_lateral_offsets_meters) < 0.0))
    assert abs(share_below_line - 0.5) < MAXIMUM_SIDE_SHARE_DEVIATION, share_below_line
