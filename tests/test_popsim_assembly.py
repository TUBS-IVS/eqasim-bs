"""Tests for assembling the popsim_mid persons frame (Phase 5g.5 core).

Composes the expansion + demographic + attribute mappings into one persons frame
(household attributes joined from the MiD donor household). Tiny synthetic data.
"""

from __future__ import annotations

import pandas as pd

from braunschweig.popsim import assembly


def _merged():
    """Two synthetic households.  Includes ``RegionalSchlussel_ARS`` so
    build_persons can derive commune_id / departement_id / iris_id (bug D1 fix)."""
    return pd.DataFrame({
        "ZENSUS100m": ["A", "B"],
        "ZENSUS1km": ["KA", "KB"],
        "H_ID": [1, 2],
        # Real Braunschweig (03101) 12-digit ARS.
        "RegionalSchlussel_ARS": ["031010000000", "031010000000"],
    })


def _mid_persons():
    return pd.DataFrame(
        {
            "H_ID": [1, 1, 2],
            "P_ID": [1, 2, 1],
            "HP_ALTER": [40, 10, 70],
            "HP_SEX": [1, 2, 2],
            "P_TAET": [1, 9, 11],     # employed, pupil, retired
            "P_FSCHEIN": [1, 2, 1],   # licence yes/no/yes
            "P_FKARTE": [3, 8, 5],    # Deutschlandticket, never, monthly-abo
        }
    )


def _mid_households():
    return pd.DataFrame(
        {
            "H_ID": [1, 2],
            "oek_status": [3, 5],
            "hheink_gr1": [4, 9],     # 1500-2000 -> 1750; 4000-4600 -> 4300
            "H_ANZAUTO": [1, 2],
            "H_ANZRAD": [2, 0],
            # anzpedrad = bicycles INCLUDING pedelecs (default number_of_bicycles source,
            # verified 2026-07-08); no pedelecs here, so it equals H_ANZRAD.
            "anzpedrad": [2, 0],
            # H_ANZPED = Anzahl Pedelecs (default has_ebike source, verified 2026-07-08);
            # neither household owns one.
            "H_ANZPED": [0, 0],
        }
    )


def test_build_persons_composes_demographics_attributes_and_household():
    persons, _map = assembly.build_persons(_merged(), _mid_households(), _mid_persons())
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
    assert bool(p1["has_pt_subscription"]) is True   # P_FKARTE 3 = Deutschlandticket
    # household A has 2 bikes / 2 persons -> all.
    assert p1["bicycle_availability"] == "all"


def test_build_persons_car_availability_uses_adult_count():
    persons, _map = assembly.build_persons(_merged(), _mid_households(), _mid_persons())
    # Household A (A_1_0): 1 adult (age 40) + 1 child (age 10) -> 1 adult; 1 car -> all.
    a = persons[persons["household_id"] == "A_1_0"]
    assert (a["car_availability"] == "all").all()


def test_build_persons_carries_home_cell():
    persons, _map = assembly.build_persons(_merged(), _mid_households(), _mid_persons())
    assert set(persons.loc[persons["household_id"] == "A_1_0", "ZENSUS100m"]) == {"A"}


# ---------------------------------------------------------------------------
# Mapper contract: every attribute_mapper returns (persons, pseudonym_map)
# ---------------------------------------------------------------------------

def test_mapper_contract_no_isinstance_sniff():
    """build_persons must not sniff the mapper return value with
    ``isinstance(result, tuple)`` and silently substitute an empty pseudonym
    map -- that would lose the re-linking map for a pseudonymisation-required
    source (the file would still be written, just empty: "it ran")."""
    import inspect

    src = inspect.getsource(assembly.build_persons)
    assert "isinstance(result, tuple)" not in src


def test_mapper_returning_frame_only_raises():
    """A mapper that returns only a frame (no pseudonym map) must raise, not
    silently substitute an empty map (which would lose re-linking for a
    pseudonymisation-required source)."""
    import pytest

    def bad_mapper(persons, households, *, rng):
        return persons  # missing the pseudonym map

    with pytest.raises(TypeError, match="pseudonym"):
        assembly.build_persons(
            _merged(), _mid_households(), _mid_persons(),
            attribute_mapper=bad_mapper, pseudonymise=False,
            inkar_scale=None,
        )
