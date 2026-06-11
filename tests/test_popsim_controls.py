"""Tests for the PopulationSim control integerization (Phase 2).

The 100 m control marginals must be integerized so that, within each 1 km
parent, the integer 100 m values sum to the (rounded) 1 km target -- the
hierarchical consistency PopulationSim relies on. Uses the largest-remainder
(Hamilton) apportionment.
"""

from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.popsim import controls


# ---------------------------------------------------------------------------
# largest_remainder_round
# ---------------------------------------------------------------------------

def test_largest_remainder_basic():
    # floors [1,1,1] = 3, need 4 -> the largest remainder (1.6) gets the +1.
    assert controls.largest_remainder_round([1.2, 1.2, 1.6], 4) == [1, 1, 2]


def test_largest_remainder_preserves_total():
    out = controls.largest_remainder_round([0.5, 2.3, 4.1, 1.1], 8)
    assert sum(out) == 8
    assert all(isinstance(x, int) for x in out)


def test_largest_remainder_zero_total():
    assert controls.largest_remainder_round([0.0, 0.0], 0) == [0, 0]


def test_largest_remainder_exact_integers_unchanged():
    assert controls.largest_remainder_round([2.0, 3.0], 5) == [2, 3]


def test_largest_remainder_ties_break_by_index():
    # Equal remainders (0.5 each); only one +1 to give -> lowest index wins.
    assert controls.largest_remainder_round([0.5, 0.5], 1) == [1, 0]


def test_largest_remainder_can_round_down():
    # total below the sum of floors -> remove from the smallest remainder.
    # floors [2,2] = 4, total 3 -> drop one from the smallest frac (index 0).
    assert controls.largest_remainder_round([2.4, 2.6], 3) == [1, 2]


def test_largest_remainder_empty():
    assert controls.largest_remainder_round([], 0) == []


# ---------------------------------------------------------------------------
# integerize_within_parents
# ---------------------------------------------------------------------------

def _toy_controls():
    return pd.DataFrame(
        {
            "GITTER_ID_100m": ["a1", "a2", "a3", "b1", "b2"],
            "parent": ["A", "A", "A", "B", "B"],
            "pop": [1.2, 1.2, 1.6, 4.4, 4.6],  # A sums 4.0, B sums 9.0
        }
    )


def test_integerize_within_parents_sums_to_rounded_parent_total():
    df = _toy_controls()
    out = controls.integerize_within_parents(df, value_col="pop", parent_col="parent")
    per_parent = out.groupby(df["parent"]).sum()
    assert per_parent["A"] == 4  # round(4.0)
    assert per_parent["B"] == 9  # round(9.0)
    assert out.dtype.kind == "i"


def test_integerize_within_parents_uses_explicit_targets():
    df = _toy_controls()
    targets = {"A": 5, "B": 9}  # override A's target
    out = controls.integerize_within_parents(
        df, value_col="pop", parent_col="parent", targets=targets
    )
    per_parent = out.groupby(df["parent"]).sum()
    assert per_parent["A"] == 5
    assert per_parent["B"] == 9


def test_integerize_preserves_row_order_and_index():
    df = _toy_controls().set_index("GITTER_ID_100m")
    out = controls.integerize_within_parents(df, value_col="pop", parent_col="parent")
    assert list(out.index) == list(df.index)
