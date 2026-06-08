"""Tests for expanding popsim donor households into eqasim persons (Phase 5g).

The PopulationSim output is one row per synthetic household, referencing a MiD
donor ``H_ID``. The same donor can recur within a cell, so a stable
``(cell, H_ID, occurrence)`` identity is assigned, then each household is expanded
to its MiD persons and mapped to the eqasim demographic schema. Tiny synthetic
data only.
"""

from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.popsim import expand


def _merged_households():
    # donor H_ID 1 appears twice in cell A (occurrence 0 and 1), once in cell B.
    return pd.DataFrame(
        {
            "ZENSUS100m": ["A", "A", "B"],
            "ZENSUS1km": ["KA", "KA", "KB"],
            "H_ID": [1, 1, 2],
        }
    )


def _mid_persons():
    # household 1 has two persons, household 2 has one.
    return pd.DataFrame(
        {
            "H_ID": [1, 1, 2],
            "P_ID": [1, 2, 1],
            "HP_ALTER": [40, 8, 70],
            "HP_SEX": [1, 2, 2],
        }
    )


# ---------------------------------------------------------------------------
# assign_synthetic_household_ids
# ---------------------------------------------------------------------------

def test_assign_household_ids_occurrence_and_uniqueness():
    out = expand.assign_synthetic_household_ids(_merged_households())
    assert list(out["hh_occurrence"]) == [0, 1, 0]
    # household_id encodes cell_H_ID_occurrence and is unique.
    assert list(out["household_id"]) == ["A_1_0", "A_1_1", "B_2_0"]
    assert out["household_id"].is_unique


# ---------------------------------------------------------------------------
# expand_to_persons
# ---------------------------------------------------------------------------

def test_expand_to_persons_one_row_per_donor_person():
    hh = expand.assign_synthetic_household_ids(_merged_households())
    persons = expand.expand_to_persons(hh, _mid_persons())
    # 2 households of donor 1 (2 persons each) + 1 household of donor 2 (1 person) = 5.
    assert len(persons) == 5
    assert persons["person_id"].is_unique
    # Each synthetic household keeps its cell + a person count matching the donor.
    counts = persons.groupby("household_id").size().to_dict()
    assert counts == {"A_1_0": 2, "A_1_1": 2, "B_2_0": 1}


def test_expand_to_persons_preserves_cell_and_household_link():
    hh = expand.assign_synthetic_household_ids(_merged_households())
    persons = expand.expand_to_persons(hh, _mid_persons())
    a = persons[persons["household_id"] == "A_1_0"]
    assert set(a["ZENSUS100m"]) == {"A"}
    assert (a["household_id"] == "A_1_0").all()


# ---------------------------------------------------------------------------
# map_demographics
# ---------------------------------------------------------------------------

def test_map_demographics_age_and_sex():
    hh = expand.assign_synthetic_household_ids(_merged_households())
    persons = expand.expand_to_persons(hh, _mid_persons())
    mapped = expand.map_demographics(persons)
    assert "age" in mapped.columns and "sex" in mapped.columns
    row = mapped[mapped["P_ID"] == 1].iloc[0]
    assert row["age"] == 40
    assert mapped.loc[mapped["HP_SEX"] == 1, "sex"].eq("male").all()
    assert mapped.loc[mapped["HP_SEX"] == 2, "sex"].eq("female").all()


def test_map_demographics_unknown_sex_is_unknown():
    persons = pd.DataFrame({"HP_ALTER": [30], "HP_SEX": [9]})
    mapped = expand.map_demographics(persons)
    assert mapped.loc[0, "sex"] == "unknown"
