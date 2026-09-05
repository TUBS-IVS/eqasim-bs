"""Tests for the MiD home-office-day donor pool builder (Phase B Task 2, issue #244).

Tiny synthetic MiD frames: 5 persons in 2 households, 6 trips. Fixture layout:

* Household 1 (H_GR=2, H_ANZAUTO=1 -> has_car True): donor "1_1" (male, 40, P_ARB_ENTF=15.0 ->
  distance_class "10_25") and non-donor "1_2" (age 10 -- makes "1_1"'s has_children_u14 True even
  though "1_2" itself is never selected as a donor). "1_1" has an active-escort trip (W_ZWECK=6)
  plus a home trip.
* Household 2 (H_GR=3, H_ANZAUTO=0 -> has_car False): three donors --
  "2_1" (female, P_ARB_ENTF=200.0 top-code -> distance_class "100_200" via P_ARB_ENTF, even
  though it also has a work trip with a different wegkm, to prove P_ARB_ENTF precedence),
  "2_2" (HP_SEX=9 -> sex unknown; P_ARB_ENTF missing code 999 -> falls back to its first work
  trip length 200.0 km, classified WITHOUT the MiD top-code -> distance_class "gt200"), and
  "2_3" (immobile: no MiD trips at all, no valid P_ARB_ENTF -> distance_class/source "unknown").

Donor "2_1" additionally carries M_HOFF=2 (not flagged as a home-office-module person) to
exercise the n_not_in_module diagnostic/warning even though starb2 alone already selected it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.synthesis.commute_day import donor_pool


def _persons_fixture() -> pd.DataFrame:
    return pd.DataFrame({
        "H_ID":        [1, 1, 2, 2, 2],
        "P_ID":        [1, 2, 1, 2, 3],
        "HP_ID":       ["1_1", "1_2", "2_1", "2_2", "2_3"],
        "HP_SEX":      [1, 2, 2, 9, 1],
        "HP_ALTER":    [40, 10, 35, 50, 30],
        "arbwo":       [1, 2, 1, 1, 1],
        "P_STARB1":    [1, 9, 1, 1, 1],
        "starb2":      [1, 9, 1, 1, 1],
        "M_HOFF":      [1, 0, 2, 1, 1],
        "P_ARB_ENTF":  [15.0, np.nan, 200.0, 999.0, np.nan],
    })


def _households_fixture() -> pd.DataFrame:
    return pd.DataFrame({
        "H_ID": [1, 2],
        "H_ANZAUTO": [1, 0],
        "H_GR": [2, 3],
    })


def _wege_fixture() -> pd.DataFrame:
    # 6 trips: 2 for donor "1_1" (escort + home), 2 for "2_1" (work + home),
    # 2 for "2_2" (work + home). Donor "2_3" is immobile (no rows).
    return pd.DataFrame({
        "H_ID":      [1, 1, 2, 2, 2, 2],
        "P_ID":      [1, 1, 1, 1, 2, 2],
        "W_ID":      [1, 2, 1, 2, 1, 2],
        "W_ZWECK":   [6, 8, 1, 8, 1, 8],
        "hvm_imp":   [4, 4, 4, 4, 4, 4],
        "W_SZS":     [7, 7, 8, 17, 8, 17],
        "W_SZM":     [0, 30, 0, 0, 0, 0],
        "W_AZS":     [7, 7, 8, 17, 8, 17],
        "W_AZM":     [15, 45, 30, 30, 40, 40],
        "wegkm":     [3.0, 3.0, 100.0, 100.0, 200.0, 5.0],
        "wegkm_imp": [3.0, 3.0, 100.0, 100.0, 200.0, 5.0],
    })


def test_select_home_office_day_donors_filters_on_weekday_worked_at_home():
    persons = _persons_fixture()
    donors = donor_pool.select_home_office_day_donors(persons)
    assert set(donors["HP_ID"]) == {"1_1", "2_1", "2_2", "2_3"}
    assert "1_2" not in set(donors["HP_ID"])


def test_donor_attributes_child_flag_from_non_donor_household_member():
    persons = _persons_fixture()
    donors = donor_pool.select_home_office_day_donors(persons)
    attributes = donor_pool.donor_attributes(donors, persons, _households_fixture(), _wege_fixture())
    row = attributes.set_index("donor_id").loc["1_1"]
    # "1_2" (age 10, never a donor) is the only reason "1_1" has_children_u14 is True.
    assert bool(row["has_children_u14"]) is True
    assert bool(row["has_car"]) is True
    assert bool(row["has_active_escort"]) is True
    assert row["household_size"] == 2
    assert row["sex"] == "male"
    assert row["age"] == 40
    assert row["employed"] is True or row["employed"] == True  # noqa: E712


def test_donor_attributes_distance_precedence_p_arb_entf_before_trip_length():
    persons = _persons_fixture()
    donors = donor_pool.select_home_office_day_donors(persons)
    attributes = donor_pool.donor_attributes(donors, persons, _households_fixture(), _wege_fixture())
    by_id = attributes.set_index("donor_id")

    # "2_1": valid P_ARB_ENTF = 200.0 (top-coded) -> "100_200", even though its own work trip
    # (wegkm=100.0) would classify differently -- P_ARB_ENTF must win.
    row_2_1 = by_id.loc["2_1"]
    assert row_2_1["distance_class"] == "100_200"
    assert row_2_1["distance_source"] == "P_ARB_ENTF"
    assert bool(row_2_1["has_car"]) is False
    assert bool(row_2_1["has_children_u14"]) is False
    assert bool(row_2_1["has_active_escort"]) is False
    assert row_2_1["household_size"] == 3

    # "2_2": P_ARB_ENTF is a missing code (999) -> falls back to the first work-trip length
    # (200.0 km), classified WITHOUT the MiD top-code -> "gt200", not "100_200".
    row_2_2 = by_id.loc["2_2"]
    assert row_2_2["distance_class"] == "gt200"
    assert row_2_2["distance_source"] == "trip_length"
    assert pd.isna(row_2_2["sex"])

    # "2_3": immobile, no valid P_ARB_ENTF and no work trip at all -> unknown.
    row_2_3 = by_id.loc["2_3"]
    assert pd.isna(row_2_3["distance_class"])
    assert row_2_3["distance_source"] == "unknown"
    assert pd.isna(row_2_3["distance_km"])


def test_donor_trips_immobile_donor_yields_no_rows_but_others_get_contract_columns():
    persons = _persons_fixture()
    donors = donor_pool.select_home_office_day_donors(persons)
    trips = donor_pool.donor_trips(
        donors, _wege_fixture(), random_seed=0,
        escort_purpose=False, escort_passive_education=False,
        explicit_round_trip_purposes=True,
    )
    assert set(trips["donor_id"].unique()) == {"1_1", "2_1", "2_2"}
    assert "2_3" not in set(trips["donor_id"].unique())
    assert len(trips) == 6

    expected_contract_columns = [
        "donor_id", "trip_index", "departure_time", "arrival_time",
        "preceding_purpose", "following_purpose", "is_first_trip", "is_last_trip",
        "trip_duration", "activity_duration", "mode",
    ]
    for column in expected_contract_columns:
        assert column in trips.columns
    assert "euclidean_distance" in trips.columns
    assert "trip_key" in trips.columns
    # Raw MiD extras (e.g. W_ZWECK, hvm_imp, wegkm) must be dropped from the donor trips.
    assert "W_ZWECK" not in trips.columns
    assert "hvm_imp" not in trips.columns


def test_build_home_office_donor_pool_diagnostics_and_shapes():
    persons = _persons_fixture()
    wege = _wege_fixture()
    households = _households_fixture()

    attributes, trips, diagnostics = donor_pool.build_home_office_donor_pool(
        persons, wege, households, random_seed=0,
        escort_purpose=False, escort_passive_education=False,
        explicit_round_trip_purposes=True,
    )

    assert len(attributes) == 4
    assert set(attributes["donor_id"]) == {"1_1", "2_1", "2_2", "2_3"}
    assert set(trips["donor_id"].unique()) == {"1_1", "2_1", "2_2"}

    assert diagnostics["n_donors"] == 4
    assert diagnostics["n_immobile"] == 1
    assert diagnostics["n_missing_distance"] == 1
    assert diagnostics["n_sex_unknown"] == 1
    assert diagnostics["n_not_in_module"] == 1
    assert diagnostics["distance_source_counts"] == {
        "P_ARB_ENTF": 2, "trip_length": 1, "unknown": 1,
    }

    cells = diagnostics["cells"]
    assert sum(cells.values()) == 4
    assert cells[("10_25", True, True)] == 1
    assert cells[("100_200", False, False)] == 1
    assert cells[("gt200", False, False)] == 1
    assert cells[(None, False, False)] == 1
