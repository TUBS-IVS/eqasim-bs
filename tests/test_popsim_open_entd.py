"""Tests for the ENTD donor-source adapter (Phase 2: popsim_open).

All fixtures are synthetic; no real ENTD files are required. The tests verify:

- ENTD_SEED_COLUMNS constant shape and content.
- EntdSource.map_person_attributes emits every REQUIRED_PERSON_COLUMNS (except
  is_urban_resident, which build_persons adds from the home commune).
- Spot-checks for derived/defaulted columns: car_availability, age_range,
  high_income, household_income, pt_subscription_type, source_* provenance ids.
- EntdSource.build_trips joins trips onto synthetic persons, produces the 11-col
  contract + euclidean_distance + trip_index, and preserves ENTD mode/purpose vocab.
- Registry returns EntdSource for get_source("entd").
- build_persons called without attribute_mapper still uses the MiD mapper (byte-
  identical guard).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import sources
from braunschweig.popsim.sources.entd import EntdSource, ENTD_SEED_COLUMNS, ENTD_BUILT_SEED_COLUMNS
from braunschweig.popsim.seed import SeedColumns
from braunschweig.population import schema
from braunschweig.popsim.trips_stage import CONTRACT


# ---------------------------------------------------------------------------
# Minimal ENTD-shaped fixtures (no real data files needed)
# ---------------------------------------------------------------------------

def _entd_households():
    """Three ENTD households with canonical columns."""
    return pd.DataFrame({
        "household_id":       [10, 20, 30],
        "household_weight":   [1.5, 2.0, 1.0],
        "household_size":     [2, 3, 1],
        "number_of_cars":     [1, 2, 0],
        "number_of_bicycles": [2, 1, 0],
        "departement_id":     ["75", "69", "33"],
        "consumption_units":  [1.5, 2.1, 1.0],
        "urban_type":         ["central_city", "suburb", "none"],
        # income_class 0..13 per ENTD INCOME_CLASS_BOUNDS in cleaned.py:
        # 0 = <400, 1=400-600 ... 13=>=10000
        "income_class":       [5, 10, 13],
    })


def _entd_persons():
    """Five ENTD persons linked to the three households above."""
    return pd.DataFrame({
        "person_id":                [100, 101, 200, 201, 202],
        "household_id":             [10,  10,  20,  20,  30],
        "person_weight":            [1.5, 1.5, 2.0, 2.0, 1.0],
        "age":                      [40,  10,  25,  55,  70],
        "sex":                      ["male", "female", "male", "female", "male"],
        "employed":                 [True, False, True, False, False],
        "studies":                  [False, True, False, False, False],
        "has_license":              [True, False, True, True, False],
        "has_pt_subscription":      [True, False, False, True, False],
        "number_of_trips":          [3, 2, 4, 0, 1],
        "departement_id":           ["75", "75", "69", "69", "33"],
        "trip_weight":              [1.0, 1.0, 2.0, 0.0, 1.0],
        "socioprofessional_class":  [3, 8, 5, 7, 7],
    })


def _entd_trips():
    """Minimal ENTD trips already in canonical schema (post-cleaned.py).

    ENTD trips use the same mode/purpose labels as eqasim (mapped in cleaned.py),
    so no re-mapping is needed in EntdSource.build_trips.
    """
    return pd.DataFrame({
        "person_id":          [100, 100, 200],
        "trip_id":            [1000, 1001, 2000],
        "trip_weight":        [1.0, 1.0, 2.0],
        "departure_time":     [28800.0, 36000.0, 32400.0],   # 8h, 10h, 9h
        "arrival_time":       [29700.0, 36900.0, 34200.0],   # 8h15, 10h15, 9h30
        "trip_duration":      [900.0, 900.0, 1800.0],
        "activity_duration":  [7200.0, None, 3600.0],
        "following_purpose":  ["work", "home", "shop"],
        "preceding_purpose":  ["home", "work", "home"],
        "is_last_trip":       [False, True, True],
        "is_first_trip":      [True, False, True],
        "mode":               ["car", "car", "walk"],
        "origin_departement_id":      ["75", "75", "69"],
        "destination_departement_id": ["75", "75", "69"],
        "routed_distance":    [12000.0, 12000.0, 3000.0],
        "euclidean_distance": [9231.0,  9231.0,  2308.0],
    })


def _synthetic_persons_with_source_ids():
    """Synthetic persons that have been assigned source_person_id from ENTD."""
    return pd.DataFrame({
        "person_id":       ["syn_A", "syn_B", "syn_C"],
        "household_id":    ["hh_1",  "hh_1",  "hh_2"],
        # source_person_id points into the ENTD persons table (person_id column)
        "source_person_id": [100, 100, 200],
    })


# ---------------------------------------------------------------------------
# ENTD_SEED_COLUMNS
# ---------------------------------------------------------------------------

def test_entd_seed_columns_is_seed_columns_instance():
    assert isinstance(ENTD_SEED_COLUMNS, SeedColumns)


def test_entd_seed_columns_field_values():
    assert ENTD_SEED_COLUMNS.household_id == "household_id"
    assert ENTD_SEED_COLUMNS.household_weight == "household_weight"
    assert ENTD_SEED_COLUMNS.person_household_id == "household_id"
    assert ENTD_SEED_COLUMNS.person_id == "person_id"
    assert ENTD_SEED_COLUMNS.person_weight == "person_weight"
    assert ENTD_SEED_COLUMNS.age == "age"
    assert ENTD_SEED_COLUMNS.sex == "sex"
    # ENTD has no day-of-week completeness filter (all trips are already reference-day)
    assert ENTD_SEED_COLUMNS.day_filter_col is None
    assert ENTD_SEED_COLUMNS.day_filter_values is None


def test_entd_source_seed_columns_returns_constant():
    src = EntdSource()
    assert src.seed_columns() == ENTD_SEED_COLUMNS


def test_entd_source_built_seed_columns_returns_mid_schema():
    """built_seed_columns() returns the MiD-schema constant (H_ID, H_GEW, …).

    This is the schema produced by build_seed(); filter_seed_to_stratum uses it
    to discover the correct join key on the post-build_seed frames.
    """
    src = EntdSource()
    assert src.built_seed_columns() == ENTD_BUILT_SEED_COLUMNS
    assert src.built_seed_columns().household_id == "H_ID"
    assert src.built_seed_columns().age == "HP_ALTER"
    assert src.built_seed_columns().sex == "HP_SEX"


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

def test_registry_returns_entd_source():
    src = sources.get_source("entd")
    assert src.name == "entd"
    assert isinstance(src, EntdSource)


# ---------------------------------------------------------------------------
# map_person_attributes: schema completeness
# ---------------------------------------------------------------------------

def test_entd_map_attributes_emits_required_schema():
    """map_person_attributes must produce every REQUIRED_PERSON_COLUMNS entry
    except is_urban_resident (added later by build_persons from home commune).
    No NaN in any required column (except those that may be structurally missing
    for children, but our fixture has no children that force NaN).
    """
    src = EntdSource()
    hh = _entd_households()
    p = _entd_persons()
    rng = np.random.RandomState(42)
    result, _pseudonym_map = src.map_person_attributes(p, hh, rng=rng)

    # All required columns except is_urban_resident (added by build_persons).
    required_here = [
        c for c in schema.REQUIRED_PERSON_COLUMNS
        if c not in ("is_urban_resident", "person_id", "household_id")
    ]

    missing_cols = [c for c in required_here if c not in result.columns]
    assert not missing_cols, f"map_person_attributes missing required columns: {missing_cols}"

    # No NaN in the required columns that must be complete
    non_null_required = [
        "age", "sex", "employed", "has_license", "has_pt_subscription",
        "car_availability", "bicycle_availability",
        "age_range", "high_income", "household_size",
        "pt_subscription_type", "socioprofessional_class",
        "source_person_id", "source_household_id",
    ]
    for col in non_null_required:
        assert result[col].notna().all(), (
            f"Column {col!r} has unexpected NaN values in map_person_attributes output"
        )


# ---------------------------------------------------------------------------
# map_person_attributes: spot-checks for derived / defaulted columns
# ---------------------------------------------------------------------------

def test_entd_car_availability_derived_correctly():
    """car_availability = derive_car_availability(n_cars, n_adults_>=18) per household."""
    src = EntdSource()
    hh = _entd_households()
    p = _entd_persons()
    result, _pseudonym_map = src.map_person_attributes(p, hh, rng=np.random.RandomState(0))

    # Household 10: 1 car, adults = persons age>=18: person 100 (age 40) -> 1 adult.
    # derive_car_availability(1, 1) = "all"
    row_hh10 = result[result["source_household_id"].isin(
        result.loc[result["age"] == 40, "source_household_id"]
    )]
    assert (row_hh10[row_hh10["age"] == 40]["car_availability"] == "all").all()

    # Household 30: 0 cars -> "none"
    row_hh30 = result[result["age"] == 70]
    assert (row_hh30["car_availability"] == "none").all()


def test_entd_age_range_cut_correctly():
    """age_range must use _AGE_RANGE_BINS from assembly (matches enriched.py bins)."""
    src = EntdSource()
    hh = _entd_households()
    p = _entd_persons()
    result, _pseudonym_map = src.map_person_attributes(p, hh, rng=np.random.RandomState(0))

    # age 10 -> primary_school (bin (-1, 10])
    assert (result[result["age"] == 10]["age_range"] == "primary_school").all()
    # age 25 -> higher_education (bin (17, inf))
    assert (result[result["age"] == 25]["age_range"] == "higher_education").all()
    # age 40 -> higher_education
    assert (result[result["age"] == 40]["age_range"] == "higher_education").all()


def test_entd_high_income_placeholder_and_eur_midpoint():
    """map_person_attributes sets household_income_eur and a placeholder high_income.

    After the popsim income unification (braunschweig.popsim.income), the REAL
    high_income is set by build_persons AFTER map_person_attributes returns,
    using the unified numeric rule: high_income = (household_income_eur >= 5000).
    The placeholder here (income_class >= ENTD_HIGH_INCOME_CLASS=13) is kept for
    schema completeness; build_persons overwrites it.

    This test verifies that:
    - household_income_eur is set to the ENTD raw midpoint (no INKAR yet).
    - The placeholder high_income is True for income_class=13 and False for class=5.
    """
    from braunschweig.popsim.income import ENTD_INCOME_CLASS_MIDPOINT_EUR

    src = EntdSource()
    hh = _entd_households()
    p = _entd_persons()
    result, _pseudonym_map = src.map_person_attributes(p, hh, rng=np.random.RandomState(0))

    # household_income_eur must be present and set to the raw ENTD midpoint.
    assert "household_income_eur" in result.columns, (
        "map_person_attributes must emit household_income_eur"
    )
    # Household 30 (income_class=13) -> midpoint 12000.0 EUR (person age=70)
    eur_70 = float(result[result["age"] == 70]["household_income_eur"].iloc[0])
    assert abs(eur_70 - ENTD_INCOME_CLASS_MIDPOINT_EUR[13]) < 1.0, (
        f"income_class=13 -> expected {ENTD_INCOME_CLASS_MIDPOINT_EUR[13]}, got {eur_70}"
    )
    # Household 10 (income_class=5) -> midpoint 1350.0 EUR (person age=40)
    eur_40 = float(result[result["age"] == 40]["household_income_eur"].iloc[0])
    assert abs(eur_40 - ENTD_INCOME_CLASS_MIDPOINT_EUR[5]) < 1.0, (
        f"income_class=5 -> expected {ENTD_INCOME_CLASS_MIDPOINT_EUR[5]}, got {eur_40}"
    )
    # Placeholder high_income: income_class=13 -> True; income_class=5 -> False
    # (build_persons overwrites this with the numeric rule after INKAR scaling)
    assert (result[result["age"] == 70]["high_income"] == True).all()
    assert (result[result["age"] == 40]["high_income"] == False).all()


def test_entd_household_income_mapped():
    """household_income must be one of the INCOME_CLASS_BY_GROUP values (eqasim labels)."""
    from braunschweig.popsim.attributes import INCOME_CLASS_BY_GROUP
    valid_labels = set(INCOME_CLASS_BY_GROUP.values())

    src = EntdSource()
    result, _pseudonym_map = src.map_person_attributes(
        _entd_persons(), _entd_households(), rng=np.random.RandomState(0)
    )
    invalid = ~result["household_income"].isin(valid_labels)
    assert not invalid.any(), (
        f"household_income contains invalid labels: "
        f"{result.loc[invalid, 'household_income'].unique()}"
    )


def test_popsim_open_derives_economic_status_from_income_class():
    """ENTD has no native status; it is derived from the income class via the
    legacy inverse map (approximation, logged)."""
    src = EntdSource()
    hh = _entd_households()
    p = _entd_persons()
    persons, _ = src.map_person_attributes(p, hh, rng=np.random.RandomState(0))

    assert "economic_status" in persons.columns
    valid = persons["economic_status"].dropna()
    assert len(valid) > 0
    assert set(valid) <= {"very_low", "low", "medium", "high", "very_high"}


def test_popsim_open_economic_status_nan_for_missing_income():
    """Households with ENTD income_class -1 (missing) have household_income=NaN
    and must keep economic_status=NaN (no invented status; schema-optional)."""
    src = EntdSource()
    hh = _entd_households()
    hh["income_class"] = [-1, 10, 13]
    p = _entd_persons()
    persons, _ = src.map_person_attributes(p, hh, rng=np.random.RandomState(0))

    # Household 10 (income_class=-1): persons age 40 and 10 -> NaN status.
    missing = persons[persons["source_household_id"] == 10]["economic_status"]
    assert missing.isna().all(), (
        f"NaN household_income must yield NaN economic_status, got {missing.values}"
    )
    # The other households still receive a valid status.
    present = persons[persons["source_household_id"] != 10]["economic_status"]
    assert present.notna().all()


def test_popsim_open_economic_status_bridge_covers_full_mid_vocabulary():
    """The MiD-label -> H4-class bridge must cover EVERY INCOME_CLASS_BY_GROUP
    label (vocabulary-drift guard) and resolve to a valid 5-class status."""
    from braunschweig.popsim.attributes import INCOME_CLASS_BY_GROUP
    from braunschweig.popsim.sources.entd import _H4_INCOME_CLASS_BY_MID_LABEL
    from braunschweig.synthesis.population.enriched import ECONOMIC_STATUS_BY_INCOME_CLASS

    for label in INCOME_CLASS_BY_GROUP.values():
        assert label in _H4_INCOME_CLASS_BY_MID_LABEL, (
            f"MiD income label {label!r} missing from the economic-status bridge"
        )
        h4_class = _H4_INCOME_CLASS_BY_MID_LABEL[label]
        assert h4_class in ECONOMIC_STATUS_BY_INCOME_CLASS, (
            f"Bridge target {h4_class!r} is not an ECONOMIC_STATUS_BY_INCOME_CLASS key"
        )


def test_entd_pt_subscription_type_defaults():
    """pt_subscription_type default: has_pt_subscription True -> wochen_monat_ohne_abo;
    False -> fahre_nie. Both must be in PT_TICKET_CATEGORIES."""
    from braunschweig.data.mid.reference_tables import PT_TICKET_CATEGORIES

    src = EntdSource()
    hh = _entd_households()
    p = _entd_persons()
    result, _pseudonym_map = src.map_person_attributes(p, hh, rng=np.random.RandomState(0))

    # Persons with has_pt_subscription=True
    sub_true = result[result["has_pt_subscription"] == True]["pt_subscription_type"]
    assert (sub_true == "wochen_monat_ohne_abo").all(), (
        f"Expected wochen_monat_ohne_abo for subscribers, got: {sub_true.unique()}"
    )
    # Persons with has_pt_subscription=False
    sub_false = result[result["has_pt_subscription"] == False]["pt_subscription_type"]
    assert (sub_false == "fahre_nie").all(), (
        f"Expected fahre_nie for non-subscribers, got: {sub_false.unique()}"
    )
    # All values must be valid
    assert result["pt_subscription_type"].isin(PT_TICKET_CATEGORIES).all()


def test_entd_source_provenance_ids_equal_donor_ids():
    """source_person_id and source_household_id must equal the ENTD donor ids
    (ENTD is open data; no pseudonymisation is needed).
    """
    src = EntdSource()
    hh = _entd_households()
    p = _entd_persons()
    result, _pseudonym_map = src.map_person_attributes(p, hh, rng=np.random.RandomState(0))

    # source_person_id must match the ENTD person_id on the joined result
    pd.testing.assert_series_equal(
        result["source_person_id"].reset_index(drop=True),
        result["person_id"].reset_index(drop=True),
        check_names=False,
        obj="source_person_id should equal ENTD person_id (open data, no pseudonymisation)",
    )
    # source_household_id: every row's source_household_id must be the household_id
    # that the ENTD person belongs to (joined from households via person->household_id)
    expected_hh = p.set_index("person_id")["household_id"]
    for _, row in result.iterrows():
        expected = int(expected_hh[row["person_id"]])
        assert row["source_household_id"] == expected, (
            f"Person {row['person_id']}: source_household_id={row['source_household_id']} "
            f"expected {expected}"
        )


def test_entd_household_size_joined_from_households():
    """household_size must come from the households table, not be recomputed."""
    src = EntdSource()
    hh = _entd_households()
    p = _entd_persons()
    result, _pseudonym_map = src.map_person_attributes(p, hh, rng=np.random.RandomState(0))

    # HH 10 has household_size=2 -> both persons (age 40, 10) must have 2
    hh10_sizes = result[result["source_household_id"] == 10]["household_size"]
    assert (hh10_sizes == 2).all(), f"HH 10 household_size expected 2, got {hh10_sizes.values}"

    # HH 30 has household_size=1 -> person (age 70) must have 1
    hh30_sizes = result[result["source_household_id"] == 30]["household_size"]
    assert (hh30_sizes == 1).all(), f"HH 30 household_size expected 1, got {hh30_sizes.values}"


# ---------------------------------------------------------------------------
# build_trips: canonical trip join + contract
# ---------------------------------------------------------------------------

def test_entd_build_trips_returns_contract_columns():
    """build_trips must contain every 11-col synthesis.population.trips CONTRACT column."""
    src = EntdSource()
    persons = _synthetic_persons_with_source_ids()
    trips = _entd_trips()
    result = src.build_trips(persons, trips, random_seed=0)

    missing_cols = [c for c in CONTRACT if c not in result.columns]
    assert not missing_cols, f"build_trips missing contract columns: {missing_cols}"


def test_entd_build_trips_contains_euclidean_distance():
    """build_trips output must include euclidean_distance (present in ENTD trips)."""
    src = EntdSource()
    result = src.build_trips(
        _synthetic_persons_with_source_ids(), _entd_trips(), random_seed=0
    )
    assert "euclidean_distance" in result.columns


def test_entd_build_trips_derives_euclidean_from_routed():
    """When the donor trips lack euclidean_distance (the data.hts.entd.filtered case),
    build_trips must derive it as routed_distance / 1.3 (eqasim ENTD detour factor)."""
    from braunschweig.popsim.sources.entd import ENTD_DETOUR_FACTOR

    src = EntdSource()
    trips = _entd_trips().drop(columns=["euclidean_distance"])  # filtered donor: only routed
    result = src.build_trips(
        _synthetic_persons_with_source_ids(), trips, random_seed=0
    )
    assert "euclidean_distance" in result.columns
    assert result["euclidean_distance"].notna().all()
    expected = result["routed_distance"] / ENTD_DETOUR_FACTOR
    pd.testing.assert_series_equal(
        result["euclidean_distance"].reset_index(drop=True),
        expected.reset_index(drop=True),
        check_names=False,
    )


def test_entd_build_trips_raises_without_any_distance():
    """No euclidean_distance AND no routed_distance -> fail-fast (no silent zero)."""
    src = EntdSource()
    trips = _entd_trips().drop(columns=["euclidean_distance", "routed_distance"])
    with pytest.raises(ValueError, match="routed_distance"):
        src.build_trips(_synthetic_persons_with_source_ids(), trips, random_seed=0)


def test_entd_build_trips_keyed_by_synthetic_person_id():
    """All trip rows must carry the SYNTHETIC person_id (not the ENTD donor id)."""
    src = EntdSource()
    persons = _synthetic_persons_with_source_ids()
    result = src.build_trips(persons, _entd_trips(), random_seed=0)

    expected_ids = set(persons["person_id"].tolist())
    actual_ids = set(result["person_id"].unique())
    assert actual_ids.issubset(expected_ids), (
        f"build_trips person_ids not a subset of synthetic ids: "
        f"{actual_ids - expected_ids}"
    )


def test_entd_build_trips_trip_index_is_cumcount():
    """trip_index must be 0-based cumcount within each synthetic person."""
    src = EntdSource()
    persons = _synthetic_persons_with_source_ids()
    result = src.build_trips(persons, _entd_trips(), random_seed=0)

    for pid, grp in result.groupby("person_id"):
        expected = list(range(len(grp)))
        actual = sorted(grp["trip_index"].tolist())
        assert actual == expected, (
            f"person {pid!r}: expected trip_index {expected}, got {actual}"
        )


def test_entd_build_trips_preserves_mode_purpose_vocab():
    """Mode and purpose labels must be unchanged (ENTD trips are already canonical)."""
    src = EntdSource()
    persons = _synthetic_persons_with_source_ids()
    trips = _entd_trips()
    result = src.build_trips(persons, trips, random_seed=0)

    # Modes in the output must be a subset of what was in the donor trips
    donor_modes = set(trips["mode"].unique())
    assert set(result["mode"].unique()).issubset(donor_modes), (
        "build_trips must not re-map ENTD mode labels"
    )
    donor_purposes = set(trips["following_purpose"].unique()) | set(trips["preceding_purpose"].unique())
    assert set(result["following_purpose"].unique()).issubset(donor_purposes)


def test_entd_build_trips_global_trip_id_is_integer():
    """trip_id must be reassigned as a global sequential integer."""
    src = EntdSource()
    result = src.build_trips(
        _synthetic_persons_with_source_ids(), _entd_trips(), random_seed=0
    )
    assert result["trip_id"].dtype in (np.int32, np.int64, int)
    assert result["trip_id"].is_unique, "Reassigned trip_ids must be globally unique"


def test_entd_build_trips_jitter_applied():
    """Departure and arrival times must differ between two seeds (jitter is active)."""
    src = EntdSource()
    persons = _synthetic_persons_with_source_ids()
    trips = _entd_trips()

    out0 = src.build_trips(persons.copy(), trips.copy(), random_seed=0)
    out1 = src.build_trips(persons.copy(), trips.copy(), random_seed=99)

    # Sort by donor trip id for stable comparison
    dep0 = out0.sort_values("trip_id")["departure_time"].values
    dep1 = out1.sort_values("trip_id")["departure_time"].values
    assert not np.allclose(dep0, dep1), (
        "build_trips departure_times are identical for two different seeds; "
        "jitter may not be applied"
    )


# ---------------------------------------------------------------------------
# build_persons default mapper guard (byte-identical for MiD)
# ---------------------------------------------------------------------------

def test_build_persons_default_mapper_is_mid():
    """build_persons called without attribute_mapper must produce all MiD attribute
    columns (is_urban_resident, source_person_id, weight, …), confirming the
    default path is unchanged (byte-identical guard).
    """
    from braunschweig.popsim import assembly, expand

    merged = pd.DataFrame({
        "ZENSUS100m": ["A"],
        "ZENSUS1km": ["KA"],
        "H_ID": [1],
        "RegionalSchlussel_ARS": ["031010000000"],
    })
    mid_hh = pd.DataFrame({
        "H_ID":       [1],
        "oek_status": [3],
        "hheink_gr1": [4],
        "H_ANZAUTO":  [1],
        "H_ANZRAD":   [2],
    })
    mid_p = pd.DataFrame({
        "H_ID":      [1],
        "P_ID":      [1],
        "HP_ALTER":  [40],
        "HP_SEX":    [1],
        "P_TAET":    [1],
        "P_FSCHEIN": [1],
        "P_FKARTE":  [3],
        "P_BKAT":    [1],
    })

    # Call build_persons without attribute_mapper -> must use MiD default
    persons, _pseudonym_map = assembly.build_persons(
        merged, mid_hh, mid_p, rng=np.random.RandomState(42)
    )

    mid_specific_cols = [
        "economic_status", "household_income_eur",
        "weight", "is_urban_resident",
        "source_person_id", "source_household_id",
    ]
    for col in mid_specific_cols:
        assert col in persons.columns, (
            f"build_persons without attribute_mapper missing MiD column {col!r}"
        )

    # is_urban_resident must be True (commune_id starts with "03101")
    assert (persons["is_urban_resident"] == True).all()
