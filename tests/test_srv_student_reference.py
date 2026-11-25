"""Unit tests for the SrV 2023 university-student aggregates (issue #140, Phase 0, table R5).

Synthetic SrV-shaped frames only -- the raw scientific-use microdata is local-only and must
never be read by a test. Every expected value is computed by hand from the table in the
docstring of :func:`_raw`; the weights are chosen so that an unweighted share, a mean of the
two Kreis shares and the right weighted share all differ.
"""
import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration import srv_student_reference as R
from braunschweig.calibration.srv_participation_universe import REGION_CODE
from braunschweig.calibration.student_behaviour_labels import (
    CONTROLS, NOT_STUDENT, STUDENT_DISTANCE_LABELS, UNIVERSE_ADULTS, UNIVERSE_STUDENTS,
    effective_sample_size, share_standard_error)

REPO = Path(__file__).resolve().parents[1]
WOLFSBURG = "03103"


def _raw():
    """Four households in two Kreise, eleven persons.

    ====  =====  ===  =====  ======  ==========  ===========================================
    pid   kreis  age  V_ERW  weight  E_OEV_FK    day (E_ANZ_WEGE; legs)
    ====  =====  ===  =====  ======  ==========  ===========================================
    1_1   03101   22  7 stu       2  50 (flat)   3: edu 9->10 h (WNR 2), edu 12->13 h (WNR 1),
                                                 home (WNR 3, listed first in the file)
    1_2   03101   24  7 stu       1  1 (no)      2: work, home
    2_1   03101   27  9           2  -8          2: work, home
    2_2   03101   15  6 pupil     1  -8          2: school, home (not an adult)
    2_3   03101   19  8 appr.     1  1           2: education, home
    3_1   03102   21  7 stu       1  -8 (no)     2: edu 7 h -> arrival hour -1, home
    3_2   03102   35  7 stu       1  -10         0: no leg
    3_3   03102   50  10          2  -8          0
    4_1   03102   70  3           2  -8          0
    4_2   03102   40  9           2  1           2: other education (evening course), home
    4_3   03102   30  -10         2  -8          1: home (V_ERW unreadable -> excluded)
    ====  =====  ===  =====  ======  ==========  ===========================================

    Hand-computed expectations (weights in brackets):

    * adults with a readable V_ERW: students 1_1 [2], 1_2 [1], 3_1 [1], 3_2 [1] = 5; others
      2_1 [2], 2_3 [1], 3_3 [2], 4_1 [2], 4_2 [2] = 9; region student share = 5/14. Kreis
      03101 = 3/6 = 0.5, 03102 = 2/8 = 0.25 (mean of the two 0.375, unweighted 4/9).
    * ages 20-29: students 1_1 [2], 1_2 [1], 3_1 [1] = 4, other 2_1 [2] -> 4/6 = 2/3.
    * presence (students, all ages, with an education leg): 1_1 [2] + 3_1 [1] = 3 of 5.
    """
    households = pd.DataFrame({"HHNR": [1, 2, 3, 4],
                               "AGS": [3101000, 3101000, 3102000, 3102000]})
    persons = pd.DataFrame({
        "HHNR": [1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4],
        "PNR": [1, 2, 1, 2, 3, 1, 2, 3, 1, 2, 3],
        "V_ALTER": [22, 24, 27, 15, 19, 21, 35, 50, 70, 40, 30],
        "V_ERW": [7, 7, 9, 6, 8, 7, 7, 10, 3, 9, -10],
        "E_ANZ_WEGE": [3, 2, 2, 2, 2, 2, 0, 0, 0, 2, 1],
        "GEWICHT_P_ZENSUS": [2.0, 1.0, 2.0, 1.0, 1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 2.0],
        "MITTL_WERKTAG": [1] * 11,
        "E_OEV_FK": [50, 1, -8, -8, 1, -8, -10, -8, -8, 1, -8],
        "V_PKW_VERFUEG": [3, 1, 1, -8, 2, 3, 3, 1, 1, 1, 1],
        "V_FUEHR_PKW": [1, 1, 1, 2, 2, 2, 1, 1, 1, 1, 1],
        "V_FK_VERFUEG": [1, 1, 2, 1, 1, 1, 2, 2, 2, 2, 2],
    })
    # One row per leg: (HHNR, PNR, WNR, E_ZWECK_9, V_BEGINN_STUNDE, V_ANKUNFT_STUNDE,
    # V_ZWECK, E_START_ZWECK, V_START_LAGE, V_ZIEL_LAGE, GIS_LAENGE). V_ZWECK 8 / E_ZWECK_9 9
    # stand for a return home; V_START_LAGE / V_ZIEL_LAGE 1 = own home.
    rows = [
        (1, 1, 3, 9, 14, 15, 8, 6, 2, 1, 12.0),
        (1, 1, 1, 4, 12, 13, 6, 6, 2, 2, 3.0),
        (1, 1, 2, 4, 9, 10, 6, 8, 1, 2, 12.0),
        (1, 2, 1, 1, 8, 9, 1, 8, 1, 2, 5.0),
        (1, 2, 2, 9, 17, 18, 8, 1, 2, 1, 5.0),
        (2, 1, 1, 1, 7, 8, 1, 8, 1, 2, 20.0),
        (2, 1, 2, 9, 16, 17, 8, 1, 2, 1, 20.0),
        (2, 2, 1, 3, 7, 8, 5, 8, 1, 2, 2.0),
        (2, 2, 2, 9, 13, 14, 8, 5, 2, 1, 2.0),
        (2, 3, 1, 4, 7, 8, 6, 8, 1, 2, 8.0),
        (2, 3, 2, 9, 15, 16, 8, 6, 2, 1, 8.0),
        (3, 1, 1, 4, 7, -1, 6, 8, 1, 2, 30.0),
        (3, 1, 2, 9, 15, 16, 8, 6, 2, 1, 30.0),
        (4, 2, 1, 4, 18, 19, 7, 8, 1, 2, 4.0),
        (4, 2, 2, 9, 21, 22, 8, 7, 2, 1, 4.0),
        (4, 3, 1, 9, 10, 11, 8, 4, 2, 1, 1.0),
    ]
    legs = pd.DataFrame(rows, columns=["HHNR", "PNR", "WNR", "E_ZWECK_9", "V_BEGINN_STUNDE",
                                       "V_ANKUNFT_STUNDE", "V_ZWECK", "E_START_ZWECK",
                                       "V_START_LAGE", "V_ZIEL_LAGE", "GIS_LAENGE"])
    kreis_ags = legs["HHNR"].map({1: 3101000, 2: 3101000, 3: 3102000, 4: 3102000})
    legs["V_START_AGS"] = kreis_ags
    legs["V_ZIEL_AGS"] = kreis_ags
    legs["GIS_LAENGE_GUELTIG"] = 1
    legs["GEWICHT_W_ZENSUS"] = legs["HHNR"].map({1: 2.0, 2: 1.0, 3: 1.0, 4: 2.0})
    legs["REGIOSTAR7"] = 71
    return persons, legs, households


@pytest.fixture
def srv_frames():
    return _raw()


def _behaviour(srv_frames):
    persons, legs, households = srv_frames
    universe, _ = R.student_universe(persons, households)
    table, diagnostics = R.build_behaviour_table(universe, legs)
    return table, diagnostics


def _cell(table, control, universe_name, label):
    row = table[(table["control"] == control) & (table["universe"] == universe_name)
                & (table["label"] == label)]
    assert len(row) == 1
    return row.iloc[0]


def test_student_share_and_presence_by_hand(srv_frames):
    persons, legs, households = srv_frames
    universe, _ = R.student_universe(persons, households)
    table, _ = R.build_student_share_table(universe, legs)
    by_code = table.set_index("code")
    region = by_code.loc[REGION_CODE]
    assert region["student_share_adults"] == pytest.approx(5 / 14)   # weights students / adults
    assert by_code.loc["03101", "student_share_adults"] == pytest.approx(0.5)
    assert by_code.loc["03102", "student_share_adults"] == pytest.approx(0.25)
    assert region["student_share_20_29"] == pytest.approx(2 / 3)
    assert region["n_adults"] == 9 and region["n_students"] == 4
    assert region["students_weighted_persons"] == pytest.approx(5.0)
    assert region["presence_share"] == pytest.approx(3 / 5)
    # 1_1 [2], 1_2 [1], 3_1 [1], 3_2 [1]: (5)^2 / (4 + 1 + 1 + 1)
    assert region["n_effective_presence"] == pytest.approx(25 / 7)
    assert np.isnan(by_code.loc["03101", "presence_share"])          # region row only
    assert by_code.loc[WOLFSBURG, "n_adults"] == 0                    # Wolfsburg zero row
    assert np.isnan(by_code.loc[WOLFSBURG, "student_share_adults"])
    assert set(table["code"]) == {"03101", "03102", "03103", "03151", "03153", "03154",
                                  "03157", "03158", REGION_CODE}


def test_behaviour_labels_partition_both_universes(srv_frames):
    table, _ = _behaviour(srv_frames)
    for (control, universe_name), group in table.groupby(["control", "universe"]):
        assert group["share"].sum() == pytest.approx(1.0)
        expected = set(CONTROLS[control]) | ({NOT_STUDENT} if universe_name == UNIVERSE_ADULTS
                                             else set())
        assert set(group["label"]) == expected
    assert set(table["control"]) == set(CONTROLS)
    # Hand values: student_edu = 1_1 [2] + 3_1 [1] = 3 of 14 adults, of 5 students.
    assert _cell(table, "status_x_education_leg", UNIVERSE_ADULTS,
                 "student_edu")["share"] == pytest.approx(3 / 14)
    assert _cell(table, "status_x_education_leg", UNIVERSE_STUDENTS,
                 "student_edu")["share"] == pytest.approx(3 / 5)
    # trip class 1-2: 1_2 [1] and 3_1 [1] of 5; work leg: 1_2 [1] of 14.
    assert _cell(table, "trip_class", UNIVERSE_STUDENTS,
                 "student_trips_1_2")["share"] == pytest.approx(2 / 5)
    assert _cell(table, "work_leg", UNIVERSE_ADULTS,
                 "student_work")["share"] == pytest.approx(1 / 14)
    # Kish over the 9 adults' weights [2,1,2,1,1,1,2,2,2]: 14^2 / 24; over the students 25/7.
    adults_cell = _cell(table, "status_x_education_leg", UNIVERSE_ADULTS, NOT_STUDENT)
    assert adults_cell["n_effective"] == pytest.approx(196 / 24)
    students_cell = _cell(table, "status_x_education_leg", UNIVERSE_STUDENTS, "student_edu")
    assert students_cell["n_effective"] == pytest.approx(25 / 7)
    assert students_cell["se_share"] == pytest.approx(math.sqrt(0.6 * 0.4 / (25 / 7)))


def test_arrival_uses_the_first_education_leg(srv_frames):
    """1_1 [2] arrives 10 h on the first education leg by start hour (begins 9 h, WNR 2) and
    13 h on the later one (begins 12 h, WNR 1, listed first in the file): only
    "student_arrive_9_11" may carry it, so ordering by file position or by leg number alone
    fails. Base of the students universe: 1_1 [2], 1_2 [1], 3_2 [1] (3_1 has an unreadable
    arrival hour), so n_effective = 4^2 / (4 + 1 + 1) = 16/6 on this reduced base."""
    table, _ = _behaviour(srv_frames)
    arrive = _cell(table, "arrival", UNIVERSE_STUDENTS, "student_arrive_9_11")
    assert arrive["share"] == pytest.approx(2 / 4)
    assert arrive["n_effective"] == pytest.approx(16 / 6)
    assert _cell(table, "arrival", UNIVERSE_STUDENTS,
                 "student_arrive_12_later")["weight_sum"] == 0
    assert _cell(table, "arrival", UNIVERSE_STUDENTS,
                 "student_no_education_leg")["share"] == pytest.approx(2 / 4)


def test_unreadable_codes_are_excluded_and_counted(srv_frames):
    """4_3 (V_ERW -10) is neither a student nor "not a student"; 3_2 (E_OEV_FK -10) is not
    "no flatrate"; 3_1 (arrival hour -1) is not "no education leg". Each is counted."""
    persons, legs, households = srv_frames
    universe, universe_diagnostics = R.student_universe(persons, households)
    assert universe.set_index("pid")["is_student"].isna().sum() == 1
    share, share_diagnostics = R.build_student_share_table(universe, legs)
    assert share_diagnostics["n_excluded_unreadable_employment_code"] == 1
    table, diagnostics = R.build_behaviour_table(universe, legs)
    assert diagnostics["n_excluded_unreadable_employment_code"] == 1
    assert diagnostics["n_students_unreadable_arrival_hour"] == 1
    assert diagnostics["n_students_unreadable_season_ticket"] == 1
    # not_student = the 9 readable non-students only, never 4_3 [2].
    assert _cell(table, "work_leg", UNIVERSE_ADULTS, NOT_STUDENT)["weight_sum"] == 9
    # no flatrate = 1_2 [1] (code 1) + 3_1 [1] (code -8, kept as no flatrate); never 3_2.
    no_flat = _cell(table, "season_ticket", UNIVERSE_ADULTS, "student_no_flatrate")
    assert no_flat["weight_sum"] == 2 and no_flat["share"] == pytest.approx(2 / 13)
    assert _cell(table, "season_ticket", UNIVERSE_STUDENTS,
                 "student_flatrate")["share"] == pytest.approx(2 / 4)
    # no education leg = 1_2 [1] + 3_2 [1]; 3_1 is excluded from the arrival control only.
    assert _cell(table, "arrival", UNIVERSE_ADULTS,
                 "student_no_education_leg")["weight_sum"] == 2
    assert _cell(table, "status_x_education_leg", UNIVERSE_STUDENTS,
                 "student_edu")["weight_sum"] == 3


def test_unreadable_start_hour_excludes_from_arrival_only(srv_frames):
    """3_1 gets a readable arrival hour (8 h) but an unreadable start hour (-1): the order of
    their education legs is undeterminable, so they leave the arrival control (1 of the 2
    students with an education leg) and stay in every other control."""
    persons, legs, households = srv_frames
    first_leg_of_3_1 = (legs["HHNR"] == 3) & (legs["PNR"] == 1) & (legs["WNR"] == 1)
    legs.loc[first_leg_of_3_1, ["V_BEGINN_STUNDE", "V_ANKUNFT_STUNDE"]] = [-1, 8]
    table, diagnostics = _behaviour((persons, legs, households))
    assert diagnostics["n_students_unreadable_start_hour"] == 1
    assert diagnostics["n_students_unreadable_arrival_hour"] == 0
    assert _cell(table, "arrival", UNIVERSE_STUDENTS,
                 "student_arrive_before_9")["weight_sum"] == 0
    assert _cell(table, "arrival", UNIVERSE_STUDENTS,
                 "student_no_education_leg")["weight_sum"] == 2
    assert _cell(table, "status_x_education_leg", UNIVERSE_STUDENTS,
                 "student_edu")["weight_sum"] == 3


def test_every_arrival_hour_unreadable_raises(srv_frames):
    """A recoded arrival-hour column excludes 2 of the 2 students with an education leg. Over
    all 4 adult students that would be 50 % and pass the limit; the base is the students with
    an education leg, so it raises."""
    persons, legs, households = srv_frames
    legs["V_ANKUNFT_STUNDE"] = -1
    with pytest.raises(ValueError, match="arrival control"):
        _behaviour((persons, legs, households))


def test_distance_observations_keep_adult_students_only(srv_frames):
    """3_1 is made 17: still a student, but outside the adult universe of the distance table
    (counted). 2_2 is a pupil, 4_3 has an unreadable V_ERW (excluded, counted), 9_1 is in no
    household of the universe (excluded, counted); only 1_1 remains."""
    persons, legs, households = srv_frames
    persons.loc[(persons["HHNR"] == 3) & (persons["PNR"] == 1), "V_ALTER"] = 17
    universe, _ = R.student_universe(persons, households)
    observations = pd.DataFrame({"hhnr": [1, 3, 2, 4, 9], "pnr": [1, 1, 2, 3, 1],
                                 "kreis": ["03101", "03102", "03101", "03102", "03101"],
                                 "distance_km": [12.0, 30.0, 2.0, 1.0, 5.0],
                                 "weight": [2.0, 1.0, 1.0, 2.0, 1.0]})
    kept, diagnostics = R.select_student_observations(observations, universe)
    assert list(zip(kept["hhnr"], kept["pnr"])) == [(1, 1)]
    assert diagnostics["n_observations_students_not_adult"] == 1
    assert diagnostics["n_observations_unreadable_employment_code"] == 1
    assert diagnostics["n_observations_not_in_universe"] == 1


@pytest.mark.parametrize("column, value", [("V_ERW", 99), ("E_OEV_FK", 99)])
def test_undocumented_code_raises(srv_frames, column, value):
    persons, legs, households = srv_frames
    persons.loc[0, column] = value
    with pytest.raises(ValueError, match=column):
        universe, _ = R.student_universe(persons, households)
        R.build_behaviour_table(universe, legs)


def test_education_status_by_age_by_hand(srv_frames):
    """Adults with an education leg: 2_3 (19, V_ERW 8) is an apprentice although code 8 is in
    the employed set; 1_1 [2] and 3_1 [1] (20-29) are students; 4_2 (40, V_ERW 9) employed."""
    persons, legs, households = srv_frames
    universe, _ = R.student_universe(persons, households)
    table, _ = R.build_education_status_table(universe, legs)
    assert len(table) == 15
    cells = table.set_index(["age_band", "status"])
    assert cells.loc[("18_19", "apprentice"), "share"] == pytest.approx(1.0)
    assert cells.loc[("18_19", "employed"), "share"] == 0
    assert cells.loc[("20_29", "student"), "n_unweighted"] == 2
    assert cells.loc[("20_29", "student"), "share"] == pytest.approx(1.0)
    assert cells.loc[("30plus", "employed"), "share"] == pytest.approx(1.0)


def test_effective_size_and_standard_error_by_hand():
    assert effective_sample_size([1, 1, 2]) == pytest.approx(16 / 6)
    assert share_standard_error(0.5, 16 / 6) == pytest.approx(math.sqrt(0.25 / (16 / 6)))
    assert np.isnan(effective_sample_size([]))
    assert np.isnan(share_standard_error(float("nan"), float("nan")))


def test_season_ticket_codes_match_the_kreis_table_extractor():
    path = REPO / "scripts" / "extract_srv_kreis_tables.py"
    spec = importlib.util.spec_from_file_location("extract_srv_kreis_tables", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert set(R.FLATRATE_E_OEV_FK) == {module.TICKET_GROUP_DEUTSCHLANDTICKET_CODE,
                                        *module.TICKET_GROUP_OTHER_FLATRATE_CODES}
    assert set(R.NO_FLATRATE_E_OEV_FK) == set(module.TICKET_GROUP_NOT_FLATRATE_CODES)


def test_cli_writes_all_tables_with_headers(srv_frames, tmp_path):
    persons, legs, households = srv_frames
    raw = tmp_path / "srv2023_raw"
    raw.mkdir()
    for frame, name in ((persons, "SrV2023_Personen.csv"), (legs, "SrV2023_Wege.csv"),
                        (households, "SrV2023_Haushalte.csv")):
        frame.to_csv(raw / name, sep=";", decimal=",", encoding="cp1252", index=False)
    out = tmp_path / "out"
    path = REPO / "scripts" / "extract_srv_student_reference.py"
    spec = importlib.util.spec_from_file_location("extract_srv_student_reference", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["--raw", str(raw), "--out-dir", str(out), "--source-commit", "abc1234",
                        "--n-bootstrap", "20"]) == 0

    tables = {}
    for name in (R.STUDENT_SHARE_TABLE, R.EDUCATION_STATUS_TABLE, R.EDUCATION_DISTANCE_TABLE,
                 R.BEHAVIOUR_TABLE, R.COMPARISON_TABLE):
        text = (out / name).read_text(encoding="utf-8")
        assert text.startswith("#") and "\r\n" not in text
        header = [line for line in text.splitlines() if line.startswith("#")]
        assert sum(line.startswith("# Exclusions:") for line in header) == 1
        assert any("abc1234" in line for line in header)
        tables[name] = pd.read_csv(out / name, comment="#", dtype={"code": str})
    kreis_codes = {"03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158",
                   REGION_CODE}
    assert set(tables[R.STUDENT_SHARE_TABLE]["code"]) == kreis_codes
    assert set(tables[R.EDUCATION_DISTANCE_TABLE]["code"]) == kreis_codes
    assert len(tables[R.EDUCATION_STATUS_TABLE]) == 15
    # 5 controls; per control the student labels twice plus not_student once.
    assert len(tables[R.BEHAVIOUR_TABLE]) == sum(2 * len(labels) + 1
                                                 for labels in CONTROLS.values())
    # Students with a distance observation: 1_1 (12 km, Kreis 03101) and 3_1 (30 km, 03102);
    # the pupil 2_2 and the apprentice 2_3 have one too but are no students.
    distance = tables[R.EDUCATION_DISTANCE_TABLE].set_index("code")
    assert distance.loc[REGION_CODE, "n_persons"] == 2
    assert distance.loc["03101", "share_10_25"] == pytest.approx(1.0)
    comparison = tables[R.COMPARISON_TABLE]
    assert set(comparison["group"]) == {"student", "other_adult"}
    assert set(comparison["variable"]) == {"V_PKW_VERFUEG", "V_FUEHR_PKW", "V_FK_VERFUEG"}


def test_education_distance_noise_floor_is_seeded():
    """03101: distances [3, 7, 10, 30, 120] km, weights [1, 1, 1, 2, 1]: 10 km falls into the
    10_25 band (left-inclusive), so 0_5 = 1/6, 5_10 = 1/6, 10_25 = 1/6, 25_50 = 2/6,
    100_plus = 1/6."""
    observations = pd.DataFrame({
        "kreis": ["03101"] * 5 + ["03102"] * 3,
        "distance_km": [3.0, 7.0, 10.0, 30.0, 120.0, 1.0, 2.0, 60.0],
        "weight": [1.0, 1.0, 1.0, 2.0, 1.0, 1.0, 2.0, 1.0],
    })
    first = R.build_education_distance_table(observations, n_bootstrap=50, seed=7)
    second = R.build_education_distance_table(observations, n_bootstrap=50, seed=7)
    pd.testing.assert_series_equal(first["emd_noise_95"], second["emd_noise_95"])
    share_columns = ["share_%s" % label for label in STUDENT_DISTANCE_LABELS]
    by_code = first.set_index("code")
    for code in ("03101", "03102", REGION_CODE):
        assert by_code.loc[code, share_columns].sum() == pytest.approx(1.0)
    assert by_code.loc["03101", "share_10_25"] == pytest.approx(1 / 6)
    assert by_code.loc["03101", "share_25_50"] == pytest.approx(2 / 6)
    assert by_code.loc[REGION_CODE, "n_persons"] == 8
    assert by_code.loc[WOLFSBURG, "n_persons"] == 0
    assert by_code.loc[WOLFSBURG, share_columns].isna().all()
    assert np.isnan(by_code.loc[WOLFSBURG, "emd_noise_95"])
