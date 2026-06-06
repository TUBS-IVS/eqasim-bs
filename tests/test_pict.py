"""Tests for the pairwise (PICT) test-case generator + the pipeline config model."""
from __future__ import annotations

import sys
from itertools import combinations
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.testing import pict  # noqa: E402


def _covered_pairs(rows, names):
    covered = set()
    for row in rows:
        for i, j in combinations(range(len(names)), 2):
            a, b = names[i], names[j]
            covered.add((a, row[a], b, row[b]))
    return covered


def test_pairwise_covers_every_pair_without_constraints():
    factors = {"a": (0, 1), "b": (0, 1), "c": (0, 1), "d": ("x", "y")}
    rows = pict.pairwise(factors)
    names = list(factors)
    assert _covered_pairs(rows, names) == pict.feasible_pairs(factors)
    # Pairwise of four small factors is far smaller than the 16-row Cartesian.
    assert len(rows) <= 8


def test_pairwise_honours_constraints_and_still_covers_feasible_pairs():
    # b may only be 1 if a is 1.
    factors = {"a": (0, 1), "b": (0, 1), "c": (0, 1)}
    constraints = (lambda x: (x["b"] == 0) or (x["a"] == 1),)
    rows = pict.pairwise(factors, constraints)
    # No selected row violates the constraint.
    for row in rows:
        assert not (row["b"] == 1 and row["a"] == 0)
    # The infeasible pair (a=0, b=1) is never required; all feasible pairs covered.
    feasible = pict.feasible_pairs(factors, constraints)
    assert ("a", 0, "b", 1) not in feasible
    assert _covered_pairs(rows, list(factors)) >= feasible


def test_pipeline_covering_array_is_feasible_and_complete():
    rows = pict.pipeline_covering_array()
    names = list(pict.PIPELINE_FACTORS)
    assert rows, "covering array must be non-empty"
    # Every emitted configuration satisfies every pipeline constraint.
    for row in rows:
        for constraint in pict.PIPELINE_CONSTRAINTS:
            assert constraint(row), f"row violates a constraint: {row}"
    # Every feasible pair is covered.
    assert _covered_pairs(rows, names) >= pict.feasible_pairs(
        pict.PIPELINE_FACTORS, pict.PIPELINE_CONSTRAINTS
    )
    # Pairwise must be dramatically smaller than the full feasible Cartesian.
    full = len(pict.valid_assignments(pict.PIPELINE_FACTORS, pict.PIPELINE_CONSTRAINTS))
    assert len(rows) < full
    assert len(rows) <= 60


def test_pipeline_constraints_reject_documented_violations():
    base = {n: pict.PIPELINE_FACTORS[n][0] for n in pict.PIPELINE_FACTORS}

    def feasible(**overrides):
        a = dict(base)
        a.update(overrides)
        return all(c(a) for c in pict.PIPELINE_CONSTRAINTS)

    # age-aware chunking without the household-size margin is rejected.
    assert not feasible(age_aware_chunking=True, use_household_size_margin=False)
    # joint age x size without the size margin is rejected.
    assert not feasible(use_joint_age_size_margin=True, use_household_size_margin=False)
    # employment margin without the size margin is rejected.
    assert not feasible(use_employment_margin=True, use_household_size_margin=False)
    # sex-aware couples without age-aware chunking is rejected.
    assert not feasible(
        sex_aware_couples=True, age_aware_chunking=False, use_household_size_margin=True
    )
    # The Dirichlet prior is NOT constrained: it applies to every IPF seed cell,
    # so it is feasible with or without the employment margin.
    assert feasible(dirichlet_prior_strength=0.5, use_employment_margin=False,
                    use_household_size_margin=True)
    # the full valid stack is feasible.
    assert feasible(
        use_household_size_margin=True,
        use_joint_age_size_margin=True,
        age_aware_chunking=True,
        use_employment_margin=True,
        sex_aware_couples=True,
        dirichlet_prior_strength=0.5,
    )


def test_pipeline_covering_array_is_deterministic():
    assert pict.pipeline_covering_array() == pict.pipeline_covering_array()
