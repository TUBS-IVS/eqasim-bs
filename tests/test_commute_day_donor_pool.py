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
    assert bool(row["employed"]) is True


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

    # "2_3": immobile, no valid P_ARB_ENTF and no work trip at all -> the LITERAL string
    # "unknown" (never None/NaN -- the later matching module treats "unknown" as "matches any
    # class", which a null value would not).
    row_2_3 = by_id.loc["2_3"]
    assert row_2_3["distance_class"] == donor_pool.DISTANCE_CLASS_UNKNOWN
    assert row_2_3["distance_source"] == "unknown"
    assert pd.isna(row_2_3["distance_km"])

    # distance_class must never be null for ANY donor, mobile or not.
    assert attributes["distance_class"].notna().all()


def test_donor_trips_immobile_donor_yields_no_rows_but_others_get_contract_columns():
    persons = _persons_fixture()
    donors = donor_pool.select_home_office_day_donors(persons)
    attributes = donor_pool.donor_attributes(donors, persons, _households_fixture(), _wege_fixture())
    trips = donor_pool.donor_trips(
        donors, attributes, _wege_fixture(), random_seed=0,
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


def test_donor_trips_hands_the_resample_a_sex_column_without_mixed_types(monkeypatch):
    """Regression: a `sex` column mixing NaN with "male"/"female" kills the whole donor build.

    Stage B of ``braunschweig.popsim.trips``'s resample cascade matches on ``sex``, and eqasim's
    ``statistical_matching`` sorts the union of each matching column's distinct values -- so
    NaN next to strings raises ``TypeError: '<' not supported between instances of 'float' and
    'str'``. MEASURED on the real MiD delivery (2026-09-05): 12 of 8,026 home-office-day donors
    carry an HP_SEX code outside {1, 2}, which aborted the first 100 % ON proof run. The tiny
    fixtures never reach stage B, so the invariant is pinned at the seam instead: the persons
    frame handed to the trip-table builder must carry an explicit label, while the ATTRIBUTES
    frame keeps NaN for every other consumer.
    """
    captured = {}
    real_builder = donor_pool.build_validated_trip_table

    def capturing_builder(persons, wege, **kwargs):
        captured["persons"] = persons.copy()
        return real_builder(persons, wege, **kwargs)

    monkeypatch.setattr(donor_pool, "build_validated_trip_table", capturing_builder)

    persons = _persons_fixture()
    donors = donor_pool.select_home_office_day_donors(persons)
    attributes = donor_pool.donor_attributes(donors, persons, _households_fixture(),
                                             _wege_fixture())
    assert attributes["sex"].isna().any(), "the fixture must contain a donor with unknown sex"

    donor_pool.donor_trips(
        donors, attributes, _wege_fixture(), random_seed=0,
        escort_purpose=False, escort_passive_education=False,
        explicit_round_trip_purposes=True,
    )

    sex = captured["persons"]["sex"]
    assert sex.notna().all()
    assert donor_pool.SEX_LABEL_UNKNOWN in set(sex.unique())
    # The union of the distinct values must be sortable -- exactly what statistical_matching does.
    sorted(set(sex.unique()))
    # The attributes frame is NOT rewritten: NaN stays the documented value there.
    assert attributes["sex"].isna().any()


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
    assert diagnostics["n_chain_dropped_by_resample"] == 0
    assert diagnostics["n_missing_distance"] == 1
    assert diagnostics["n_sex_unknown"] == 1
    assert diagnostics["n_not_in_module"] == 1
    assert diagnostics["n_household_unmatched"] == 0
    assert diagnostics["distance_source_counts"] == {
        "P_ARB_ENTF": 2, "trip_length": 1, "unknown": 1,
    }

    cells = diagnostics["cells"]
    assert sum(cells.values()) == 4
    assert cells[("10_25", True, True)] == 1
    assert cells[("100_200", False, False)] == 1
    assert cells[("gt200", False, False)] == 1
    assert cells[("unknown", False, False)] == 1
    assert None not in {key[0] for key in cells}


def test_donor_attributes_warns_and_nans_has_car_when_household_missing():
    persons = _persons_fixture()
    donors = donor_pool.select_home_office_day_donors(persons)
    # Only household 1's row is present -- every household-2 donor ("2_1", "2_2", "2_3") has an
    # H_ID absent from the households frame.
    households_missing_h2 = _households_fixture().loc[lambda df: df["H_ID"] == 1]
    attributes = donor_pool.donor_attributes(donors, persons, households_missing_h2, _wege_fixture())
    by_id = attributes.set_index("donor_id")

    assert bool(by_id.loc["1_1", "has_car"]) is True
    for donor_id in ("2_1", "2_2", "2_3"):
        assert pd.isna(by_id.loc[donor_id, "has_car"])


def test_build_home_office_donor_pool_reports_household_unmatched_count():
    persons = _persons_fixture()
    wege = _wege_fixture()
    households_missing_h2 = _households_fixture().loc[lambda df: df["H_ID"] == 1]

    _attributes, _trips, diagnostics = donor_pool.build_home_office_donor_pool(
        persons, wege, households_missing_h2, random_seed=0,
        escort_purpose=False, escort_passive_education=False,
        explicit_round_trip_purposes=True,
    )
    assert diagnostics["n_household_unmatched"] == 3


def test_donor_attributes_works_with_a_shuffled_non_range_index():
    persons = _persons_fixture()
    donors = donor_pool.select_home_office_day_donors(persons)
    # A subset/reorder that leaves a non-contiguous, shuffled index (e.g. after a boolean mask
    # elsewhere in a caller's pipeline) must not break row alignment.
    shuffled_donors = donors.sample(frac=1.0, random_state=1)
    assert not shuffled_donors.index.equals(pd.RangeIndex(len(shuffled_donors)))

    attributes = donor_pool.donor_attributes(
        shuffled_donors, persons, _households_fixture(), _wege_fixture())
    row = attributes.set_index("donor_id").loc["1_1"]
    assert bool(row["has_children_u14"]) is True
    assert bool(row["has_car"]) is True
    assert bool(row["has_active_escort"]) is True
    assert row["distance_class"] == "10_25"


def test_build_home_office_donor_pool_distinguishes_chain_dropped_from_immobile():
    """A donor with real Wege rows whose chain cannot be repaired or attribute-matched must be
    counted as n_chain_dropped_by_resample, never as n_immobile (fix round 1 item 2)."""
    persons = pd.DataFrame({
        "H_ID":        [20, 21],
        "P_ID":        [1, 1],
        "HP_ID":       ["20_1", "21_1"],
        "HP_SEX":      [1, 2],   # different sex -> stage B's first (never-relaxed) matching key
        "HP_ALTER":    [40, 45],  # is infeasible for "21_1", so it is guaranteed to be dropped,
        "arbwo":       [1, 1],    # never repaired into a valid chain by chance.
        "P_STARB1":    [1, 1],
        "starb2":      [1, 1],
        "M_HOFF":      [1, 1],
        "P_ARB_ENTF":  [15.0, 20.0],
    })
    households = pd.DataFrame({"H_ID": [20, 21], "H_ANZAUTO": [1, 1], "H_GR": [1, 1]})
    wege = pd.DataFrame({
        "H_ID":      [20, 20, 21, 21],
        "P_ID":      [1, 1, 1, 1],
        "W_ID":      [1, 2, 1, 2],
        "W_ZWECK":   [1, 8, 1, 8],
        "hvm_imp":   [4, 4, 4, 4],
        # Donor "21_1" has BOTH trips carrying the MiD "keine Angabe" coded time (99) in every
        # time field -> mid_time_seconds NaNs both departure and arrival -> PlanValidator's
        # "nan_times" issue -> unfixable. No wegmin_imp1 column at all, so stage A (time
        # imputation) is skipped; stage B (attribute-matched chain replacement) then tries to
        # match "21_1" (sex "female") against the only other donor, "20_1" (sex "male") -- the
        # never-relaxed first key -- and fails deterministically.
        "W_SZS":     [8, 17, 99, 99],
        "W_SZM":     [0, 0, 99, 99],
        "W_AZS":     [8, 17, 99, 99],
        "W_AZM":     [30, 30, 99, 99],
        "wegkm":     [5.0, 5.0, 5.0, 5.0],
        "wegkm_imp": [5.0, 5.0, 5.0, 5.0],
    })

    attributes, trips, diagnostics = donor_pool.build_home_office_donor_pool(
        persons, wege, households, random_seed=0,
        escort_purpose=False, escort_passive_education=False,
        explicit_round_trip_purposes=True,
    )

    assert len(attributes) == 2
    assert "21_1" not in set(trips["donor_id"].unique())
    assert diagnostics["n_immobile"] == 0
    assert diagnostics["n_chain_dropped_by_resample"] == 1
