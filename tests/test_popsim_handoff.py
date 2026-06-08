"""Tests for the cell -> building handoff (Phase 5).

Ports the popsimprep notebook Step 5: distribute the synthetic households of each
100 m cell across the buildings in that cell, preferring ``has_home`` buildings
and falling back to all buildings when none are flagged. Uses tiny synthetic
frames (no real 3 GB GeoPackage).
"""

from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.popsim import handoff


def _buildings():
    # Cell A: 2 has_home + 1 not. Cell B: 0 has_home (2 buildings).
    # Cell C: 1 building, no households. (Cell D has no buildings at all.)
    return pd.DataFrame(
        {
            "cell_id": ["A", "A", "A", "B", "B", "C"],
            "has_home": [True, True, False, False, False, True],
        }
    )


def _hh_by_cell():
    # A gets 3 households, B gets 2, D (no buildings) gets 1 -> orphan.
    return {"A": [1, 2, 3], "B": [4, 5], "D": [6]}


# ---------------------------------------------------------------------------
# households_by_cell
# ---------------------------------------------------------------------------

def test_households_by_cell_groups_h_ids():
    combined = pd.DataFrame(
        {"ZENSUS100m": ["A", "A", "B"], "H_ID": [1, 2, 9]}
    )
    grouped = handoff.households_by_cell(combined)
    assert grouped == {"A": [1, 2], "B": [9]}


# ---------------------------------------------------------------------------
# assign_households_to_buildings
# ---------------------------------------------------------------------------

def test_assign_round_robin_over_has_home_buildings():
    buildings = _buildings()
    assigned, _ = handoff.assign_households_to_buildings(_hh_by_cell(), buildings)
    by_index = assigned.set_index(assigned.index)["HH_IDs"]
    # Cell A: 3 HH round-robin over the 2 has_home buildings (rows 0,1):
    # row0 <- [1,3], row1 <- [2]; the non-has_home row 2 stays empty.
    assert by_index[0] == "1;3"
    assert by_index[1] == "2"
    assert by_index[2] in (None, "")


def test_assign_falls_back_to_all_buildings_when_no_has_home():
    buildings = _buildings()
    assigned, report = handoff.assign_households_to_buildings(_hh_by_cell(), buildings)
    # Cell B has no has_home -> fallback to its 2 buildings (rows 3,4): [4]/[5].
    by_index = assigned.set_index(assigned.index)["HH_IDs"]
    assert by_index[3] == "4"
    assert by_index[4] == "5"
    assert report.n_cells_fallback == 1


def test_assign_reports_orphans_and_rates():
    buildings = _buildings()
    _, report = handoff.assign_households_to_buildings(_hh_by_cell(), buildings)
    assert report.n_cells_with_households == 3       # A, B, D
    assert report.n_cells_orphan == 1                # D has no buildings
    assert report.n_households_orphan == 1           # household 6
    assert report.n_households_assigned == 5         # A:3 + B:2
    assert report.orphan_household_rate == pytest.approx(1 / 6)


def test_assign_does_not_touch_cells_without_households():
    buildings = _buildings()
    assigned, _ = handoff.assign_households_to_buildings(_hh_by_cell(), buildings)
    # Cell C building (row 5) has no households assigned.
    assert assigned.set_index(assigned.index)["HH_IDs"][5] in (None, "")


def test_assign_requires_expected_columns():
    bad = pd.DataFrame({"wrong": [1]})
    with pytest.raises(ValueError):
        handoff.assign_households_to_buildings({"A": [1]}, bad)
