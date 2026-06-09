"""Tests for assembly.build_persons schema-gap columns (Task 4).

Verifies that the gap columns mandated by the popsim-mid integration spec
(Section 5.1) are emitted correctly:

  age_range, high_income, household_size, is_urban_resident,
  pt_subscription_type, socioprofessional_class,
  source_person_id, source_household_id

Uses a small synthetic fixture so no external data is needed.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import assembly


# ---------------------------------------------------------------------------
# Minimal fixtures
# ---------------------------------------------------------------------------

def _merged_households():
    """Two synthetic households with two distinct MiD donors.

    Includes ``RegionalSchlussel_ARS``: the 12-digit ARS that stage.py joins from
    the cells parquet onto the merged PopulationSim output before calling
    build_persons (required for derive_zone_ids to produce commune_id /
    departement_id / iris_id; bug D1 fix).
    """
    return pd.DataFrame({
        "ZENSUS100m": ["A", "B"],
        "ZENSUS1km": ["KA", "KB"],
        "H_ID": [1, 2],
        # Real Braunschweig (03101) 12-digit ARS; last 3 digits = Gemeinde suffix "000".
        "RegionalSchlussel_ARS": ["031010000000", "031010000000"],
    })


def _mid_persons():
    """Three MiD respondent persons covering a range of ages and ticket codes.

    Columns required by expand + attribute mappers:
    - H_ID, P_ID: donor keys
    - HP_ALTER, HP_SEX: demographics
    - P_TAET: employment status
    - P_FSCHEIN: driving licence
    - P_FKARTE: PT ticket type (1=single, 3=Deutschlandticket, 5=monthly-abo)
    - P_BKAT: occupation category (1=Angestellte -> CS1 6; 7=not employed -> fallback)
    """
    return pd.DataFrame({
        "H_ID":      [1,  1,  2],
        "P_ID":      [1,  2,  1],
        "HP_ALTER":  [40, 10, 25],
        "HP_SEX":    [1,  2,  1],
        "P_TAET":    [1,  9,  8],      # employed; pupil; unemployed
        "P_FSCHEIN": [1,  2,  1],      # licence yes/no/yes
        "P_FKARTE":  [3,  8,  1],      # Deutschlandticket; never; single ticket
        "P_BKAT":    [1,  99, 7],      # Angestellte; k.A. (nonresponse); not employed
    })


def _mid_households():
    """Two MiD donor households covering different income groups."""
    return pd.DataFrame({
        "H_ID":       [1, 2],
        "oek_status": [3, 5],           # medium; very_high
        "hheink_gr1": [4, 15],          # 1500-2000 EUR; >7000 EUR
        "H_ANZAUTO":  [1, 0],
        "H_ANZRAD":   [2, 1],
    })


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_build_persons_emits_gap_columns_and_source_ids():
    """All eight gap columns must be present and non-null where expected."""
    persons = assembly.build_persons(
        _merged_households(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )
    for col in [
        "age_range", "high_income", "household_size", "is_urban_resident",
        "pt_subscription_type", "socioprofessional_class",
        "source_person_id", "source_household_id",
    ]:
        assert col in persons.columns, f"Missing column: {col}"

    # source IDs must be non-null for every person (provenance guarantee).
    assert persons["source_person_id"].notna().all(), "source_person_id has nulls"
    assert persons["source_household_id"].notna().all(), "source_household_id has nulls"

    # age_range and household_size must be non-null for every person.
    assert persons["age_range"].notna().all(), "age_range has nulls"
    assert persons["household_size"].notna().all(), "household_size has nulls"


def test_age_range_bins_match_enriched_py_core():
    """age_range labels must match synthesis/population/enriched.py lines 110-114.

    enriched.py logic:
      default -> higher_education (age >= 18)
      age <= 10 -> primary_school
      11 <= age <= 14 -> middle_school
      15 <= age <= 17 -> high_school
    """
    persons = assembly.build_persons(
        _merged_households(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )
    # age=40 -> higher_education
    p_adult = persons[persons["source_person_id"] == "1"].iloc[0]
    assert p_adult["age"] == 40
    assert p_adult["age_range"] == "higher_education", (
        f"age=40 should be higher_education, got {p_adult['age_range']}"
    )

    # age=10 -> primary_school (boundary: age <= 10)
    p_child = persons[persons["source_person_id"] == "2"].iloc[0]
    assert p_child["age"] == 10
    assert p_child["age_range"] == "primary_school", (
        f"age=10 should be primary_school, got {p_child['age_range']}"
    )

    # age=25 -> higher_education
    p_young = persons[persons["source_person_id"].isin(["1"]) & (persons["age"] == 25)]
    if len(p_young) == 0:
        # The third person (H_ID=2, P_ID=1, age=25) has source_person_id="1" too
        p_young = persons[persons["age"] == 25]
    assert len(p_young) >= 1
    assert (p_young["age_range"] == "higher_education").all(), (
        f"age=25 should be higher_education, got {p_young['age_range'].tolist()}"
    )


def test_age_range_boundary_conditions():
    """Verify that age_range boundary ages map to the correct category."""
    # Build a custom merged + persons frame with boundary ages.
    merged = pd.DataFrame({
        "ZENSUS100m": ["X"], "ZENSUS1km": ["KX"], "H_ID": [99],
        "RegionalSchlussel_ARS": ["031010000000"],
    })
    hh = pd.DataFrame({
        "H_ID": [99], "oek_status": [3], "hheink_gr1": [4],
        "H_ANZAUTO": [0], "H_ANZRAD": [0],
    })
    # One person per boundary age: 10, 11, 14, 15, 17, 18.
    persons_raw = pd.DataFrame({
        "H_ID":      [99] * 6,
        "P_ID":      [1, 2, 3, 4, 5, 6],
        "HP_ALTER":  [10, 11, 14, 15, 17, 18],
        "HP_SEX":    [1] * 6,
        "P_TAET":    [9, 9, 9, 9, 9, 1],   # all non-employed except age-18
        "P_FSCHEIN": [2, 2, 2, 2, 2, 1],
        "P_FKARTE":  [8, 8, 8, 8, 8, 3],
        "P_BKAT":    [7, 7, 7, 7, 7, 1],
    })
    result = assembly.build_persons(merged, hh, persons_raw, rng=np.random.RandomState(0))
    age_to_range = dict(zip(result["age"].tolist(), result["age_range"].tolist()))

    assert age_to_range[10] == "primary_school",  f"age=10: {age_to_range[10]}"
    assert age_to_range[11] == "middle_school",   f"age=11: {age_to_range[11]}"
    assert age_to_range[14] == "middle_school",   f"age=14: {age_to_range[14]}"
    assert age_to_range[15] == "high_school",     f"age=15: {age_to_range[15]}"
    assert age_to_range[17] == "high_school",     f"age=17: {age_to_range[17]}"
    assert age_to_range[18] == "higher_education", f"age=18: {age_to_range[18]}"


def test_high_income_flag():
    """high_income must be True only for persons in a household with income 'over_7000'."""
    persons = assembly.build_persons(
        _merged_households(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )
    # Household 1 has hheink_gr1=4 -> household_income='1500_2000' -> not high income.
    hh1 = persons[persons["source_household_id"] == "1"]
    assert not hh1["high_income"].any(), "Household 1 should not be high_income"

    # Household 2 has hheink_gr1=15 -> household_income='over_7000' -> high income.
    hh2 = persons[persons["source_household_id"] == "2"]
    assert hh2["high_income"].all(), "Household 2 should be high_income"


def test_household_size_equals_donor_person_count():
    """household_size must equal the number of persons in the synthetic household."""
    persons = assembly.build_persons(
        _merged_households(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )
    # Synthetic household from donor 1 has 2 MiD persons.
    hh1 = persons[persons["source_household_id"] == "1"]
    assert (hh1["household_size"] == 2).all(), (
        f"Expected household_size=2 for donor 1, got {hh1['household_size'].tolist()}"
    )

    # Synthetic household from donor 2 has 1 MiD person.
    hh2 = persons[persons["source_household_id"] == "2"]
    assert (hh2["household_size"] == 1).all(), (
        f"Expected household_size=1 for donor 2, got {hh2['household_size'].tolist()}"
    )


def test_pt_subscription_type_is_valid_category():
    """pt_subscription_type must be a valid PT_TICKET_CATEGORIES string."""
    from braunschweig.data.mid.reference_tables import PT_TICKET_CATEGORIES

    persons = assembly.build_persons(
        _merged_households(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )
    assert "pt_subscription_type" in persons.columns
    invalid = ~persons["pt_subscription_type"].isin(PT_TICKET_CATEGORIES)
    assert not invalid.any(), (
        f"Invalid pt_subscription_type values: "
        f"{persons.loc[invalid, 'pt_subscription_type'].unique().tolist()}"
    )


def test_pt_subscription_type_primary_codes_map_correctly():
    """P_FKARTE 3 -> deutschlandticket; P_FKARTE 8 -> fahre_nie; P_FKARTE 1 -> einzelfahrschein."""
    persons = assembly.build_persons(
        _merged_households(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )
    # person P_ID=1 in H_ID=1, age=40: P_FKARTE=3 -> deutschlandticket
    row_dt = persons[(persons["source_household_id"] == "1") & (persons["age"] == 40)].iloc[0]
    assert row_dt["pt_subscription_type"] == "deutschlandticket", (
        f"P_FKARTE=3 should map to deutschlandticket, got {row_dt['pt_subscription_type']}"
    )

    # person P_ID=2 in H_ID=1, age=10: P_FKARTE=8 -> fahre_nie
    row_nie = persons[(persons["source_household_id"] == "1") & (persons["age"] == 10)].iloc[0]
    assert row_nie["pt_subscription_type"] == "fahre_nie", (
        f"P_FKARTE=8 should map to fahre_nie, got {row_nie['pt_subscription_type']}"
    )

    # person P_ID=1 in H_ID=2, age=25: P_FKARTE=1 -> einzelfahrschein
    row_single = persons[(persons["source_household_id"] == "2") & (persons["age"] == 25)].iloc[0]
    assert row_single["pt_subscription_type"] == "einzelfahrschein", (
        f"P_FKARTE=1 should map to einzelfahrschein, got {row_single['pt_subscription_type']}"
    )


def test_source_ids_trace_to_mid_donors():
    """source_person_id / source_household_id must contain the MiD donor P_ID / H_ID."""
    persons = assembly.build_persons(
        _merged_households(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )
    # All donor H_IDs present in source_household_id.
    assert set(persons["source_household_id"].unique()) == {"1", "2"}, (
        f"Expected source_household_ids {{1,2}}, got "
        f"{set(persons['source_household_id'].unique())}"
    )
    # source_person_id values must be non-empty strings.
    assert persons["source_person_id"].str.len().gt(0).all(), (
        "source_person_id contains empty strings"
    )


def test_build_persons_emits_weight_column_equal_to_one():
    """build_persons must emit a ``weight`` column with value 1.0 for every person.

    popsim_mid is an already-expanded population (each row = one synthetic person,
    no stochastic rounding needed).  synthesis.population.sampled requires this
    column; with weight=1.0 every household is replicated exactly once before the
    sampling_rate selection, matching the behaviour of braunschweig.ipf.attributed.
    """
    persons = assembly.build_persons(
        _merged_households(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )
    assert "weight" in persons.columns, "build_persons must emit a 'weight' column"
    assert (persons["weight"] == 1.0).all(), (
        f"All weight values must be 1.0; got: {persons['weight'].unique().tolist()}"
    )


def test_build_persons_has_studies_column():
    """build_persons must emit a boolean ``studies`` column derived from P_TAET.

    Bug D4: studies was not derived from P_TAET, so map_socioprofessional_class
    fallback always saw studies=False (treating all students as non-students).
    """
    persons = assembly.build_persons(
        _merged_households(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )
    assert "studies" in persons.columns, (
        "build_persons must emit a 'studies' column derived from P_TAET"
    )
    assert persons["studies"].dtype == bool, (
        f"studies must be bool, got {persons['studies'].dtype}"
    )

    # P_TAET=1 (employed) -> studies=False
    # P_TAET=9 (Schueler) -> studies=True
    # P_TAET=8 (Ausbildung) -> studies=True
    p_employed = persons[
        (persons["source_household_id"] == "1") & (persons["age"] == 40)
    ].iloc[0]
    assert p_employed["studies"] is False or p_employed["studies"] == False, (
        f"P_TAET=1 (employed) should give studies=False, got {p_employed['studies']}"
    )

    p_pupil = persons[
        (persons["source_household_id"] == "1") & (persons["age"] == 10)
    ].iloc[0]
    assert p_pupil["studies"] is True or p_pupil["studies"] == True, (
        f"P_TAET=9 (Schueler) should give studies=True, got {p_pupil['studies']}"
    )


def test_build_persons_spc_uses_p_bkat_crosswalk():
    """When the donor has a valid P_BKAT code, SPC must come from SPC_BY_P_BKAT.

    Bug D4: P_BKAT was absent from MID_PERSON_ATTR_COLS, so SPC always used
    the broad-activity fallback. After the fix, P_BKAT=1 (Angestellte) must
    yield CS1 6, not whatever the fallback derives from employed/age.

    The _mid_persons() fixture has:
      H_ID=1, P_ID=1, age=40, P_TAET=1 (employed), P_BKAT=1 (Angestellte) -> CS1 6
    """
    from braunschweig.popsim.attributes import SPC_BY_P_BKAT

    persons = assembly.build_persons(
        _merged_households(), _mid_households(), _mid_persons(),
        rng=np.random.RandomState(0),
    )
    # The first person: H_ID=1, age=40, P_BKAT=1 -> CS1 6
    row = persons[
        (persons["source_household_id"] == "1") & (persons["age"] == 40)
    ].iloc[0]
    expected_spc = SPC_BY_P_BKAT[1]  # P_BKAT=1 -> 6
    assert row["socioprofessional_class"] == expected_spc, (
        f"P_BKAT=1 (Angestellte) should give SPC={expected_spc} (CS1 6), "
        f"got {row['socioprofessional_class']}. "
        "This indicates P_BKAT is not reaching the persons frame (bug D4)."
    )
