"""Tests for the new popsim control attributes: housing_tenure, building_type_3class, hh_type5.

Verifies:
1. attributes.map_housing_tenure derives owner/renter/unknown correctly from H_MIETE.
2. attributes.map_building_type_3class derives 3-class correctly from haustyp.
3. assembly.map_mid_person_attributes carries both new columns from donor households
   onto the persons frame (via _HOUSEHOLD_ATTRS join).
4. assembly.map_mid_person_attributes derives hh_type5 from expanded persons.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import attributes
from braunschweig.popsim import assembly


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _donor_households_with_tenure_and_building_type():
    """MiD donor households covering all H_MIETE and haustyp codes."""
    return pd.DataFrame({
        "H_ID":       [1,  2,  3,  4,  5],
        "oek_status": [3,  3,  3,  3,  3],
        "hheink_gr1": [4,  4,  4,  4,  4],
        "H_ANZAUTO":  [1,  0,  1,  0,  1],
        "H_ANZRAD":   [0,  1,  2,  0,  1],
        "H_MIETE":    [2,  1,  9,  2,  1],   # owner, renter, unknown, owner, renter
        "haustyp":    [1,  2,  3,  4, 95],   # EFH, MFH<12, GWB, sonstiges, n.z.
    })


def _merged_households_multi():
    """Five synthetic households backed by five distinct MiD donors."""
    return pd.DataFrame({
        "ZENSUS100m": ["A", "B", "C", "D", "E"],
        "ZENSUS1km":  ["KA", "KB", "KC", "KD", "KE"],
        "H_ID":       [1, 2, 3, 4, 5],
        "RegionalSchlussel_ARS": ["031010000000"] * 5,
    })


def _mid_persons_multi():
    """Persons for the five donor households."""
    return pd.DataFrame({
        "H_ID":     [1, 1, 2, 3, 4, 5],
        "P_ID":     [1, 2, 1, 1, 1, 1],
        "HP_ALTER": [40, 10, 25, 70, 45, 30],
        "HP_SEX":   [1,  2,  1,  2,  1,  2],
        "P_TAET":   [1,  9,  8,  11, 1,  8],
        "P_FSCHEIN":[1,  2,  1,  2,  1,  1],
        "P_FKARTE": [3,  8,  1,  8,  3,  8],
        "P_BKAT":   [1,  99, 7,  7,  1,  7],
    })


# ---------------------------------------------------------------------------
# 1. map_housing_tenure
# ---------------------------------------------------------------------------

def test_map_housing_tenure_derives_owner():
    hh = pd.DataFrame({"H_ID": [1], "H_MIETE": [2]})
    out = attributes.map_housing_tenure(hh)
    assert "housing_tenure" in out.columns
    assert out.loc[0, "housing_tenure"] == "owner"


def test_map_housing_tenure_derives_renter():
    hh = pd.DataFrame({"H_ID": [1], "H_MIETE": [1]})
    out = attributes.map_housing_tenure(hh)
    assert out.loc[0, "housing_tenure"] == "renter"


def test_map_housing_tenure_unknown_codes():
    """H_MIETE codes 3, 9, 309 -> unknown."""
    for code in [3, 9, 309]:
        hh = pd.DataFrame({"H_ID": [1], "H_MIETE": [code]})
        out = attributes.map_housing_tenure(hh)
        assert out.loc[0, "housing_tenure"] == "unknown", (
            f"H_MIETE={code} should give 'unknown'"
        )


def test_map_housing_tenure_absent_column_noop():
    """If H_MIETE is absent (ENTD path), the frame is returned unchanged."""
    hh = pd.DataFrame({"H_ID": [1], "oek_status": [3]})
    out = attributes.map_housing_tenure(hh)
    assert "housing_tenure" not in out.columns


def test_map_housing_tenure_mixed():
    """Multiple rows: owner, renter, unknown in one call."""
    hh = pd.DataFrame({
        "H_ID": [1, 2, 3, 4],
        "H_MIETE": [2, 1, 9, 2],
    })
    out = attributes.map_housing_tenure(hh)
    assert list(out["housing_tenure"]) == ["owner", "renter", "unknown", "owner"]


# ---------------------------------------------------------------------------
# 2. map_building_type_3class
# ---------------------------------------------------------------------------

def test_map_building_type_3class_efh():
    hh = pd.DataFrame({"H_ID": [1], "haustyp": [1]})
    out = attributes.map_building_type_3class(hh)
    assert "building_type_3class" in out.columns
    assert out.loc[0, "building_type_3class"] == "ein_zweifamilienhaus"


def test_map_building_type_3class_mfh():
    for code in [2, 3]:  # MFH 3-12 and GWB 13+ both -> mehrfamilienhaus
        hh = pd.DataFrame({"H_ID": [1], "haustyp": [code]})
        out = attributes.map_building_type_3class(hh)
        assert out.loc[0, "building_type_3class"] == "mehrfamilienhaus", (
            f"haustyp={code} should be mehrfamilienhaus"
        )


def test_map_building_type_3class_sonstiges():
    hh = pd.DataFrame({"H_ID": [1], "haustyp": [4]})
    out = attributes.map_building_type_3class(hh)
    assert out.loc[0, "building_type_3class"] == "sonstiges"


def test_map_building_type_3class_nicht_zutreffend():
    """haustyp=95 (n.z.) -> NaN (excluded from all three classes)."""
    hh = pd.DataFrame({"H_ID": [1], "haustyp": [95]})
    out = attributes.map_building_type_3class(hh)
    assert pd.isna(out.loc[0, "building_type_3class"])


def test_map_building_type_3class_absent_column_noop():
    """If haustyp is absent (ENTD path), the frame is returned unchanged."""
    hh = pd.DataFrame({"H_ID": [1], "oek_status": [3]})
    out = attributes.map_building_type_3class(hh)
    assert "building_type_3class" not in out.columns


# ---------------------------------------------------------------------------
# 3. assembly carries housing_tenure and building_type_3class onto persons
# ---------------------------------------------------------------------------

def test_assembly_carries_tenure_and_building_type():
    """After build_persons, persons must carry housing_tenure and building_type_3class."""
    persons, _ = assembly.build_persons(
        _merged_households_multi(),
        _donor_households_with_tenure_and_building_type(),
        _mid_persons_multi(),
        rng=np.random.RandomState(0),
    )
    assert "housing_tenure" in persons.columns, (
        "assembly.build_persons must carry housing_tenure from donor households"
    )
    assert "building_type_3class" in persons.columns, (
        "assembly.build_persons must carry building_type_3class from donor households"
    )


def test_assembly_tenure_values_match_donor():
    """Persons from H_ID=1 (H_MIETE=2) must have housing_tenure='owner'."""
    persons, _ = assembly.build_persons(
        _merged_households_multi(),
        _donor_households_with_tenure_and_building_type(),
        _mid_persons_multi(),
        rng=np.random.RandomState(0),
    )
    # source_household_id is a surrogate: H_ID=1 is the first sorted donor -> surrogate 1.
    hh1_persons = persons[persons["source_household_id"] == 1]
    assert len(hh1_persons) >= 1
    assert (hh1_persons["housing_tenure"] == "owner").all(), (
        f"H_ID=1 (H_MIETE=2) -> owner; got {hh1_persons['housing_tenure'].tolist()}"
    )


def test_assembly_tenure_renter():
    """Persons from H_ID=2 (H_MIETE=1) must have housing_tenure='renter'."""
    persons, _ = assembly.build_persons(
        _merged_households_multi(),
        _donor_households_with_tenure_and_building_type(),
        _mid_persons_multi(),
        rng=np.random.RandomState(0),
    )
    hh2_persons = persons[persons["source_household_id"] == 2]
    assert (hh2_persons["housing_tenure"] == "renter").all(), (
        f"H_ID=2 (H_MIETE=1) -> renter; got {hh2_persons['housing_tenure'].tolist()}"
    )


def test_assembly_building_type_efh():
    """Persons from H_ID=1 (haustyp=1) must have building_type_3class='ein_zweifamilienhaus'."""
    persons, _ = assembly.build_persons(
        _merged_households_multi(),
        _donor_households_with_tenure_and_building_type(),
        _mid_persons_multi(),
        rng=np.random.RandomState(0),
    )
    hh1_persons = persons[persons["source_household_id"] == 1]
    assert (hh1_persons["building_type_3class"] == "ein_zweifamilienhaus").all()


def test_assembly_building_type_nzt_is_nan():
    """Persons from H_ID=5 (haustyp=95, n.z.) must have building_type_3class=NaN."""
    persons, _ = assembly.build_persons(
        _merged_households_multi(),
        _donor_households_with_tenure_and_building_type(),
        _mid_persons_multi(),
        rng=np.random.RandomState(0),
    )
    hh5_persons = persons[persons["source_household_id"] == 5]
    assert hh5_persons["building_type_3class"].isna().all(), (
        f"H_ID=5 (haustyp=95) -> NaN; got {hh5_persons['building_type_3class'].tolist()}"
    )


# ---------------------------------------------------------------------------
# 4. hh_type5 derivation in assembly
# ---------------------------------------------------------------------------

def test_assembly_derives_hh_type5():
    """assembly.build_persons must emit hh_type5 on the persons frame."""
    persons, _ = assembly.build_persons(
        _merged_households_multi(),
        _donor_households_with_tenure_and_building_type(),
        _mid_persons_multi(),
        rng=np.random.RandomState(0),
    )
    assert "hh_type5" in persons.columns, (
        "assembly.build_persons must derive hh_type5 from expanded persons"
    )


def test_assembly_hh_type5_single_person_household():
    """A household with one person aged 40 should be classified as 'einpersonen'."""
    merged = pd.DataFrame({
        "ZENSUS100m": ["X"], "ZENSUS1km": ["KX"], "H_ID": [99],
        "RegionalSchlussel_ARS": ["031010000000"],
    })
    hh = pd.DataFrame({
        "H_ID": [99], "oek_status": [3], "hheink_gr1": [4],
        "H_ANZAUTO": [0], "H_ANZRAD": [0],
        "H_MIETE": [2], "haustyp": [1],
    })
    persons_raw = pd.DataFrame({
        "H_ID": [99], "P_ID": [1], "HP_ALTER": [40], "HP_SEX": [1],
        "P_TAET": [1], "P_FSCHEIN": [1], "P_FKARTE": [3], "P_BKAT": [1],
    })
    persons, _ = assembly.build_persons(merged, hh, persons_raw, rng=np.random.RandomState(0))
    assert "hh_type5" in persons.columns
    assert persons["hh_type5"].iloc[0] == "einpersonen", (
        f"Single-person household (age=40) should be 'einpersonen', "
        f"got '{persons['hh_type5'].iloc[0]}'"
    )
