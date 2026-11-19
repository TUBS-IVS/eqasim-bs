"""Tests for the minor-child household class (issue #659, owner decision D-fam).

The seed class ``hh_minor_class`` (``seed.derive_hh_minor_class``) and its per-cell
1 km targets (``minor_child_targets.per_cell_targets``) replace the four 100 m census
family-type controls, whose child definition (any age) does not match the seed's
under-18 definition.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import minor_child_targets as mct
from braunschweig.popsim.seed import derive_hh_minor_class


# ---------------------------------------------------------------------------
# Seed class
# ---------------------------------------------------------------------------


def test_minor_class(caplog):
    households = {
        "one_adult": [40, 10],
        "couple_with_minor": [40, 38, 10],
        "adult_child_and_minor": [45, 20, 15],
        "no_minor": [40, 20],
        "minors_only": [15, 12],
    }
    persons = pd.DataFrame(
        [(hid, age) for hid, ages in households.items() for age in ages],
        columns=["household_id", "HP_ALTER"],
    )
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.seed"):
        result = derive_hh_minor_class(persons)

    assert result.name == "hh_minor_class"
    assert result["one_adult"] == "one_adult"
    assert result["couple_with_minor"] == "multi_adult"
    assert result["adult_child_and_minor"] == "multi_adult"
    assert result["no_minor"] == "none"
    assert pd.isna(result["minors_only"])
    # The unclassifiable household is surfaced, never silently left NaN.
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


# ---------------------------------------------------------------------------
# Per-cell 1 km targets
# ---------------------------------------------------------------------------

_LONE_FATHER, _LONE_MOTHER = mct.LONE_PARENT_SOURCES
_MARRIED, _REGISTERED, _COHABITING = mct.COUPLE_SOURCES


def _squares(rows):
    """rows: (square id, family-table households, lone fathers, lone mothers,
    married couples, registered partners, cohabiting couples), all with a minor."""
    return pd.DataFrame(rows, columns=[
        mct.SQUARE_ID_COLUMN, mct.FAMILY_TABLE_HOUSEHOLDS,
        _LONE_FATHER, _LONE_MOTHER, _MARRIED, _REGISTERED, _COHABITING,
    ])


def _cells(rows):
    return pd.DataFrame(rows, columns=["cell", "parent", "kreis", "households"])


def _targets(cells, squares):
    return mct.per_cell_targets(
        cells, squares, parent_col="parent", kreis_col="kreis",
        household_total_col="households")


def test_targets_are_the_square_shares():
    squares = _squares([("S1", 100, 2, 3, 15, 1, 4)])  # 5 lone parents, 20 couples
    cells = _cells([("c1", "S1", "03101", 30), ("c2", "S1", "03101", 10)])

    out, report = _targets(cells, squares)

    np.testing.assert_allclose(out[mct.ONE_ADULT_TARGET], [1.5, 0.5])
    np.testing.assert_allclose(out[mct.MULTI_ADULT_TARGET], [6.0, 2.0])
    assert report["squares_on_kreis_fallback"] == 0
    assert report["households_on_kreis_fallback"] == 0
    assert report["squares_total"] == 1
    assert list(cells.columns) == ["cell", "parent", "kreis", "households"]  # input untouched


def test_square_without_family_table_takes_the_kreis_share_and_is_counted(caplog):
    # Kreis A primary squares S1 + S2: lone 20/400 = 0.05, couples 60/400 = 0.15.
    # S5 straddles A (10 households) and B (30): it belongs to B, so B's share is
    # 40/200 = 0.2 lone parents and 0 couples, and S5 does not enter A's share.
    # S3 has 0 family-table households, S4 is absent from the table; S4 holds 35
    # households in A and 5 in B, so both of its cells take A's share.
    squares = _squares([
        ("S1", 100, 2, 3, 15, 1, 4),
        ("S2", 300, 5, 10, 30, 0, 10),
        ("S3", 0, 0, 0, 0, 0, 0),
        ("S5", 200, 10, 30, 0, 0, 0),
    ])
    cells = _cells([
        ("c1", "S1", "A", 40), ("c2", "S2", "A", 60), ("c3", "S3", "A", 20),
        ("c4", "S4", "A", 35), ("c5", "S4", "B", 5),
        ("c6", "S5", "A", 10), ("c7", "S5", "B", 30),
    ])

    with caplog.at_level(logging.INFO, logger=mct.__name__):
        out, report = _targets(cells, squares)

    one = out.set_index("cell")[mct.ONE_ADULT_TARGET]
    multi = out.set_index("cell")[mct.MULTI_ADULT_TARGET]
    assert one["c3"] == pytest.approx(1.0) and multi["c3"] == pytest.approx(3.0)
    assert one["c4"] == pytest.approx(1.75) and multi["c4"] == pytest.approx(5.25)
    assert one["c5"] == pytest.approx(0.25) and multi["c5"] == pytest.approx(0.75)
    assert one["c6"] == pytest.approx(2.0) and multi["c6"] == pytest.approx(0.0)
    assert one["c1"] == pytest.approx(2.0) and multi["c1"] == pytest.approx(8.0)
    assert report["squares_total"] == 5
    assert report["squares_on_kreis_fallback"] == 2
    assert report["households_on_kreis_fallback"] == 60
    assert report["households_total"] == 200
    # 30 % of the households on the fallback is far above the 5 % warning bound.
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_true_zero_square_stays_zero():
    # Households in the family table, none with a minor: a true 0, not a fallback.
    squares = _squares([("S1", 50, 0, 0, 0, 0, 0), ("S2", 100, 2, 3, 15, 1, 4)])
    cells = _cells([("c1", "S1", "A", 25), ("c2", "S2", "A", 10)])

    out, report = _targets(cells, squares)

    assert out.loc[out["cell"] == "c1", mct.ONE_ADULT_TARGET].item() == 0.0
    assert out.loc[out["cell"] == "c1", mct.MULTI_ADULT_TARGET].item() == 0.0
    assert report["squares_on_kreis_fallback"] == 0


@pytest.mark.parametrize("defect, expected", [
    ("drop_source", _LONE_MOTHER),
    ("nan_source", _MARRIED),
    ("drop_cells_column", "households"),
])
def test_missing_source_column_raises(defect, expected):
    squares = _squares([("S1", 100, 2, 3, 15, 1, 4)])
    cells = _cells([("c1", "S1", "A", 30)])
    if defect == "drop_source":
        squares = squares.drop(columns=[_LONE_MOTHER])
    elif defect == "nan_source":
        squares[_MARRIED] = squares[_MARRIED].astype(float)
        squares.loc[0, _MARRIED] = np.nan
    else:
        cells = cells.drop(columns=["households"])

    with pytest.raises(ValueError, match=expected):
        _targets(cells, squares)
