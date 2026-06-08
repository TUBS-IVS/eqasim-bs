"""Tests for assembling the popsim_mid persons frame (Phase 5g.5 core).

Composes the expansion + demographic + attribute mappings into one persons frame
(household attributes joined from the MiD donor household). Tiny synthetic data.
"""

from __future__ import annotations

import pandas as pd

from braunschweig.popsim import assembly


def _merged():
    return pd.DataFrame(
        {"ZENSUS100m": ["A", "B"], "ZENSUS1km": ["KA", "KB"], "H_ID": [1, 2]}
    )


def _mid_persons():
    return pd.DataFrame(
        {
            "H_ID": [1, 1, 2],
            "P_ID": [1, 2, 1],
            "HP_ALTER": [40, 10, 70],
            "HP_SEX": [1, 2, 2],
            "P_TAET": [1, 9, 11],     # employed, pupil, retired
            "P_FSCHEIN": [1, 2, 1],   # licence yes/no/yes
        }
    )


def _mid_households():
    return pd.DataFrame(
        {
            "H_ID": [1, 2],
            "oek_status": [3, 5],
            "hheink_gr1": [4, 9],     # 1500-2000 -> 1750; 4000-4600 -> 4300
            "H_ANZAUTO": [1, 2],
        }
    )


def test_build_persons_composes_demographics_attributes_and_household():
    persons = assembly.build_persons(_merged(), _mid_households(), _mid_persons())
    # 2 persons for donor 1 + 1 for donor 2 = 3.
    assert len(persons) == 3
    assert persons["person_id"].is_unique

    p1 = persons[persons["person_id"] == "A_1_0_1"].iloc[0]
    assert p1["age"] == 40 and p1["sex"] == "male"
    assert bool(p1["employed"]) is True and bool(p1["has_license"]) is True
    assert p1["economic_status"] == "medium"
    assert p1["household_income_eur"] == 1750.0
    assert p1["number_of_cars"] == 1
    # car availability derived per household from cars vs adults (>=18).
    assert p1["car_availability"] in {"none", "some", "all"}


def test_build_persons_car_availability_uses_adult_count():
    persons = assembly.build_persons(_merged(), _mid_households(), _mid_persons())
    # Household A (A_1_0): 1 adult (age 40) + 1 child (age 10) -> 1 adult; 1 car -> all.
    a = persons[persons["household_id"] == "A_1_0"]
    assert (a["car_availability"] == "all").all()


def test_build_persons_carries_home_cell():
    persons = assembly.build_persons(_merged(), _mid_households(), _mid_persons())
    assert set(persons.loc[persons["household_id"] == "A_1_0", "ZENSUS100m"]) == {"A"}
