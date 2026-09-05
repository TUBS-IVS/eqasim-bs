"""Unit tests for braunschweig.calibration.commute_day_state_reference (synthetic frames)."""
import logging

import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration import commute_day_state_reference as R


@pytest.mark.parametrize("km, label", [
    (3.0, "lt10"), (10.0, "10_25"), (24.9, "10_25"), (25.0, "25_50"), (99.9, "50_100"),
    (100.0, "100_200"), (200.0, "100_200"), (250.0, "gt200"), (0.0, None), (-1.0, None), (np.nan, None),
])
def test_classify_commute_distance(km, label):
    assert R.classify_commute_distance(km) == label


def test_classify_commute_distance_topcode_km_none_disables_the_special_case():
    # Finding 5: the MiD 200 km top-code special case must be OPT-OUT-able for a distance source
    # (e.g. a raw trip length, wegkm) that was never subject to MiD top-coding.
    assert R.classify_commute_distance(200.0, topcode_km=None) == "gt200"
    # The default keeps today's behaviour unchanged for every existing caller.
    assert R.classify_commute_distance(200.0) == "100_200"
    assert R.classify_commute_distance(200.0, topcode_km=R.MID_DISTANCE_TOPCODE_KM) == "100_200"
    # Distances away from the top-code boundary are unaffected by the parameter either way.
    assert R.classify_commute_distance(150.0, topcode_km=None) == "100_200"


@pytest.mark.parametrize("km, label", [
    (3.0, "lt10"), (10.0, "lt10"), (10.1, "10_25"), (25.0, "10_25"), (25.1, "25_50"),
    (50.0, "25_50"), (100.0, "50_100"), (100.1, "100_200"), (200.0, "100_200"), (250.0, "gt200"),
    (0.0, None), (-1.0, None), (np.nan, None),
])
def test_classify_commute_distance_right_inclusive(km, label):
    assert R.classify_commute_distance_right_inclusive(km) == label


@pytest.mark.parametrize("raw_km, cleaned_km", [
    (996.0, None), (999.0, None), (2202.0, None), (200.0, 200.0), (15.5, 15.5),
])
def test_clean_mid_commute_distance_km(raw_km, cleaned_km):
    cleaned = R.clean_mid_commute_distance_km([raw_km])
    if cleaned_km is None:
        assert pd.isna(cleaned.iloc[0])
    else:
        assert cleaned.iloc[0] == pytest.approx(cleaned_km)


def _mid_persons():
    # 6 weekday module persons: 4 at 5 km (2 workplace, 1 home, 1 not worked), 2 at 150 km (1 home, 1 other);
    # row 6 is a weekend row (excluded); row 7 has P_STARB1 == 9 ("no answer" -> state-missing) at 60 km;
    # row 8 has a P_STARB1 filter code (202, "not employed/not asked" -> excluded from the universe).
    return pd.DataFrame({
        "P_GEW":     [1.0, 1.0, 2.0, 1.0, 1.0, 1.0, 5.0, 1.0, 1.0],
        "arbwo":     [1,   1,   1,   1,   1,   1,   0,   1,   1],
        "M_HOFF":    [1,   1,   1,   1,   1,   1,   1,   1,   1],
        "P_STARB1":  [1,   1,   1,   2,   1,   1,   1,   9,   202],
        "starb2":    [2,   2,   1,   409, 1,   3,   2,   1,   1],
        "P_ARB_ENTF":[5.0, 6.0, 4.0, 7.0, 150.0, 120.0, 3.0, 60.0, 10.0],
    })


def test_build_mid_workday_location_table_shares():
    t = R.build_mid_workday_location_table(_mid_persons()).set_index("distance_class")
    lt10 = t.loc["lt10"]
    assert lt10["n_unweighted"] == 4
    assert lt10["share_at_workplace"] == pytest.approx(2 / 5)      # weights 1+1 of 5
    assert lt10["share_at_home"] == pytest.approx(2 / 5)           # weight 2
    assert lt10["share_did_not_work"] == pytest.approx(1 / 5)
    far = t.loc["100_200"]
    assert far["share_at_home"] == pytest.approx(0.5) and far["share_other_place"] == pytest.approx(0.5)
    # Row 7 (P_STARB1 == 9, "no answer") is the only 50-100 km person; its state is undetermined,
    # so share_missing on that row is 1.0 (Ruling R4: state-missing, non-zero).
    fifty_to_hundred = t.loc["50_100"]
    assert fifty_to_hundred["n_unweighted"] == 1
    assert fifty_to_hundred["share_missing"] == pytest.approx(1.0)
    share_cols = list(R.SHARE_COLUMNS)
    assert np.allclose(t[share_cols].sum(axis=1), 1.0)
    # Row 8 (P_STARB1 == 202, a "not employed/not asked" filter code) is outside the universe and
    # must not be counted anywhere: 7 = 9 input rows - 1 weekend (row 6) - 1 filter code (row 8).
    assert "all" in t.index and t.loc["all", "n_unweighted"] == 7


def test_build_mid_workday_location_table_missing_distance_row():
    p = _mid_persons(); p.loc[0, "P_ARB_ENTF"] = 996.0
    t = R.build_mid_workday_location_table(p).set_index("distance_class")
    assert t.loc["all", "n_missing_distance"] == 1
    # Distance-missing (row 0) does not affect share_missing -- only row 7's state-missing
    # (P_STARB1 == 9) does: weight 1 of the universe's total weight 8 (1+1+2+1+1+1+1).
    assert t.loc["all", "share_missing"] == pytest.approx(1 / 8)
    assert "lt10" in t.index and t.loc["lt10", "n_unweighted"] == 3


def test_inconsistent_starb1_starb2_pair_logs_warning(caplog):
    p = _mid_persons()
    # Row 3 (P_STARB1 == 2, starb2 == 409) is consistent; flip its starb2 so P_STARB1 == 2 with
    # starb2 != 409, which must be flagged as an inconsistent pair (Ruling R4).
    p.loc[3, "starb2"] = 2
    with caplog.at_level(logging.WARNING):
        R.build_mid_workday_location_table(p)
    assert any("consistency check" in message for message in caplog.messages)


def test_load_workday_location_table_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        R.load_workday_location_table(tmp_path)


def _boundary_persons():
    """Four universe persons whose commute distances straddle class boundaries (finding 2): A/D
    sit exactly on a boundary and swap classes between the two bin conventions, while C (100 km,
    a class boundary too) moves out of the class it shares with nobody else under the other
    convention. Distinct states per person make a nonzero share deviation checkable directly."""
    return pd.DataFrame({
        "P_GEW":     [1.0, 1.0, 1.0, 1.0],
        "arbwo":     [1,   1,   1,   1],
        "M_HOFF":    [1,   1,   1,   1],
        "P_STARB1":  [1,   1,   1,   2],
        "starb2":    [2,   1,   3,   409],
        "P_ARB_ENTF":[10.0, 15.0, 100.0, 5.0],
    })


def test_measure_bin_convention_deviation_counts_and_max_deviation():
    deviation = R.measure_bin_convention_deviation(_boundary_persons())

    # Left-inclusive [a, b) (production, classify_commute_distance): A(10)->10_25, B(15)->10_25,
    # C(100)->100_200, D(5)->lt10.
    left = deviation["left_inclusive_n_unweighted"]
    assert left["lt10"] == 1 and left["10_25"] == 2 and left.get("100_200", 0) == 1
    assert left["all"] == 4

    # Right-inclusive (a, b] (classify_commute_distance_right_inclusive): A(10)->lt10 (moves in
    # with D), B(15)->10_25 (stays), C(100)->50_100 (moves out of 100_200 entirely).
    right = deviation["right_inclusive_n_unweighted"]
    assert right["lt10"] == 2 and right["10_25"] == 1 and right.get("50_100", 0) == 1
    assert right.get("100_200", 0) == 0  # absent: build_mid_workday_location_table never emits
                                         # an empty row, so C's departure leaves no 100_200 row.
    assert right["all"] == 4

    # The "10_25" class holds a DIFFERENT population under each convention (left: A+B, right:
    # B only) with different reporting-day states, so its weighted shares genuinely differ --
    # this is a MEASURED, not hand-typed, deviation.
    assert deviation["max_abs_share_deviation"] == pytest.approx(0.5)


def test_measure_bin_convention_deviation_is_zero_when_bins_do_not_move_membership():
    # A single-person universe: the "all" row is the only row present under both conventions and
    # its own shares cannot depend on which OTHER class the person's distance falls into.
    persons = pd.DataFrame({
        "P_GEW": [1.0], "arbwo": [1], "M_HOFF": [1], "P_STARB1": [1], "starb2": [2],
        "P_ARB_ENTF": [50.0],
    })
    deviation = R.measure_bin_convention_deviation(persons)
    assert deviation["max_abs_share_deviation"] == pytest.approx(0.0)


def _mid_pool_persons():
    return pd.DataFrame({
        "HP_ID":   [1, 2, 3, 4, 5],
        "H_ID":    [10, 10, 20, 30, 30],
        "P_GEW":   [1.0] * 5,
        "arbwo":   [1, 1, 1, 1, 1],
        "M_HOFF":  [1, 0, 1, 1, 1],
        "P_STARB1":[1, 2, 1, 1, 1],
        "starb2":  [1, 409, 1, 2, 1],
        "P_ARB_ENTF": [8.0, 0.0, 150.0, 30.0, 996.0],
        "HP_ALTER":[40, 6, 35, 50, 45],
        "HP_SEX":  [2, 1, 1, 1, 2],
    })


def _mid_pool_trips():
    return pd.DataFrame({"HP_ID": [1, 1, 1, 3, 3], "W_ZWECK": [6, 4, 8, 7, 8]})


def test_build_mid_home_office_donor_pool_cells():
    pool = R.build_mid_home_office_donor_pool(_mid_pool_persons(), _mid_pool_trips())
    total = pool[(pool["distance_class"] == "all") & (pool["has_children"] == "all")].iloc[0]
    assert total["n_donors"] == 3            # HP 1 (8 km, escort, child in hh), HP 3 (150 km), HP 5 (missing distance)
    lt10_mask = (
        (pool["distance_class"] == "lt10")
        & (pool["has_children"] == True)  # noqa: E712
        & (pool["has_active_escort"] == True)  # noqa: E712
    )
    lt10 = pool[lt10_mask].iloc[0]
    assert lt10["n_donors"] == 1 and lt10["n_mobile"] == 1 and lt10["mean_trips_mobile"] == pytest.approx(3.0)
    far_mask = (
        (pool["distance_class"] == "100_200")
        & (pool["has_children"] == False)  # noqa: E712
        & (pool["has_active_escort"] == False)  # noqa: E712
    )
    far = pool[far_mask].iloc[0]
    assert far["n_donors"] == 1 and far["share_female"] == pytest.approx(0.0)
    missing = pool[(pool["distance_class"] == "missing") & (pool["has_children"] == "all")].iloc[0]
    assert missing["n_donors"] == 1 and missing["n_mobile"] == 0


def _donor_workers():
    """Six synthetic workers: 2 equal classes, 2 assigned-above-donor, 1 assigned-below, 1 donor
    distance missing (see ``_donor_donors`` for the matching donor distances)."""
    return pd.DataFrame({
        "person_id": [1, 2, 3, 4, 5, 6],
        "hts_id": ["a", "b", "c", "d", "e", "f"],
        "assigned_distance_class": ["lt10", "25_50", "25_50", "100_200", "10_25", "50_100"],
    })


def _donor_donors():
    return pd.DataFrame({
        "hts_id": ["a", "b", "c", "d", "e", "f"],
        # a 5 km -> lt10 (equal), b 30 km -> 25_50 (equal), c 5 km -> lt10 (assigned above),
        # d 12 km -> 10_25 (assigned above), e 60 km -> 50_100 (assigned below), f NaN -> missing.
        "donor_distance_km": [5.0, 30.0, 5.0, 12.0, 60.0, np.nan],
        "donor_worked_on_day": [1, 1, 2, 1, 1, 9],
        "donor_starb2": [2, 1, 409, 2, 3, 99],
    })


def test_donor_vs_assigned_class_counts():
    cross_tab, diagnostics = R.donor_vs_assigned_class(_donor_workers(), _donor_donors())

    assert diagnostics["n_workers"] == 6
    assert diagnostics["n_matched_donor"] == 6
    assert diagnostics["n_donor_distance_missing"] == 1
    assert diagnostics["n_assigned_class_missing"] == 0
    assert diagnostics["n_comparable"] == 5
    assert diagnostics["n_assigned_gt_donor"] == 2
    assert diagnostics["n_assigned_lt_donor"] == 1
    assert diagnostics["n_assigned_eq_donor"] == 2
    assert diagnostics["share_assigned_gt_donor"] == pytest.approx(2 / 5)
    assert diagnostics["n_assigned_gt_donor_by_assigned_class"] == {"25_50": 1, "100_200": 1}
    # Donor reporting-day states over the matched workers: 4 worked (a, b, d, e), 1 did not (c),
    # 1 no answer (f); of the workers, a and d were at the workplace and b at home.
    assert diagnostics["n_donor_worked_on_day"] == 4
    assert diagnostics["n_donor_did_not_work_on_day"] == 1
    assert diagnostics["n_donor_at_home"] == 1
    assert diagnostics["n_donor_at_workplace"] == 2

    indexed = cross_tab.set_index("donor_distance_class")
    assert indexed.loc["lt10", "n_donor_total"] == 2
    assert indexed.loc["lt10", "n_lt10"] == 1 and indexed.loc["lt10", "n_25_50"] == 1
    assert indexed.loc["lt10", "share_lt10"] == pytest.approx(0.5)
    assert indexed.loc["10_25", "n_100_200"] == 1
    assert indexed.loc["25_50", "n_25_50"] == 1
    assert indexed.loc["50_100", "n_10_25"] == 1
    assert indexed.loc["missing", "n_donor_total"] == 1
    assert indexed.loc["missing", "n_50_100"] == 1
    assert indexed.loc["all", "n_donor_total"] == 6
    # Every class row and column is emitted, also the empty ones (stable table shape).
    assert list(indexed.index) == list(R.COMMUTE_CLASS_LABELS) + ["missing", "all"]
    assert indexed.loc["gt200", "n_donor_total"] == 0


def test_donor_vs_assigned_class_forwards_topcode_km_to_the_donor_classification():
    # Finding 5: a donor distance of exactly 200.0 km from a source that was never MiD top-coded
    # (e.g. a trip length) must classify as "gt200", not "100_200", when topcode_km=None; the
    # default keeps today's behaviour ("100_200") unchanged.
    workers = pd.DataFrame({
        "person_id": [1], "hts_id": ["a"], "assigned_distance_class": ["gt200"],
    })
    donors = pd.DataFrame({
        "hts_id": ["a"], "donor_distance_km": [200.0],
        "donor_worked_on_day": [1], "donor_starb2": [2],
    })
    _, default_diag = R.donor_vs_assigned_class(workers, donors)
    assert default_diag["n_assigned_eq_donor"] == 0
    assert default_diag["n_assigned_gt_donor"] == 1  # gt200 (assigned) > 100_200 (donor, default)

    _, none_diag = R.donor_vs_assigned_class(workers, donors, topcode_km=None)
    assert none_diag["n_assigned_eq_donor"] == 1  # gt200 (assigned) == gt200 (donor, topcode_km=None)
    assert none_diag["n_assigned_gt_donor"] == 0


def test_donor_vs_assigned_class_unmatched_workers_are_excluded_and_counted():
    workers = _donor_workers()
    workers.loc[len(workers)] = [7, "no-such-donor", "lt10"]
    cross_tab, diagnostics = R.donor_vs_assigned_class(workers, _donor_donors())
    assert diagnostics["n_workers"] == 7 and diagnostics["n_matched_donor"] == 6
    assert cross_tab.set_index("donor_distance_class").loc["all", "n_donor_total"] == 6


def test_donor_vs_assigned_class_rejects_duplicate_donors():
    donors = pd.concat([_donor_donors(), _donor_donors().head(1)], ignore_index=True)
    with pytest.raises(ValueError, match="unique on hts_id"):
        R.donor_vs_assigned_class(_donor_workers(), donors)


def test_donor_vs_assigned_class_requires_columns():
    with pytest.raises(ValueError, match="assigned_distance_class"):
        R.donor_vs_assigned_class(_donor_workers().drop(columns=["assigned_distance_class"]),
                                  _donor_donors())


def test_donor_vs_assigned_class_warns_above_missing_share(caplog):
    # Four of six donors lose their distance -> 4/6 = 0.667 > the 0.5 default threshold.
    donors = _donor_donors()
    donors.loc[[0, 1, 2], "donor_distance_km"] = np.nan
    with caplog.at_level(logging.WARNING):
        _, diagnostics = R.donor_vs_assigned_class(_donor_workers(), donors)
    assert diagnostics["n_donor_distance_missing"] == 4
    assert diagnostics["share_donor_distance_missing"] == pytest.approx(4 / 6)
    assert diagnostics["warn_missing_share"] == pytest.approx(0.5)
    assert any("NOT necessarily representative" in message for message in caplog.messages)


def test_donor_vs_assigned_class_does_not_warn_below_missing_share(caplog):
    with caplog.at_level(logging.WARNING):
        _, diagnostics = R.donor_vs_assigned_class(_donor_workers(), _donor_donors())
    assert diagnostics["share_donor_distance_missing"] == pytest.approx(1 / 6)
    assert caplog.messages == []


def test_donor_vs_assigned_class_warn_threshold_is_configurable(caplog):
    with caplog.at_level(logging.WARNING):
        R.donor_vs_assigned_class(_donor_workers(), _donor_donors(), warn_missing_share=0.1)
    assert any("NOT necessarily representative" in message for message in caplog.messages)


def test_donor_vs_assigned_class_survives_a_non_default_worker_index():
    # Ruling: the class series must be built on the merged frame's index, not a fresh RangeIndex.
    workers = _donor_workers()
    workers.index = [100, 101, 102, 103, 104, 105]
    _, diagnostics = R.donor_vs_assigned_class(workers, _donor_donors())
    assert diagnostics["n_assigned_gt_donor"] == 2 and diagnostics["n_comparable"] == 5


def _worker_donors():
    """Five workers with their donor's module flag, distance and reporting-day codes joined on."""
    return pd.DataFrame({
        "donor_distance_km": [5.0, 30.0, np.nan, np.nan, 12.0],
        "donor_in_home_office_module": [1, 1, 1, 0, 0],
        "donor_reporting_day_weekday": [1, 1, 2, 1, np.nan],
        "donor_reporting_day_of_week": [1, 3, 6, 5, 5],
    })


def test_donor_universe_diagnostics_counts():
    universe = R.donor_universe_diagnostics(_worker_donors())
    assert universe["n_workers"] == 5
    assert universe["n_in_home_office_module"] == 3
    assert universe["n_not_in_home_office_module"] == 2
    assert universe["n_module_flag_other"] == 0
    assert universe["n_distance_valid"] == 3
    assert universe["share_distance_valid"] == pytest.approx(3 / 5)
    # P_ARB_ENTF can only be valid inside the module -- but the helper MEASURES that rather than
    # assuming it, so this fixture deliberately gives one out-of-module donor a distance.
    assert universe["n_distance_valid_in_module"] == 2
    assert universe["share_distance_valid_in_module"] == pytest.approx(2 / 3)
    assert universe["n_distance_valid_not_in_module"] == 1
    assert universe["share_distance_valid_not_in_module"] == pytest.approx(0.5)
    assert universe["n_by_reporting_day_weekday"] == {"1": 3, "2": 1, "missing": 1}
    assert universe["n_by_reporting_day_of_week"] == {"1": 1, "3": 1, "5": 2, "6": 1}


def test_donor_universe_diagnostics_requires_columns():
    with pytest.raises(ValueError, match="donor_in_home_office_module"):
        R.donor_universe_diagnostics(_worker_donors().drop(columns=["donor_in_home_office_module"]))


def _mid_trips_for_length():
    return pd.DataFrame({
        "H_ID":    [1, 1, 1, 2, 2, 3, 4, 4],
        "P_ID":    [1, 1, 2, 1, 1, 1, 1, 1],
        # H1/P1: a non-work trip, then two work trips -> the FIRST work trip (12.5 km) wins.
        # H1/P2: work trip with a filter-coded length (>= 1000) -> no length.
        # H2/P1: a zero-length work trip then a valid one -> the zero is skipped.
        # H3/P1: only a business trip (W_ZWECK 2) -> no length, business is not the commute.
        # H4/P1: two work trips, first 4.0 km.
        "W_ZWECK": [7, 1, 1, 1, 1, 2, 1, 1],
        "wegkm":   [3.0, 12.5, 9999.0, 0.0, 8.0, 40.0, 4.0, 44.0],
    })


def test_first_work_trip_length_km_keeps_the_first_valid_work_trip():
    lengths = R.first_work_trip_length_km(_mid_trips_for_length())
    assert len(lengths) == 3
    indexed = lengths.set_index(["H_ID", "P_ID"])[R.WORK_TRIP_LENGTH_COLUMN]
    assert indexed.loc[(1, 1)] == pytest.approx(12.5)
    assert indexed.loc[(2, 1)] == pytest.approx(8.0)
    assert indexed.loc[(4, 1)] == pytest.approx(4.0)
    assert (1, 2) not in indexed.index and (3, 1) not in indexed.index


def test_first_work_trip_length_km_requires_columns():
    with pytest.raises(ValueError, match="wegkm"):
        R.first_work_trip_length_km(_mid_trips_for_length().drop(columns=["wegkm"]))
