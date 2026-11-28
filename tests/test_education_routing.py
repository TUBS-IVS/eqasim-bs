"""Routing of education activities by student status (issue #140, Phase 4, design B1).

Covers the routing table, the student-status primary path and its counted fallback, the placement of the new
levels in ``assign_education_locations`` and the byte-identical OFF path (golden values at the end of the file).
"""
from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from braunschweig.synthesis.locations import education_routing as routing
from braunschweig.synthesis.locations.education_gravity import assign_education_locations, merge_person_attributes
from braunschweig.synthesis.locations.education_routing import (
    ADULT_EDUCATION, BBS_ADULT, route_education_level, routing_summary, student_status)

NAN = float("nan")


# ------------------------------------------------------------------------------------------------ routing table
_TABLE = [
    # (age, is_student, P_TAET, routed level): every row of the table of design B1
    (22, True, 10, "university"),
    (18, True, 10, "university"),
    (19, True, 10, "university"),
    (45, True, 10, "university"),                      # a student at any adult age
    (17, True, 10, "upper_secondary"),                 # student under 18: today's age band
    (12, True, 10, "sekundar_1"),
    (17, False, 9, "upper_secondary"),                 # pupil under 20: age band (split into oberstufe / bbs later)
    (19, False, 8, "upper_secondary"),                 # apprentice aged 19: age band, no bbs_adult
    (4, False, 17, "kindergarten"),
    (8, False, 9, "grundschule"),
    (25, False, 8, BBS_ADULT),                         # apprentice 20+
    (20, False, 9, BBS_ADULT),                         # pupil 20+
    (43, False, 4, ADULT_EDUCATION),                   # employed adult
    (20, False, NAN, ADULT_EDUCATION),                 # status missing
    (25, False, 10, ADULT_EDUCATION),                  # P_TAET 10 but not flagged: the flag decides, not the code
]


@pytest.mark.parametrize("age,is_student,p_taet,expected", _TABLE)
def test_routing_table_row(age, is_student, p_taet, expected):
    out = route_education_level(pd.Series([age]), pd.Series([is_student]), pd.Series([p_taet]))
    assert out.tolist() == [expected]


def test_routing_without_p_taet_sends_nobody_to_bbs_adult():
    ages = pd.Series([25, 43, 22])
    out = route_education_level(ages, pd.Series([False, False, True]), None)
    assert out.tolist() == [ADULT_EDUCATION, ADULT_EDUCATION, "university"]


def test_vocational_codes_are_the_apprentice_and_pupil_codes_of_phase_3():
    from braunschweig.popsim.attributes import STUDIES_TAET, STUDENT_TAET
    assert set(routing.VOCATIONAL_P_TAET) | {STUDENT_TAET} == set(STUDIES_TAET)


def test_summary_counts_the_levels_and_the_students_and_adults_the_off_rule_would_move():
    age = pd.Series([17, 22, 25, 43, 50])
    is_student = pd.Series([True, True, False, False, False])
    routed = route_education_level(age, is_student, pd.Series([10, 10, 8, 4, 4]))
    summary = routing_summary(age, is_student, routed)
    assert summary["per_level"] == {"upper_secondary": 1, "university": 1, BBS_ADULT: 1, ADULT_EDUCATION: 2}
    assert summary["students_under_18"] == 1
    assert (summary["non_student_adults"], summary["to_bbs_adult"], summary["to_adult_education"]) == (3, 1, 2)


# ------------------------------------------------------------------------------------------------ student status
def test_student_status_primary_path_reads_p_taet_and_counts_no_fallback(caplog):
    # popsim_mid-like persons: P_TAET, studies and employed all present (assembly derives the last two)
    persons = pd.DataFrame({"age": [22, 25, 30, 40], "P_TAET": [10, 8, 10, 4],
                            "studies": [True, True, True, False], "employed": [False, True, False, True]})
    with caplog.at_level(logging.INFO):
        is_student, counts = student_status(persons)
    assert is_student.tolist() == [True, False, True, False]
    assert (counts["primary"], counts["fallback"], counts["students"]) == (4, 0, 2)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_student_status_missing_p_taet_values_are_counted_as_fallback_and_warn(caplog):
    # P_TAET exists but is mostly missing: no healthy primary rate may be reported, and the warning fires
    persons = pd.DataFrame({"age": [22] * 10, "P_TAET": [10] + [NAN] * 9, "studies": [False] * 10,
                            "employed": [False] * 10})
    with caplog.at_level(logging.INFO):
        is_student, counts = student_status(persons)
    assert is_student.tolist() == [True] + [False] * 9
    assert (counts["primary"], counts["fallback"], counts["taet_missing"]) == (1, 9, 9)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings and "primary 1/10" in warnings[0] and "fallback 9" in warnings[0]
    caplog.clear()
    with caplog.at_level(logging.INFO):
        student_status(persons, warn_share=0.95)         # 90 % missing is not above 95 %
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


@pytest.mark.parametrize("unknown", [NAN, 99], ids=["missing", "no_answer_code"])
def test_unknown_status_in_a_population_with_p_taet_takes_the_fallback_rule_end_to_end(unknown):
    # Ruling: an unknown status is never read as "not a student". The persons with a missing P_TAET or the MiD
    # no-answer code 99 take studies & ~employed & age >= 20 per person; the persons with a known status stay on
    # the primary rule.
    persons = pd.DataFrame({
        "age": [25, 25, 17, 25, 25, 25],
        "P_TAET": [unknown, unknown, unknown, 10, 4, 8],
        "studies": [True, False, True, False, True, False],      # persons 4 and 5: ignored (readable P_TAET)
        "employed": [False, False, False, False, False, True],
    })
    is_student, counts = student_status(persons, warn_share=1.0)
    assert is_student.tolist() == [True, False, False, True, False, False]
    assert (counts["primary"], counts["fallback"]) == (3, 3)
    routed = route_education_level(persons["age"], is_student, persons["P_TAET"])
    assert routed.tolist() == ["university",          # unknown status, studies, not employed -> university
                               ADULT_EDUCATION,       # unknown status, not studying -> adult education
                               "upper_secondary",     # under 20, unknown status -> its age band
                               "university", ADULT_EDUCATION, BBS_ADULT]


def test_unknown_status_without_the_fallback_columns_raises():
    with pytest.raises(ValueError, match="studies"):
        student_status(pd.DataFrame({"age": [22], "P_TAET": [NAN]}))


def test_student_status_fallback_reads_a_missing_flag_as_false_and_counts_it():
    # astype(bool) would read NaN as True and make persons 2 and 3 students
    persons = pd.DataFrame({"age": [22, 25, 30], "studies": [True, NAN, True], "employed": [False, False, NAN]})
    is_student, counts = student_status(persons, warn_share=1.0)
    assert is_student.tolist() == [True, False, True]       # the missing employed flag reads as not employed
    assert counts["flag_missing"] == 2 and counts["fallback"] == 3


def test_student_status_fallback_is_counted_and_warns_above_the_share(caplog):
    persons = pd.DataFrame({"age": [22, 19, 25, 30], "studies": [True, True, True, False],
                            "employed": [False, False, True, False]})
    with caplog.at_level(logging.INFO):
        is_student, counts = student_status(persons, warn_share=0.0)
    # studies and not employed and age >= 20: only the first person
    assert is_student.tolist() == [True, False, False, False]
    assert (counts["primary"], counts["fallback"]) == (0, 4)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert warnings and "fallback 4" in warnings[0].getMessage()
    caplog.clear()
    with caplog.at_level(logging.INFO):
        student_status(persons, warn_share=1.0)          # share 1.0 is not above 1.0
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_student_status_without_any_source_raises():
    with pytest.raises(ValueError, match="studies"):
        student_status(pd.DataFrame({"age": [22], "employed": [False]}))


# ------------------------------------------------------------------------------------------------ the stage
def _nds():
    return gpd.GeoDataFrame({
        "school_id": ["g1", "g2", "s1", "s2", "o1", "o2", "b1", "b2"],
        "level": ["grundschule"] * 2 + ["sekundar_1"] * 2 + ["oberstufe"] * 2 + ["bbs"] * 2,
        "capacity": [60.0, 60.0, 60.0, 60.0, 60.0, 60.0, 80.0, 80.0],
        "commune_id": ["03101000"] * 8,
        "geometry": [Point(500.0, 0.0), Point(900.0, 100.0), Point(800.0, 0.0), Point(1200.0, 0.0),
                     Point(1000.0, 0.0), Point(2000.0, 500.0), Point(1500.0, 0.0), Point(3000.0, 0.0)],
    }, crs="EPSG:25832")


def _kita():
    return gpd.GeoDataFrame({"location_id": ["kita_a", "kita_b"], "capacity": [50.0, 50.0],
                             "geometry": [Point(100.0, 0.0), Point(400.0, 0.0)]}, crs="EPSG:25832")


def _universities():
    return gpd.GeoDataFrame({"location_id": ["uni_a", "uni_b"], "capacity": [5000.0, 20000.0],
                             "geometry": [Point(2000.0, 0.0), Point(60000.0, 0.0)]}, crs="EPSG:25832")


def _cfg(min_age=None, flows=False, **extra):
    cfg = {"slope_by_level": {"kindergarten": -0.5, "grundschule": -0.3, "sekundar_1": -0.15,
                              "oberstufe": -0.08, "bbs": -0.05},
           "slope_by_level_rs7": None,
           "max_radius_km_by_level": {"kindergarten": 8.0, "grundschule": 15.0, "sekundar_1": 30.0,
                                      "oberstufe": 60.0, "bbs": 100.0},
           "university_slope": -0.08, "university_max_radius_km": 150.0,
           "max_iterations": 50, "tolerance": 1e-6, "bbs_share": 0.681,
           "adult_education_min_age": min_age, "adult_education_slope": -0.15,
           "adult_education_max_radius_km": 30.0}
    if flows:
        cfg["student_flows_enabled"] = True
    return {**cfg, **extra}


def _persons(ages, p_taet=None, studies=None, employed=None):
    n = len(ages)
    data = {"person_id": list(range(1, n + 1)), "age": list(ages), "home_rs7": [72] * n,
            "geometry": [Point(float((i * 137) % 900), 0.0) for i in range(n)]}
    if p_taet is not None:
        data["P_TAET"] = list(p_taet)
    if studies is not None:
        data["studies"] = list(studies)
    if employed is not None:
        data["employed"] = list(employed)
    return gpd.GeoDataFrame(data, crs="EPSG:25832")


def _on(persons, seed=3, **cfg_kwargs):
    """The ON path with every person in one home Kreis whose row of T splits the students evenly over the two
    universities; no observed education leg (every student on the counted fallback, never warned here)."""
    from braunschweig.synthesis.locations.education_gravity import UniversityFlowInputs
    universities = _universities().assign(destination_id=["uni_a", "uni_b"], scope="local")
    flows = UniversityFlowInputs(
        seed=pd.DataFrame({"home_ars5": ["03101", "03101"], "destination_id": ["uni_a", "uni_b"], "seed": [1.0, 1.0]}),
        shares=pd.Series({"uni_a": 0.5, "uni_b": 0.5}), dropped_destinations=[],
        home_kreis=pd.Series("03101", index=persons["person_id"].to_numpy()), leg_km=pd.Series(dtype=float),
        day_state=None, rule="plan_length", fit_tolerance=1e-6, fit_max_iterations=100, fallback_warn_share=1.0,
        rng=np.random.default_rng(seed))
    return assign_education_locations(persons, _nds(), universities, _kita(), _cfg(flows=True, **cfg_kwargs),
                                      np.random.RandomState(seed), university_flows=flows)


def test_on_path_places_every_status_group_on_its_level_candidates():
    # 60 students (29 and 22 years), 60 apprentices (28), 60 non-student adults (43): adult_education_min_age is
    # NOT read on the ON path (min_age None here and 30 below give the same destinations).
    n = 60
    ages = [29] * n + [28] * n + [43] * n
    p_taet = [10] * n + [8] * n + [4] * n
    persons = _persons(ages, p_taet=p_taet)
    out = _on(persons)
    assert len(out) == 3 * n and set(out["person_id"]) == set(persons["person_id"])
    by_level = {level: out[out["routed_level"] == level] for level in ("university", BBS_ADULT, ADULT_EDUCATION)}
    assert [len(v) for v in by_level.values()] == [n, n, n]
    assert set(by_level["university"]["location_id"]) <= {"uni_a", "uni_b"}
    assert set(by_level[BBS_ADULT]["location_id"]) <= {"b1", "b2"}          # the vocational schools only
    # the adult-education rule: school buildings only, no university building, whatever the age threshold
    assert set(by_level[ADULT_EDUCATION]["location_id"]) <= set(_nds()["school_id"])
    out_30 = _on(persons, min_age=30)
    assert out_30.sort_values("person_id")["location_id"].tolist() == out.sort_values("person_id")[
        "location_id"].tolist()


def test_on_path_bbs_adult_is_drawn_by_decay_with_the_capacity_as_weight():
    # b1 (1.5 km, capacity 80) and b2 (3.0 km, capacity 80): decay -0.05 per km favours b1 only mildly, so both are
    # used by 400 apprentices; a capacity-balanced placement would split them exactly 50/50.
    persons = _persons([25] * 400, p_taet=[8] * 400)
    out = _on(persons)
    counts = out["location_id"].value_counts()
    assert set(counts.index) == {"b1", "b2"}
    expected_b1 = 1.0 / (1.0 + np.exp(-0.05 * (3.0 - 1.5)))   # equal weights, distances to a person near x = 0
    assert abs(counts["b1"] / 400 - expected_b1) < 0.08


def test_on_path_draws_of_the_other_levels_do_not_depend_on_the_university_step():
    # The university step draws from its own generator: 30 more students must leave every draw of the apprentices
    # (bbs_adult) and of the adult education, both drawn after it from the stage's RandomState, unchanged.
    n = 20
    base = _persons([25] * n + [43] * n + [23] * n, p_taet=[8] * n + [4] * n + [10] * n)
    more = _persons([25] * n + [43] * n + [23] * (n + 30), p_taet=[8] * n + [4] * n + [10] * (n + 30))
    out, out_more = _on(base), _on(more)
    others = out[out["routed_level"].isin([BBS_ADULT, ADULT_EDUCATION])].set_index("person_id")["location_id"]
    others_more = out_more.set_index("person_id")["location_id"].loc[others.index]
    assert len(others) == 2 * n
    pd.testing.assert_series_equal(others, others_more)


def test_on_path_keeps_pupils_under_20_on_the_age_bands_and_adds_routed_level():
    persons = _persons([4, 7, 12, 17, 19], p_taet=[17, 9, 9, 9, 8])
    out = _on(persons)
    levels = dict(zip(out["person_id"], out["routed_level"]))
    assert levels[1] == "kindergarten" and levels[2] == "grundschule" and levels[3] == "sekundar_1"
    assert levels[4] in ("oberstufe", "bbs") and levels[5] in ("oberstufe", "bbs")   # split after the routing


def test_on_path_without_p_taet_uses_the_counted_fallback_and_sends_nobody_to_bbs_adult(caplog):
    persons = _persons([25, 43, 25], studies=[True, False, False], employed=[False, False, True])
    with caplog.at_level(logging.INFO):
        out = _on(persons)
    levels = dict(zip(out["person_id"], out["routed_level"]))
    assert levels == {1: "university", 2: ADULT_EDUCATION, 3: ADULT_EDUCATION}
    assert [r for r in caplog.records if r.levelno == logging.WARNING and "fallback 3" in r.getMessage()]


def test_on_path_fallback_warn_share_comes_from_the_config(caplog):
    persons = _persons([25], studies=[True], employed=[False])
    with caplog.at_level(logging.INFO):
        _on(persons, student_status_fallback_warn_share=1.0)
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


# ------------------------------------------------------------------------------------------------ the merge
def test_merge_adds_the_student_columns_only_on_the_on_path():
    persons = pd.DataFrame({"person_id": [1, 2], "household_id": [1, 1]})
    enriched = pd.DataFrame({"person_id": [1, 2], "age": [30, 40], "P_TAET": [10, 4], "studies": [True, False],
                             "employed": [False, True], "other": [0, 0]})
    off = merge_person_attributes(persons, enriched, flows_enabled=False)
    assert list(off.columns) == ["person_id", "household_id", "age"]
    on = merge_person_attributes(persons, enriched, flows_enabled=True)
    assert list(on.columns) == ["person_id", "household_id", "age", "P_TAET", "studies", "employed"]
    # a legacy enriched frame without P_TAET merges what it has
    legacy = merge_person_attributes(persons, enriched.drop(columns="P_TAET"), flows_enabled=True)
    assert list(legacy.columns) == ["person_id", "household_id", "age", "studies", "employed"]


def test_merge_refuses_ambiguous_or_lossy_inputs_and_keeps_the_row_count():
    persons = pd.DataFrame({"person_id": [1, 2, 3], "household_id": [1, 1, 2]})
    enriched = pd.DataFrame({"person_id": [3, 1, 2], "age": [30, 40, 50], "P_TAET": [10, 4, 8]})
    merged = merge_person_attributes(persons, enriched, flows_enabled=True)
    assert merged["person_id"].tolist() == [1, 2, 3] and merged["age"].tolist() == [40, 50, 30]   # by id, not row
    assert len(merged) == len(persons)
    with pytest.raises(ValueError, match="already carry"):            # the persons carry one of the columns
        merge_person_attributes(persons.assign(P_TAET=1), enriched, flows_enabled=True)
    with pytest.raises(ValueError, match="not unique in the enriched"):
        merge_person_attributes(persons, pd.concat([enriched, enriched.iloc[:1]]), flows_enabled=True)
    with pytest.raises(ValueError, match="not unique in the persons"):
        merge_person_attributes(pd.concat([persons, persons.iloc[:1]]), enriched, flows_enabled=False)
    with pytest.raises(AssertionError, match="row count"):            # a person absent from the enriched frame
        merge_person_attributes(persons, enriched.iloc[:2], flows_enabled=True)


# ------------------------------------------------------------------------------------------------ the OFF path
def _golden_persons():
    ages = [4, 5, 7, 8, 12, 14, 16, 17, 18, 19, 19, 20, 22, 25, 29, 31, 35, 43, 47, 52]
    n = len(ages) * 3
    ages = [ages[i % len(ages)] for i in range(n)]
    persons = _persons(ages)
    # the student columns are present and must not matter with the switch OFF
    persons["P_TAET"] = [10 if a >= 18 else 9 for a in ages]
    persons["studies"] = True
    persons["employed"] = False
    return persons


@pytest.mark.parametrize("min_age,order,locations", [
    (None, "_GOLDEN_ORDER_NO_ADULT_BAND", "_GOLDEN_LOCATIONS_NO_ADULT_BAND"),
    (30, "_GOLDEN_ORDER_ADULT_BAND_30", "_GOLDEN_LOCATIONS_ADULT_BAND_30"),
])
def test_off_path_equals_the_output_of_the_stage_before_the_switch(min_age, order, locations):
    """The golden lists are the output of ``assign_education_locations`` at the commit before this change
    (seed 7, the same fixture); with the switch OFF every draw and the row order are unchanged."""
    out = assign_education_locations(_golden_persons(), _nds(), _universities(), _kita(), _cfg(min_age=min_age),
                                     np.random.RandomState(7))
    # The whole frame, dtypes included: commune_id and geometry belong to the facility named by location_id (an NDS
    # school carries its commune, a Kita and a university none).
    facilities = pd.concat([_nds()[["school_id", "commune_id", "geometry"]].rename(columns={"school_id": "location_id"}),
                            _kita()[["location_id", "geometry"]].assign(commune_id=""),
                            _universities()[["location_id", "geometry"]].assign(commune_id="")]).set_index("location_id")
    location_ids = globals()[locations]
    expected = pd.DataFrame({"person_id": pd.Series(globals()[order], dtype="int64"),
                             "commune_id": facilities.loc[location_ids, "commune_id"].to_numpy(),
                             "location_id": location_ids,
                             "geometry": gpd.GeoSeries(facilities.loc[location_ids, "geometry"].to_numpy(),
                                                       crs="EPSG:25832")})
    pd.testing.assert_frame_equal(out, expected)


# ------------------------------------------------------------------------------------------------ golden values
# Output of assign_education_locations of the commit before the routing (22cbc747), fixture _golden_persons().
_GOLDEN_ORDER_NO_ADULT_BAND = [
    3, 4, 23, 24, 43, 44, 5, 6, 25, 26, 45, 46, 8, 10, 11, 48, 7, 9, 27, 28, 29, 30, 31, 47, 49, 50, 51, 1,
    2, 21, 22, 41, 42, 12, 13, 14, 15, 16, 17, 18, 19, 20, 32, 33, 34, 35, 36, 37, 38, 39, 40, 52, 53, 54,
    55, 56, 57, 58, 59, 60,
]
_GOLDEN_LOCATIONS_NO_ADULT_BAND = [
    'g2', 'g1', 'g1', 'g2', 'g1', 'g2', 's2', 's1', 's2', 's2', 's1', 's2', 'o2', 'o2', 'o1', 'o1', 'b1',
    'b1', 'b1', 'b1', 'b2', 'b2', 'b1', 'b2', 'b1', 'b1', 'b1', 'kita_b', 'kita_a', 'kita_a', 'kita_b',
    'kita_a', 'kita_b', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a',
    'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_b', 'uni_a',
    'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a',
]
_GOLDEN_ORDER_ADULT_BAND_30 = [
    3, 4, 23, 24, 43, 44, 5, 6, 25, 26, 45, 46, 8, 10, 11, 48, 7, 9, 27, 28, 29, 30, 31, 47, 49, 50, 51, 1,
    2, 21, 22, 41, 42, 12, 13, 14, 15, 32, 33, 34, 35, 52, 53, 54, 55, 16, 17, 18, 19, 20, 36, 37, 38, 39,
    40, 56, 57, 58, 59, 60,
]
_GOLDEN_LOCATIONS_ADULT_BAND_30 = [
    'g2', 'g1', 'g1', 'g2', 'g1', 'g2', 's2', 's1', 's2', 's2', 's1', 's2', 'o2', 'o2', 'o1', 'o1', 'b1',
    'b1', 'b1', 'b1', 'b2', 'b2', 'b1', 'b2', 'b1', 'b1', 'b1', 'kita_b', 'kita_a', 'kita_a', 'kita_b',
    'kita_a', 'kita_b', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a', 'uni_a',
    'uni_a', 'uni_a', 'uni_a', 'uni_a', 'o2', 'g1', 's1', 'o1', 'g2', 'uni_a', 'uni_a', 'b2', 's2', 'b2',
    'g2', 's1', 's2', 'b1',
]
