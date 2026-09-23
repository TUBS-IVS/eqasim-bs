"""Unit tests for the SrV 2023 full-day absence aggregates (issue #370, sub-project B).

Synthetic SrV-shaped frames only -- the raw scientific-use microdata is local-only and must
never be read by a test. Three households (sizes 1, 2, 3), six persons: the single person and
both members of the 2-person household are away from home the whole reporting day
(``E_ANZ_WEGE == -7``), the 3-person household has one absent member and two present.
"""
import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration import srv_absence as A


def _persons():
    return pd.DataFrame({
        "HHNR": [1, 2, 2, 3, 3, 3], "PNR": [1, 1, 2, 1, 2, 3],
        "V_ALTER": [70, 30, 32, 40, 38, 8],
        "E_ANZ_WEGE": [-7, -7, -7, 3, 0, 2],
        "GEWICHT_P_ZENSUS": [1.0] * 6, "MITTL_WERKTAG": [1] * 6,
    })


def _households():
    return pd.DataFrame({"HHNR": [1, 2, 3], "GEWICHT_HH_ZENSUS": [1.0, 1.0, 1.0],
                         "V_ANZ_PERS": [1, 2, 3]})


def _persons_with_partial_households():
    """The tiny fixture plus three PARTIALLY absent households (issue #426):
    hh 4 (size 3): adult 45 absent, child 10 absent, adult 44 present   -> child away WITH an adult
    hh 5 (size 2): adult 60 absent, adult 58 present                    -> one adult away alone
    hh 6 (size 4): child 7 absent, adults 40/39 present, child 12 present -> child away, NO adult away
    """
    extra = pd.DataFrame({
        "HHNR": [4, 4, 4, 5, 5, 6, 6, 6, 6], "PNR": [1, 2, 3, 1, 2, 1, 2, 3, 4],
        "V_ALTER": [45, 10, 44, 60, 58, 7, 40, 39, 12],
        "E_ANZ_WEGE": [-7, -7, 2, -7, 3, -7, 3, 2, 4],
        "GEWICHT_P_ZENSUS": [1.0] * 9, "MITTL_WERKTAG": [1] * 9,
    })
    return pd.concat([_persons(), extra], ignore_index=True)


def _households_with_partial():
    return pd.DataFrame({"HHNR": [1, 2, 3, 4, 5, 6], "GEWICHT_HH_ZENSUS": [1.0] * 6,
                         "V_ANZ_PERS": [1, 2, 3, 3, 2, 4]})


def test_age_band_edges_match_the_spec():
    assert list(A.age_band(pd.Series([0, 5, 6, 17, 18, 29, 30, 44, 45, 64, 65, 74, 75, 99]))) == [
        "0-5", "0-5", "6-17", "6-17", "18-29", "18-29", "30-44", "30-44", "45-64", "45-64",
        "65-74", "65-74", "75+", "75+"]


def test_prepare_flags_absent_and_counts():
    prepared, diagnostics = A.prepare_absence_persons(_persons())
    assert prepared["absent"].tolist() == [True, True, True, False, False, False]
    assert diagnostics["n_persons_raw"] == 6 and diagnostics["n_absent"] == 3


def test_prepare_raises_on_unexpected_negative_trip_code():
    bad = _persons(); bad.loc[3, "E_ANZ_WEGE"] = -9
    with pytest.raises(ValueError, match="E_ANZ_WEGE"):
        A.prepare_absence_persons(bad)


def test_prepare_raises_when_not_average_weekday():
    bad = _persons(); bad.loc[0, "MITTL_WERKTAG"] = 0
    with pytest.raises(ValueError, match="MITTL_WERKTAG"):
        A.prepare_absence_persons(bad)


def test_prepare_logs_the_drop_rate_not_just_the_count(caplog):
    """No-silent-fallback rule (CLAUDE.md, MANDATORY): a dropped-row class must be logged as
    n / total (rate), never as a bare count. One of six persons has a non-positive weight, so
    the warning must state "1/6 persons (16.67%) dropped"."""
    bad = _persons(); bad.loc[0, "GEWICHT_P_ZENSUS"] = 0.0
    caplog.set_level("WARNING")
    A.prepare_absence_persons(bad)
    messages = [record.getMessage() for record in caplog.records]
    assert any("1/6 persons (16.67%) dropped" in message for message in messages), messages


def test_by_age_band_rates_and_all_row():
    prepared, _ = A.prepare_absence_persons(_persons())
    table = A.build_absence_by_age_band(prepared)
    row = table.set_index("band")
    assert row.loc["30-44", "p_absent"] == pytest.approx(2 / 4)   # 30, 32 absent; 40, 38 present
    assert row.loc["6-17", "p_absent"] == pytest.approx(0.0)
    assert row.loc["all", "n_unweighted"] == 6 and row.loc["all", "p_absent"] == pytest.approx(0.5)
    assert list(table["band"]) == list(A.AGE_BAND_LABELS) + ["all"]   # every band present, even empty


def test_by_household_size_all_absent_share():
    prepared, _ = A.prepare_absence_persons(_persons())
    table = A.build_absence_household_by_size(prepared, _households()).set_index("size_class")
    assert table.loc[1, "p_all_absent"] == pytest.approx(1.0)
    assert table.loc[2, "p_all_absent"] == pytest.approx(1.0)
    assert table.loc[3, "p_all_absent"] == pytest.approx(0.0)
    assert np.isnan(table.loc[4, "p_all_absent"]) and table.loc[4, "n_households_unweighted"] == 0
    assert list(table.index) == [1, 2, 3, 4, 5]


# ---------------------------------------------------------------------------
# Person-level absence rate by size class (issue #388): the PERSON-weighted share of absent
# persons among ALL persons living in a household of that size class -- distinct from
# ``p_all_absent``, which is the HOUSEHOLD-weighted share of households where EVERY member is
# absent. A reporting reference for #388's residual-only-on-households->=2 design.
# ---------------------------------------------------------------------------

def test_by_household_size_person_level_absence_rate():
    prepared, _ = A.prepare_absence_persons(_persons())
    table = A.build_absence_household_by_size(prepared, _households()).set_index("size_class")
    # Household 1 (size 1): its single member is absent -> 1/1 persons absent.
    assert table.loc[1, "n_persons_unweighted"] == 1
    assert table.loc[1, "n_absent_persons_unweighted"] == 1
    assert table.loc[1, "p_absent_person"] == pytest.approx(1.0)
    # Household 2 (size 2): both members absent -> 2/2 persons absent.
    assert table.loc[2, "n_persons_unweighted"] == 2
    assert table.loc[2, "n_absent_persons_unweighted"] == 2
    assert table.loc[2, "p_absent_person"] == pytest.approx(1.0)
    # Household 3 (size 3): nobody absent -> 0/3 persons absent.
    assert table.loc[3, "n_persons_unweighted"] == 3
    assert table.loc[3, "n_absent_persons_unweighted"] == 0
    assert table.loc[3, "p_absent_person"] == pytest.approx(0.0)
    # No household of size 4 in the fixture -> empty class, NaN rate, never a substituted zero.
    assert table.loc[4, "n_persons_unweighted"] == 0
    assert table.loc[4, "n_absent_persons_unweighted"] == 0
    assert np.isnan(table.loc[4, "p_absent_person"])


def test_by_household_size_person_counts_sum_to_the_by_age_all_row():
    """Every delivered person belongs to exactly one size class, so the by-size table's person
    counts must reconcile with the by-age table's 'all' row (same universe, both built from the
    same ``prepared`` frame)."""
    prepared, _ = A.prepare_absence_persons(_persons())
    by_age = A.build_absence_by_age_band(prepared)
    by_size = A.build_absence_household_by_size(prepared, _households())
    all_row_n_unweighted = int(by_age.loc[by_age["band"] == A.ALL_BAND, "n_unweighted"].iloc[0])
    assert int(by_size["n_persons_unweighted"].sum()) == all_row_n_unweighted == len(prepared)


def test_invariants_accept_the_builder_output_and_reject_a_share_above_one():
    prepared, _ = A.prepare_absence_persons(_persons())
    by_age = A.build_absence_by_age_band(prepared)
    by_size = A.build_absence_household_by_size(prepared, _households())
    A.check_invariants(by_age, by_size)
    broken = by_age.copy(); broken.loc[0, "p_absent"] = 1.2
    with pytest.raises(ValueError, match="p_absent"):
        A.check_invariants(broken, by_size)


def test_invariants_reject_more_absent_persons_than_persons_in_a_size_class():
    """No-silent-fallback / no-invented-data guard (issue #388): a size class can never report
    more absent persons than persons, so a violation must raise rather than silently pass through
    into the committed CSV."""
    prepared, _ = A.prepare_absence_persons(_persons())
    by_age = A.build_absence_by_age_band(prepared)
    by_size = A.build_absence_household_by_size(prepared, _households())
    broken = by_size.copy()
    broken.loc[0, "n_absent_persons_unweighted"] = broken.loc[0, "n_persons_unweighted"] + 1
    with pytest.raises(ValueError, match="n_absent_persons_unweighted"):
        A.check_invariants(by_age, broken)


def test_build_absence_household_by_size_raises_on_a_non_positive_household_weight():
    """Deferred minor (final-review fix wave): a household weight <= 0 must raise, named."""
    prepared, _ = A.prepare_absence_persons(_persons())
    households = _households(); households.loc[households["HHNR"] == 2, "GEWICHT_HH_ZENSUS"] = 0.0
    with pytest.raises(ValueError, match="non-positive GEWICHT_HH_ZENSUS"):
        A.build_absence_household_by_size(prepared, households)


# ---------------------------------------------------------------------------
# clustering_share (final-review fix wave, ruling R12): the traceable source of ADR-0110's
# "55.8 % (person-weighted; 49.4 % unweighted)" household-clustering figure.
# ---------------------------------------------------------------------------

def test_clustering_share_on_the_tiny_fixture():
    # Households: 1 (1 person, absent -> fully absent), 2 (2 persons, both absent -> fully
    # absent), 3 (3 persons, 1 absent, 2 present -> NOT fully absent). Absent persons: hh1/pnr1
    # (weight 1.0), hh2/pnr1 (weight 1.0), hh2/pnr2 (weight 1.0) -- all in fully absent
    # households -- plus none from hh3. So all 3 absent persons live in a fully absent household:
    # both shares are 1.0 on this fixture (a genuinely partial case is exercised by construction
    # in test_by_household_size_all_absent_share's household 3, which has zero absent members).
    prepared, _ = A.prepare_absence_persons(_persons())
    result = A.clustering_share(prepared)
    assert result["n_absent_persons"] == 3
    assert result["n_absent_in_fully_absent_households"] == 3
    assert result["unweighted_share"] == pytest.approx(1.0)
    assert result["weighted_share"] == pytest.approx(1.0)


def test_clustering_share_distinguishes_clustered_from_partial_absence():
    # A fourth, partially-absent household (id 4): one absent member out of two -- must lower
    # the clustering share below 1.0 while every OTHER household stays as in the tiny fixture.
    persons = _persons()
    partial = pd.DataFrame({
        "HHNR": [4, 4], "PNR": [1, 2], "V_ALTER": [50, 48],
        "E_ANZ_WEGE": [-7, 3], "GEWICHT_P_ZENSUS": [1.0, 1.0], "MITTL_WERKTAG": [1, 1],
    })
    prepared, _ = A.prepare_absence_persons(pd.concat([persons, partial], ignore_index=True))
    result = A.clustering_share(prepared)
    assert result["n_absent_persons"] == 4          # 3 from the tiny fixture + 1 from household 4
    assert result["n_absent_in_fully_absent_households"] == 3   # household 4 is only partially absent
    assert result["unweighted_share"] == pytest.approx(3 / 4)
    assert result["weighted_share"] == pytest.approx(3 / 4)     # equal weights on this fixture


def test_clustering_share_returns_nan_when_nobody_is_absent():
    persons = _persons()
    persons["E_ANZ_WEGE"] = [3, 2, 3, 3, 0, 2]   # nobody away from home
    prepared, _ = A.prepare_absence_persons(persons)
    result = A.clustering_share(prepared)
    assert result["n_absent_persons"] == 0
    assert np.isnan(result["unweighted_share"]) and np.isnan(result["weighted_share"])


# ---------------------------------------------------------------------------
# Partial-household absence (issue #426): a household is WHOLE (every member absent), PARTIAL
# (some but not all absent) or NONE. p_none + p_partial + p_all == 1 per size class.
# ---------------------------------------------------------------------------

def test_household_absence_patterns_flags_whole_partial_and_adult_counts():
    prepared, _ = A.prepare_absence_persons(_persons_with_partial_households())
    per_hh = A.household_absence_patterns(prepared).set_index("hhnr")
    assert per_hh.loc[1, "all_absent"] and not per_hh.loc[1, "partial"]          # single, away
    assert per_hh.loc[2, "all_absent"] and not per_hh.loc[2, "partial"]          # couple, both away
    assert not per_hh.loc[3, "all_absent"] and not per_hh.loc[3, "partial"]      # nobody away
    assert per_hh.loc[4, "partial"] and per_hh.loc[4, "n_absent"] == 2 and per_hh.loc[4, "n_adult_absent"] == 1
    assert per_hh.loc[5, "partial"] and per_hh.loc[5, "n_adult_absent"] == 1
    assert per_hh.loc[6, "partial"] and per_hh.loc[6, "n_absent"] == 1 and per_hh.loc[6, "n_adult_absent"] == 0
    assert per_hh["size_class"].tolist() == [1, 2, 3, 3, 2, 4]


def test_by_household_size_partial_share_partitions_with_all_absent():
    prepared, _ = A.prepare_absence_persons(_persons_with_partial_households())
    table = A.build_absence_household_by_size(prepared, _households_with_partial()).set_index("size_class")
    assert table.loc[1, "n_partial_absent_unweighted"] == 0 and table.loc[1, "p_partial_absent"] == pytest.approx(0.0)
    # size 2: hh2 fully absent, hh5 partially absent -> 0.5 / 0.5
    assert table.loc[2, "n_households_unweighted"] == 2
    assert table.loc[2, "p_all_absent"] == pytest.approx(0.5) and table.loc[2, "p_partial_absent"] == pytest.approx(0.5)
    # size 3: hh3 nobody absent, hh4 partial -> p_partial 0.5, p_all 0
    assert table.loc[3, "p_partial_absent"] == pytest.approx(0.5) and table.loc[3, "p_all_absent"] == pytest.approx(0.0)
    # size 4: hh6 partial only
    assert table.loc[4, "n_partial_absent_unweighted"] == 1 and table.loc[4, "p_partial_absent"] == pytest.approx(1.0)
    assert np.isnan(table.loc[5, "p_partial_absent"]) and table.loc[5, "n_households_unweighted"] == 0
    assert list(table.columns) == A.BY_SIZE_COLUMNS[1:]


def test_by_household_size_first_seven_columns_are_unchanged_by_the_partial_columns():
    """Additivity: the pre-#426 columns keep their names, order and values on the tiny fixture."""
    prepared, _ = A.prepare_absence_persons(_persons())
    table = A.build_absence_household_by_size(prepared, _households())
    assert list(table.columns[:7]) == ["size_class", "n_households_unweighted", "n_all_absent_unweighted",
                                       "p_all_absent", "n_persons_unweighted", "n_absent_persons_unweighted",
                                       "p_absent_person"]
    assert table.set_index("size_class").loc[2, "p_all_absent"] == pytest.approx(1.0)


def test_build_absence_household_by_size_raises_when_roster_differs_from_v_anz_pers():
    """Household size = DELIVERED persons per HHNR is an ASSUMPTION about the delivery; it is now
    verified in code, not ad hoc: a mismatch with V_ANZ_PERS must raise, naming the household."""
    prepared, _ = A.prepare_absence_persons(_persons())
    households = _households(); households.loc[households["HHNR"] == 3, "V_ANZ_PERS"] = 4
    with pytest.raises(ValueError, match="V_ANZ_PERS"):
        A.build_absence_household_by_size(prepared, households)


def test_build_absence_household_by_size_requires_the_roster_column():
    prepared, _ = A.prepare_absence_persons(_persons())
    with pytest.raises(ValueError, match="V_ANZ_PERS"):
        A.build_absence_household_by_size(prepared, _households().drop(columns=["V_ANZ_PERS"]))


def test_build_absence_household_by_size_names_a_household_missing_from_the_household_file():
    """A household of the person file with no row in the household file must fail with the
    weight-join message, not with the roster-mismatch message (Task 1 review, ruling R4)."""
    prepared, _ = A.prepare_absence_persons(_persons())
    with pytest.raises(ValueError, match="no row / weight"):
        A.build_absence_household_by_size(prepared, _households()[_households()["HHNR"] != 3])


# ---------------------------------------------------------------------------
# Composition of ABSENT persons by household pattern (issue #426): whole_household /
# partial_with_absent_adult / partial_no_absent_adult -- a partition per age band, plus the
# '0-17' children aggregate row on which the #426 acceptance criterion is evaluated.
# ---------------------------------------------------------------------------

def test_classify_absence_composition_assigns_one_pattern_per_absent_person():
    prepared, _ = A.prepare_absence_persons(_persons_with_partial_households())
    classified = A.classify_absence_composition(prepared).set_index(["hhnr", "pnr"])
    assert len(classified) == 7                                    # only absent persons are kept
    assert classified.loc[(1, 1), "pattern"] == A.PATTERN_WHOLE    # single, away
    assert classified.loc[(2, 1), "pattern"] == A.PATTERN_WHOLE
    assert classified.loc[(4, 1), "pattern"] == A.PATTERN_PARTIAL_NO_ADULT    # adult 45: the OTHER adult (44) is home
    assert classified.loc[(4, 2), "pattern"] == A.PATTERN_PARTIAL_WITH_ADULT  # child 10: adult 45 is away too
    assert classified.loc[(5, 1), "pattern"] == A.PATTERN_PARTIAL_NO_ADULT    # adult 60 alone
    assert classified.loc[(6, 1), "pattern"] == A.PATTERN_PARTIAL_NO_ADULT    # child 7: no adult away


def test_composition_by_band_rows_children_row_and_shares():
    prepared, _ = A.prepare_absence_persons(_persons_with_partial_households())
    table = A.build_absence_composition_by_band(prepared)
    assert list(table["band"]) == list(A.AGE_BAND_LABELS) + [A.CHILDREN_ROW, A.ALL_BAND]
    assert list(table.columns) == A.COMPOSITION_COLUMNS
    row = table.set_index("band")
    # children 0-17: child 10 (with adult) and child 7 (no adult) -> 0 / 0.5 / 0.5
    assert row.loc[A.CHILDREN_ROW, "age_min"] == 0 and row.loc[A.CHILDREN_ROW, "age_max"] == A.CHILD_MAX_AGE
    assert row.loc[A.CHILDREN_ROW, "n_absent_unweighted"] == 2
    assert row.loc[A.CHILDREN_ROW, "p_whole_household"] == pytest.approx(0.0)
    assert row.loc[A.CHILDREN_ROW, "p_partial_with_absent_adult"] == pytest.approx(0.5)
    assert row.loc[A.CHILDREN_ROW, "p_partial_no_absent_adult"] == pytest.approx(0.5)
    # band 45-64: adults 45 and 60, both partial without another absent adult
    assert row.loc["45-64", "n_absent_unweighted"] == 2
    assert row.loc["45-64", "p_partial_no_absent_adult"] == pytest.approx(1.0)
    # band 0-5: no absent person -> counts 0, shares NaN (never a substituted zero)
    assert row.loc["0-5", "n_absent_unweighted"] == 0 and np.isnan(row.loc["0-5", "p_whole_household"])
    # all: 7 absent = 3 whole + 1 with adult + 3 no adult
    assert row.loc[A.ALL_BAND, "n_absent_unweighted"] == 7
    assert row.loc[A.ALL_BAND, "n_whole_household_unweighted"] == 3
    assert row.loc[A.ALL_BAND, "n_partial_with_absent_adult_unweighted"] == 1
    assert row.loc[A.ALL_BAND, "n_partial_no_absent_adult_unweighted"] == 3
    assert row.loc[A.ALL_BAND, "p_whole_household"] == pytest.approx(3 / 7)


def test_composition_shares_are_person_weighted():
    persons = _persons_with_partial_households()
    persons.loc[(persons["HHNR"] == 4) & (persons["PNR"] == 2), "GEWICHT_P_ZENSUS"] = 3.0   # child 10
    prepared, _ = A.prepare_absence_persons(persons)
    row = A.build_absence_composition_by_band(prepared).set_index("band").loc[A.CHILDREN_ROW]
    assert row["p_partial_with_absent_adult"] == pytest.approx(3.0 / 4.0)   # weights 3.0 (child 10) vs 1.0 (child 7)
    assert row["n_partial_with_absent_adult_unweighted"] == 1               # counts stay unweighted


# ---------------------------------------------------------------------------
# How many members travel together in a PARTIALLY absent household (issue #426).
# ---------------------------------------------------------------------------

def test_partial_subset_size_rows_and_shares():
    prepared, _ = A.prepare_absence_persons(_persons_with_partial_households())
    table = A.build_absence_partial_subset_size(prepared, _households_with_partial())
    assert list(table.columns) == A.PARTIAL_SUBSET_COLUMNS
    assert list(zip(table["size_class"], table["n_absent_members"])) == [
        (2, 1), (3, 1), (3, 2), (4, 1), (4, 2), (4, 3), (5, 1), (5, 2), (5, 3), (5, 4)]
    row = table.set_index(["size_class", "n_absent_members"])
    assert row.loc[(2, 1), "n_households_unweighted"] == 1 and row.loc[(2, 1), "share_within_partial"] == pytest.approx(1.0)
    assert row.loc[(3, 1), "n_households_unweighted"] == 0 and row.loc[(3, 1), "share_within_partial"] == pytest.approx(0.0)
    assert row.loc[(3, 2), "n_households_unweighted"] == 1 and row.loc[(3, 2), "share_within_partial"] == pytest.approx(1.0)
    assert row.loc[(4, 1), "share_within_partial"] == pytest.approx(1.0)
    assert np.isnan(row.loc[(5, 1), "share_within_partial"]) and row.loc[(5, 1), "n_households_unweighted"] == 0


def test_partial_subset_size_top_codes_the_absent_member_count():
    persons = _persons()
    big = pd.DataFrame({   # size 7 household (class 5), 6 members away, 1 present -> k top-coded to 4
        "HHNR": [9] * 7, "PNR": list(range(1, 8)), "V_ALTER": [40, 38, 12, 10, 8, 6, 70],
        "E_ANZ_WEGE": [-7, -7, -7, -7, -7, -7, 2], "GEWICHT_P_ZENSUS": [1.0] * 7, "MITTL_WERKTAG": [1] * 7})
    households = pd.concat([_households(), pd.DataFrame({"HHNR": [9], "GEWICHT_HH_ZENSUS": [1.0], "V_ANZ_PERS": [7]})],
                           ignore_index=True)
    prepared, _ = A.prepare_absence_persons(pd.concat([persons, big], ignore_index=True))
    row = A.build_absence_partial_subset_size(prepared, households).set_index(["size_class", "n_absent_members"])
    assert row.loc[(5, 4), "n_households_unweighted"] == 1 and row.loc[(5, 4), "share_within_partial"] == pytest.approx(1.0)


# ---------------------------------------------------------------------------
# Wilson score interval -- the pre-registered acceptance bound of issue #426 (thin cells).
# ---------------------------------------------------------------------------

def test_wilson_interval_known_values():
    assert A.wilson_interval(35, 73) == pytest.approx((0.368775, 0.592184), abs=1e-5)
    assert A.wilson_interval(12, 73) == pytest.approx((0.096613, 0.265710), abs=1e-5)
    assert A.wilson_interval(0, 10) == pytest.approx((0.0, 0.277533), abs=1e-5)
    assert A.wilson_interval(10, 10) == pytest.approx((0.722467, 1.0), abs=1e-5)


def test_wilson_interval_edge_cases():
    low, high = A.wilson_interval(0, 0)
    assert np.isnan(low) and np.isnan(high)
    with pytest.raises(ValueError, match="k"):
        A.wilson_interval(5, 4)


# ---------------------------------------------------------------------------
# Invariants across the four tables.
# ---------------------------------------------------------------------------

def _all_tables():
    prepared, _ = A.prepare_absence_persons(_persons_with_partial_households())
    households = _households_with_partial()
    return (A.build_absence_by_age_band(prepared), A.build_absence_household_by_size(prepared, households),
            A.build_absence_composition_by_band(prepared), A.build_absence_partial_subset_size(prepared, households))


def test_invariants_accept_all_four_tables():
    by_age, by_size, composition, partial_subset = _all_tables()
    A.check_invariants(by_age, by_size, composition, partial_subset)
    A.check_invariants(by_age, by_size)          # the two-table call of the old script still works


def test_invariants_reject_partial_plus_all_exceeding_households():
    by_age, by_size, composition, partial_subset = _all_tables()
    broken = by_size.copy(); broken.loc[broken["size_class"] == 2, "n_partial_absent_unweighted"] = 5
    with pytest.raises(ValueError, match="n_partial_absent_unweighted"):
        A.check_invariants(by_age, broken, composition, partial_subset)


def test_invariants_reject_composition_shares_that_do_not_sum_to_one():
    by_age, by_size, composition, partial_subset = _all_tables()
    broken = composition.copy(); broken.loc[broken["band"] == A.ALL_BAND, "p_whole_household"] = 0.9
    with pytest.raises(ValueError, match="sum to 1"):
        A.check_invariants(by_age, by_size, broken, partial_subset)


def test_invariants_reject_subset_counts_that_do_not_reconcile_with_by_size():
    by_age, by_size, composition, partial_subset = _all_tables()
    broken = partial_subset.copy(); broken.loc[0, "n_households_unweighted"] = 7   # class 2, k=1
    with pytest.raises(ValueError, match="n_partial_absent_unweighted"):
        A.check_invariants(by_age, by_size, composition, broken)
