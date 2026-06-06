"""Edge / degenerate-input robustness tests for the shared doubly-constrained
Furness primitive ``balance_doubly_constrained``.

The higher-level capacity-gravity behaviour (fill proportions, radius / nearest
fallback, per-pupil slope) is covered in tests/test_education_gravity_model.py.
This file locks the numerical robustness of the underlying balancing on
degenerate inputs -- a zero-friction row (an isolated pupil), a zero-friction or
zero-attraction column (an unreachable / capacity-less school), and 1xC / Rx1
shapes -- so the divide-by-zero guards (np.divide(..., where=denom>0)) keep the
result finite (no NaN / inf) instead of poisoning the whole flow matrix.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.synthesis.locations.education_gravity_model import (  # noqa: E402
    balance_doubly_constrained,
)


def test_balance_hits_both_margins_on_a_well_posed_case():
    # Sanity anchor: a positive friction with balanced totals reproduces both
    # margins (so the degenerate cases below are contrasted against a known-good).
    production = np.array([2.0, 2.0])
    attraction = np.array([1.0, 3.0])
    friction = np.array([[1.0, 0.5], [0.5, 1.0]])
    T = balance_doubly_constrained(production, attraction, friction, tolerance=1e-6)
    assert np.all(np.isfinite(T))
    assert np.allclose(T.sum(axis=1), production, atol=1e-3)
    assert np.allclose(T.sum(axis=0), attraction, atol=1e-3)


def test_balance_zero_attraction_column_stays_finite_and_empty():
    # A school with zero attraction target must receive ~zero flow, and the
    # matrix must stay finite (no NaN from a 1/0).
    production = np.array([5.0, 5.0])
    attraction = np.array([10.0, 0.0])  # second column has no capacity target
    friction = np.array([[1.0, 1.0], [1.0, 1.0]])
    T = balance_doubly_constrained(production, attraction, friction)
    assert np.all(np.isfinite(T))
    assert np.allclose(T[:, 1], 0.0)
    # The reachable column absorbs the whole production.
    assert np.allclose(T.sum(axis=1), production, atol=1e-3)


def test_balance_zero_friction_row_isolates_that_row_without_nan():
    # A pupil with all-zero friction (no reachable school) gets a zero row; the
    # other rows must still balance and nothing may become NaN / inf.
    production = np.array([3.0, 3.0])
    attraction = np.array([3.0, 3.0])
    friction = np.array([[0.0, 0.0], [1.0, 1.0]])
    T = balance_doubly_constrained(production, attraction, friction)
    assert np.all(np.isfinite(T))
    assert np.allclose(T[0, :], 0.0)         # isolated row places nothing here
    assert T[1, :].sum() > 0.0               # the reachable row still places


def test_balance_all_zero_friction_returns_finite_zeros():
    # Fully degenerate: no positive friction anywhere -> all-zero, finite result
    # (the guards must prevent any NaN / inf).
    production = np.array([1.0, 1.0])
    attraction = np.array([1.0, 1.0])
    friction = np.zeros((2, 2))
    T = balance_doubly_constrained(production, attraction, friction)
    assert np.all(np.isfinite(T))
    assert np.allclose(T, 0.0)


def test_balance_single_school_collects_all_pupils():
    # Rx1: every pupil must land in the single available column.
    production = np.array([4.0, 6.0])
    attraction = np.array([10.0])
    friction = np.array([[1.0], [1.0]])
    T = balance_doubly_constrained(production, attraction, friction)
    assert np.all(np.isfinite(T))
    assert T.shape == (2, 1)
    assert np.allclose(T[:, 0], production, atol=1e-3)


def test_balance_single_pupil_row_spreads_over_columns():
    # 1xC: a single production row spread over columns by capacity proportions.
    production = np.array([10.0])
    attraction = np.array([3.0, 7.0])
    friction = np.array([[1.0, 1.0]])
    T = balance_doubly_constrained(production, attraction, friction)
    assert np.all(np.isfinite(T))
    assert np.isclose(T.sum(), 10.0, atol=1e-3)
    # Column proportions follow the attraction targets under equal friction.
    assert np.allclose(T[0, :], attraction, atol=1e-3)
