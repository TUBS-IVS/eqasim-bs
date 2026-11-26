"""Unit tests for the SrV 2023 university-student aggregates (issue #140, Phase 0, table R5).

Synthetic SrV-shaped frames only -- the raw scientific-use microdata is local-only and must
never be read by a test. Every expected value is computed by hand from the table in the
docstring of :func:`_raw`; the weights are chosen so that an unweighted share, a mean of the
two Kreis shares and the right weighted share all differ.
"""
import hashlib
import importlib.util
import math
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration import srv_student_reference as R
from braunschweig.calibration import student_parameters as SP
from braunschweig.calibration.srv_participation_universe import REGION_CODE
from braunschweig.calibration.srv_plan_structure import harmonise_srv
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


def test_students_away_from_home_all_day_are_counted(srv_frames):
    """The SrV universe drops persons away from home all day (E_ANZ_WEGE -7); MiD keeps them, so
    the number of STUDENTS lost this way must be in the files. 3_2 (student, 35) and 3_3 (not a
    student) are away; 2_2 (pupil, 15) is away too and is no student."""
    persons, legs, households = srv_frames
    away = (((persons["HHNR"] == 3) & persons["PNR"].isin([2, 3]))
            | ((persons["HHNR"] == 2) & (persons["PNR"] == 2)))
    persons.loc[away, "E_ANZ_WEGE"] = -7
    universe, diagnostics = R.student_universe(persons, households)
    assert diagnostics["away_from_home"] == 3
    assert diagnostics["n_students_away_from_home"] == 1
    assert diagnostics["n_adult_students_away_from_home"] == 1
    assert "3_2" not in set(universe["pid"])


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


def _cli_frames(srv_frames):
    """All raw columns the nine tables read: Phase 0 fixture + parameter + post-MATSim + start
    minute and a harmonisable V_ZWECK (the fixture's raw V_ZWECK is a distance-table code)."""
    persons, legs, households = _post_matsim_frames(_parameter_frames(srv_frames))
    legs["V_ZWECK"] = legs["E_ZWECK_9"].map(_HARMONISED_V_ZWECK)
    legs["V_BEGINN_MINUTE"] = 0
    return persons, legs, households


def test_cli_writes_all_tables_with_headers(srv_frames, tmp_path):
    persons, legs, households = _cli_frames(srv_frames)
    raw = tmp_path / "srv2023_raw"
    raw.mkdir()
    for frame, name in ((persons, "SrV2023_Personen.csv"), (legs, "SrV2023_Wege.csv"),
                        (households, "SrV2023_Haushalte.csv")):
        frame.to_csv(raw / name, sep=";", decimal=",", encoding="cp1252", index=False)
    sites = tmp_path / "lsn_university_sites.csv"
    sites.write_text("# local-only synthetic site table\n"
                     "institution,site_kreis_ars5,scope\n"
                     "TU,03101,local\nHAW,03158,local\nHS,03241,surrounding\n",
                     encoding="utf-8")
    out = tmp_path / "out"
    path = REPO / "scripts" / "extract_srv_student_reference.py"
    spec = importlib.util.spec_from_file_location("extract_srv_student_reference", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.main(["--raw", str(raw), "--sites", str(sites), "--out-dir", str(out),
                        "--source-commit", "abc1234", "--n-bootstrap", "20"]) == 0

    new_names = (R.PARAMETER_TABLE, R.YOUNG_ADULT_STATUS_TABLE, R.POST_MATSIM_TABLE,
                 R.DESTINATION_TABLE)
    sites_hash = hashlib.sha256(sites.read_bytes()).hexdigest()
    for name in new_names:
        header = [line for line in (out / name).read_text(encoding="utf-8").splitlines()
                  if line.startswith("#")]
        assert header and sum(line.startswith("# Exclusions:") for line in header) == 1
        # the local-only site table is named by its hash, never by its path
        assert sites_hash in " ".join(header) and str(sites) not in " ".join(header)
    parameter_header = " ".join(line for line in (out / R.PARAMETER_TABLE).read_text(
        encoding="utf-8").splitlines() if line.startswith("#"))
    assert "education_leg_share_harmonised_purpose" in parameter_header
    assert "education_leg_share_control_definition" in parameter_header
    assert "BEFORE" in parameter_header  # away rows: measured before the universe filter
    assert "03101" in " ".join(line for line in (out / R.DESTINATION_TABLE).read_text(
        encoding="utf-8").splitlines() if line.startswith("#"))
    destination = pd.read_csv(out / R.DESTINATION_TABLE, comment="#")
    assert len(destination) > 0

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


# ------------------------------------------------------------------ Phase 1: parameter table
# Trip times in minutes after midnight, (E_BEGINN, E_ANKUNFT), keyed by (HHNR, PNR, WNR). The
# harmonised purpose of a leg is taken from E_ZWECK_9 (1 work, 3 and 4 education, 9 home).
# Legs not listed use (480, 510) for WNR 1 and (1020, 1050) for later legs.
_LEG_TIMES = {(1, 1, 1): (420, 450), (1, 1, 2): (600, 630), (1, 1, 3): (900, 930),
              (1, 2, 1): (540, 570), (1, 2, 2): (1020, 1050),
              (3, 1, 1): (420, -1), (3, 1, 2): (900, 930)}
_HARMONISED_V_ZWECK = {1: 1, 3: 3, 4: 4, 9: 19}


def _parameter_frames(srv_frames):
    """The Phase 0 fixture plus the columns of the Phase 1 parameter table (copies).

    Adult students of the universe: 1_1 [2] (22, female, licence, car access, Deutschlandticket,
    household 1: several adults, 2 cars, bicycles), 1_2 [1] (24, male, licence, car access, code 1
    ticket), 3_1 [1] (21, female, no licence, no car access, code -8, household 3: alone, 1 car,
    no bicycle) and 3_2 [1] (35, divers -> no sex, licence, E_ZUGANG_PKW -7, E_OEV_FK -10).
    """
    persons, legs, households = (frame.copy() for frame in srv_frames)
    persons["E_ANZ_WEGE2"] = [3, 2, 2, 2, 2, 2, 0, 0, 0, 2, 1]
    persons["STICHTAG_WTAG"] = 2
    persons["GEWICHT_P_WERKTAG"] = -6.0
    persons["V_GESCHLECHT"] = [2, 1, 1, 2, 2, 2, 3, 1, 2, 1, 1]
    persons["E_ZUGANG_PKW"] = [1, 1, 1, 2, 2, 2, -7, 1, 1, 1, 1]
    times = [_LEG_TIMES.get((h, p, w), (480, 510) if w == 1 else (1020, 1050))
             for h, p, w in zip(legs["HHNR"], legs["PNR"], legs["WNR"])]
    legs["E_BEGINN"] = [begin for begin, _ in times]
    legs["E_ANKUNFT"] = [arrival for _, arrival in times]
    households["E_HHTYP"] = [2, 1, 4, 2]
    households["V_ANZ_PKW"] = [2, 1, 1, 0]
    households["E_ANZ_RAD_ALLE_6"] = [3, 0, 0, -7]
    return persons, legs, households


def _harmonise(persons, legs, households, purpose_overrides=None):
    """``harmonise_srv`` output; V_ZWECK from E_ZWECK_9, single legs overridable by key."""
    harmonised_legs = legs.copy()
    harmonised_legs["V_ZWECK"] = legs["E_ZWECK_9"].map(_HARMONISED_V_ZWECK)
    for (hhnr, pnr, wnr), code in (purpose_overrides or {}).items():
        row = ((harmonised_legs["HHNR"] == hhnr) & (harmonised_legs["PNR"] == pnr)
               & (harmonised_legs["WNR"] == wnr))
        harmonised_legs.loc[row, "V_ZWECK"] = code
    return harmonise_srv(persons, harmonised_legs, households)


def _build(frames, purpose_overrides=None):
    persons, legs, households = frames
    universe, _ = R.student_universe(persons, households)
    harmonised_persons, harmonised_trips = _harmonise(persons, legs, households,
                                                      purpose_overrides)
    return R.build_parameter_table(universe, persons, households, harmonised_persons,
                                   harmonised_trips, legs)


def _parameter_cell(table, parameter, label, base=None):
    rows = table[(table["parameter"] == parameter) & (table["label"] == label)]
    if base is not None:
        rows = rows[rows["base"] == base]
    assert len(rows) == 1, (parameter, label, base)
    return rows.iloc[0]


def test_parameter_table_partitions_each_parameter_and_base(srv_frames):
    table, _ = _build(_parameter_frames(srv_frames))
    assert list(table.columns) == SP.PARAMETER_COLUMNS
    for (parameter, base), group in table.groupby(["parameter", "base"]):
        assert group["share"].sum() == pytest.approx(1.0), (parameter, base)
    assert set(table["parameter"]) == set(SP.PARAMETERS)
    bases_by_parameter = table.groupby("parameter")["base"].agg(set).to_dict()
    for parameter, spec in SP.PARAMETERS.items():
        assert bases_by_parameter[parameter] == {spec.base, *SP.EXTRA_BASES.get(parameter, ())}
    # every label of every vocabulary is emitted, in every base the parameter lives on
    for parameter, spec in SP.PARAMETERS.items():
        for base in bases_by_parameter[parameter]:
            labels = table[(table["parameter"] == parameter) & (table["base"] == base)]["label"]
            assert list(labels) == list(spec.labels)


# Adult students 1_1 [2], 1_2 [1], 3_1 [1], 3_2 [1]; each share is the weight of the named
# students over the weight of the base, i.e. of the students NOT excluded from the parameter.
_EXPECTED_SHARES = {
    ("age_group", "20_24"): 4 / 5, ("age_group", "30plus"): 1 / 5,
    ("sex", "female"): 3 / 4, ("sex", "male"): 1 / 4,               # 3_2 divers: excluded
    ("household_type", "several_adults"): 3 / 5, ("household_type", "alone"): 2 / 5,
    ("lives_with_person_18_years_older", "yes"): 1 / 5,    # 3_1 (21) with the 50 year old 3_3
    ("lives_with_person_18_years_older", "no"): 4 / 5,     # 3_2 (35): gap 15
    ("licence", "yes"): 4 / 5, ("licence", "no"): 1 / 5,
    ("cars_in_household", "2plus"): 3 / 5, ("cars_in_household", "1"): 2 / 5,
    ("car_access", "yes"): 3 / 4, ("car_access", "no"): 1 / 4,       # 3_2 (-7) excluded
    ("bicycle_in_household", "yes"): 3 / 5, ("bicycle_in_household", "no"): 2 / 5,
    ("season_ticket", "flatrate"): 2 / 4, ("season_ticket", "no_flatrate"): 2 / 4,
    ("ticket_group4", "deutschlandticket"): 2 / 4, ("ticket_group4", "occasional_ticket"): 1 / 4,
    ("ticket_group4", "never_pt"): 1 / 4,                            # 3_2 (-10) excluded
    ("education_leg", "yes"): 4 / 5, ("education_leg", "no"): 1 / 5,    # 1_2's first trip
    ("work_leg", "no"): 1.0,                                         # is education, see below
    ("leisure_leg", "no"): 1.0, ("shop_or_errand_leg", "no"): 1.0, ("escort_leg", "no"): 1.0,
    ("mobile", "yes"): 4 / 5, ("mobile", "no"): 1 / 5,
    ("trip_class", "0"): 1 / 5, ("trip_class", "1_2"): 2 / 5, ("trip_class", "3_4"): 2 / 5,
    # mobile students 1_1 [2], 1_2 [1], 3_1 [1]
    ("first_departure_hour", "7_8"): 3 / 4, ("first_departure_hour", "9_11"): 1 / 4,
    ("last_arrival_home_hour", "before_16"): 3 / 4, ("last_arrival_home_hour", "16_18"): 1 / 4,
    ("returns_home", "1"): 1.0,
    # education students 1_1 [2] (150 + 270 min at education, first arrival 7:30), 1_2 [1] (450
    # min, arrival 9:30) and 3_1 [1] (arrival unreadable: excluded from both education parameters)
    ("time_at_education", "6h_more"): 1.0,
    ("arrival_band", "before_9"): 2 / 3, ("arrival_band", "9_11"): 1 / 3,
    ("away_all_day", "no"): 1.0,
}


def test_parameter_table_by_hand(srv_frames):
    """1_2's first trip (E_ZWECK_9 1) is given the harmonised purpose education (V_ZWECK 5), so
    three students have an education leg and one of them is excluded from the two education
    parameters (a third, not a limit case of the exclusion rule)."""
    table, diagnostics = _build(_parameter_frames(srv_frames), purpose_overrides={(1, 2, 1): 5})
    for parameter, spec in SP.PARAMETERS.items():
        for label in spec.labels:
            expected = _EXPECTED_SHARES.get((parameter, label), 0.0)
            for base in (spec.base, *SP.EXTRA_BASES.get(parameter, ())):
                cell = _parameter_cell(table, parameter, label, base)
                assert cell["share"] == pytest.approx(expected), (parameter, label, base)
    sex_female = _parameter_cell(table, "sex", "female")
    assert sex_female["n_unweighted"] == 2 and sex_female["weight_sum"] == pytest.approx(3.0)
    assert sex_female["n_effective"] == pytest.approx(16 / 6)   # base weights [2, 1, 1]
    assert diagnostics["n_adult_students"] == 4
    # the base of a day parameter with a smaller base is in the header next to its exclusions
    assert diagnostics["n_base_first_departure_hour"] == 3
    assert diagnostics["n_base_last_arrival_home_hour"] == 3
    assert diagnostics["n_base_time_at_education"] == 3
    assert diagnostics["n_base_arrival_band"] == 3
    excluded = {name: value for name, value in diagnostics.items()
                if name.startswith("n_excluded_") and value}
    assert excluded == {"n_excluded_unreadable_employment_code": 1, "n_excluded_sex": 1,
                        "n_excluded_car_access": 1, "n_excluded_season_ticket": 1,
                        "n_excluded_ticket_group4": 1, "n_excluded_time_at_education": 1,
                        "n_excluded_arrival_band": 1}


def test_parameter_table_emits_empty_cells(srv_frames):
    table, _ = _build(_parameter_frames(srv_frames))
    empty = _parameter_cell(table, "household_type", "with_children")
    assert empty["n_unweighted"] == 0 and empty["weight_sum"] == 0 and empty["share"] == 0
    other = table[(table["parameter"] == "household_type")
                  & (table["label"] != "with_children")]
    assert other["share"].sum() == pytest.approx(1.0)


def test_away_share_is_measured_before_the_universe_filter(srv_frames):
    """3_2 (student [1]) and 3_3 (other adult [2]) were away all day: outside the universe,
    inside this share. Students [2, 1, 1, 1] -> 1/5; other adults 2_1 [2], 2_3 [1], 3_3 [2],
    4_2 [2] (4_1 has an invalid weight, 4_3 an unreadable V_ERW, 2_2 is 15) -> 2/7."""
    persons, legs, households = _parameter_frames(srv_frames)
    persons.loc[(persons["HHNR"] == 3) & persons["PNR"].isin([2, 3]), "E_ANZ_WEGE"] = -7
    persons.loc[(persons["HHNR"] == 4) & (persons["PNR"] == 1), "GEWICHT_P_ZENSUS"] = -1.0
    rows, diagnostics = R.away_all_day_rows(persons, households)
    students = rows[rows["base"] == SP.BASE_STUDENTS_ALL].set_index("label")
    others = rows[rows["base"] == SP.BASE_OTHER_ADULTS_ALL].set_index("label")
    assert students.loc["yes", "share"] == pytest.approx(1 / 5)
    assert students.loc["yes", "n_unweighted"] == 1 and students["n_unweighted"].sum() == 4
    assert students.loc["yes", "n_effective"] == pytest.approx(25 / 7)
    assert others.loc["yes", "share"] == pytest.approx(2 / 7)
    assert others["n_unweighted"].sum() == 4
    assert set(rows["parameter"]) == {"away_all_day"}
    assert diagnostics["n_excluded_invalid_weight"] == 1
    assert diagnostics["n_excluded_unreadable_employment_code"] == 1
    # the universe really drops both away persons, so a share measured after it would be 0
    universe, _ = R.student_universe(persons, households)
    assert not {"3_2", "3_3"} & set(universe["pid"])


def test_away_rows_apply_the_weekday_guard_and_count_missing_ages(srv_frames):
    persons, legs, households = _parameter_frames(srv_frames)
    foreign_weekday = persons.copy()
    foreign_weekday.loc[0, "MITTL_WERKTAG"] = 2
    with pytest.raises(ValueError, match="MITTL_WERKTAG"):
        R.away_all_day_rows(foreign_weekday, households)
    persons.loc[(persons["HHNR"] == 4) & (persons["PNR"] == 2), "V_ALTER"] = -9
    rows, diagnostics = R.away_all_day_rows(persons, households)
    assert diagnostics["n_missing_age"] == 1                     # 4_2: in neither base
    others = rows[rows["base"] == SP.BASE_OTHER_ADULTS_ALL]
    assert others["n_unweighted"].sum() == 4                     # 2_1, 2_3, 3_3, 4_1
    persons["V_ALTER"] = -9
    with pytest.raises(ValueError, match="valid age"):
        R.away_all_day_rows(persons, households)


def test_away_rows_apply_the_zgb_kreis_rule(srv_frames):
    persons, legs, households = _parameter_frames(srv_frames)
    no_household = households[households["HHNR"] != 4]
    rows, diagnostics = R.away_all_day_rows(persons, no_household)
    assert diagnostics["n_excluded_missing_kreis"] == 3          # 4_1, 4_2, 4_3
    others = rows[rows["base"] == SP.BASE_OTHER_ADULTS_ALL]
    assert others["n_unweighted"].sum() == 3                     # 2_1, 2_3, 3_3
    foreign = households.copy()
    foreign.loc[foreign["HHNR"] == 4, "AGS"] = 9162000
    with pytest.raises(ValueError, match="09162"):
        R.away_all_day_rows(persons, foreign)


@pytest.mark.parametrize("hh3_type, label", [(3, "alone"), (4, "alone"), (2, "several_adults"),
                                             (1, "with_children")])
def test_household_type_codes_by_hand(srv_frames, hh3_type, label):
    """Household 3 holds the students 3_1 and 3_2 (weight 2 of 5); household 1 stays code 2."""
    persons, legs, households = _parameter_frames(srv_frames)
    households.loc[households["HHNR"] == 3, "E_HHTYP"] = hh3_type
    table, _ = _build((persons, legs, households))
    expected = {"several_adults": 3 / 5, "alone": 0.0, "with_children": 0.0}
    expected[label] += 2 / 5
    for name, share in expected.items():
        assert _parameter_cell(table, "household_type", name)["share"] == pytest.approx(share)


def _five_adult_students(persons):
    """2_1 (27, weight 2, household 2) becomes a student: [2, 1, 2, 1, 1], so that the two
    students of one household are 2 of 5, away from the exclusion limit."""
    persons.loc[(persons["HHNR"] == 2) & (persons["PNR"] == 1), "V_ERW"] = 7


def test_household_type_negative_code_is_excluded_and_counted(srv_frames):
    """Household 3 (3_1, 3_2) has code -5: excluded, counted. Household 1 (code 2) holds 1_1 [2]
    and 1_2 [1], household 2 (code 1) holds 2_1 [2]: several adults 3/5, with children 2/5."""
    persons, legs, households = _parameter_frames(srv_frames)
    _five_adult_students(persons)
    households.loc[households["HHNR"] == 3, "E_HHTYP"] = -5
    table, diagnostics = _build((persons, legs, households))
    assert diagnostics["n_adult_students"] == 5
    assert diagnostics["n_excluded_household_type"] == 2
    several = _parameter_cell(table, "household_type", "several_adults")
    with_children = _parameter_cell(table, "household_type", "with_children")
    assert several["share"] == pytest.approx(3 / 5)
    assert with_children["share"] == pytest.approx(2 / 5)


@pytest.mark.parametrize("oldest_age, yes_weight", [(53, 2), (52, 1)])
def test_older_person_in_the_household_needs_an_18_year_gap(srv_frames, oldest_age, yes_weight):
    """3_3 is [oldest_age]: against 3_1 (21) always 18+ older; against 3_2 (35) exactly 18 at 53
    (yes, the gap is inclusive) and 17 at 52 (no). Household 1 has nobody 18 years older."""
    persons, legs, households = _parameter_frames(srv_frames)
    persons.loc[(persons["HHNR"] == 3) & (persons["PNR"] == 3), "V_ALTER"] = oldest_age
    table, _ = _build((persons, legs, households))
    cell = _parameter_cell(table, "lives_with_person_18_years_older", "yes")
    assert cell["weight_sum"] == yes_weight and cell["share"] == pytest.approx(yes_weight / 5)


def test_unknown_household_member_age_is_excluded_not_assumed(srv_frames):
    """1_2's age is missing (-9), so 1_2 is no adult student any more and 1_1 cannot tell whether
    their household holds someone 18 years older: excluded from that parameter, counted. 3_1
    [1] has a 29 year older housemate (yes), 3_2 [1] none (no)."""
    persons, legs, households = _parameter_frames(srv_frames)
    persons.loc[(persons["HHNR"] == 1) & (persons["PNR"] == 2), "V_ALTER"] = -9
    table, diagnostics = _build((persons, legs, households))
    assert diagnostics["n_adult_students"] == 3
    assert diagnostics["n_students_missing_age"] == 1        # 1_2: counted, in no base
    assert diagnostics["n_excluded_lives_with_person_18_years_older"] == 1
    assert _parameter_cell(table, "lives_with_person_18_years_older",
                           "yes")["share"] == pytest.approx(1 / 2)


@pytest.mark.parametrize("make_column", [
    lambda: pd.Series([np.nan, 1, 1, 0], dtype="float64"),       # NaN for the missing code
    lambda: pd.Series([-10, 1, 1, 0], dtype="int64"),            # the documented -10
])
def test_integer_coded_car_column_with_a_missing_code(srv_frames, make_column):
    """Household 1 (students 1_1 [2], 1_2 [1]) has a missing car count: excluded, counted (2 of
    5 students); the remaining label must be "1", not "1.0", whatever the column dtype. 2_1 [2],
    3_1 [1] and 3_2 [1] have one car each."""
    persons, legs, households = _parameter_frames(srv_frames)
    _five_adult_students(persons)
    households["V_ANZ_PKW"] = make_column().values
    table, diagnostics = _build((persons, legs, households))
    assert diagnostics["n_excluded_cars_in_household"] == 2
    cell = _parameter_cell(table, "cars_in_household", "1")
    assert cell["share"] == pytest.approx(1.0) and cell["weight_sum"] == pytest.approx(4.0)


def _kreis_table_module():
    path = REPO / "scripts" / "extract_srv_kreis_tables.py"
    spec = importlib.util.spec_from_file_location("extract_srv_kreis_tables", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_ticket_group4_codes_match_the_kreis_table_extractor():
    module = _kreis_table_module()
    assert R.DEUTSCHLANDTICKET_E_OEV_FK == module.TICKET_GROUP_DEUTSCHLANDTICKET_CODE
    assert set(R.OTHER_FLATRATE_E_OEV_FK) == set(module.TICKET_GROUP_OTHER_FLATRATE_CODES)
    assert set(R.TICKET_GROUP4_NEVER_E_OEV_FK) == set(module.TICKET_GROUP4_NEVER_CODES)
    assert (set(R.TICKET_GROUP4_OCCASIONAL_E_OEV_FK)
            == set(module.TICKET_GROUP4_OCCASIONAL_CODES))


def _group4_cases():
    module = _kreis_table_module()
    cases = [(module.TICKET_GROUP_DEUTSCHLANDTICKET_CODE, "deutschlandticket")]
    cases += [(code, "other_flatrate") for code in module.TICKET_GROUP_OTHER_FLATRATE_CODES]
    cases += [(code, "never_pt") for code in module.TICKET_GROUP4_NEVER_CODES]
    cases += [(code, "occasional_ticket") for code in module.TICKET_GROUP4_OCCASIONAL_CODES]
    return cases


@pytest.mark.parametrize("code, group", _group4_cases())
def test_ticket_group4_uses_the_kreis_table_code_sets(srv_frames, code, group):
    """1_1 [2] stays Deutschlandticket and 3_1 [1] never_pt; 1_2 [1] takes the code under test
    (3_2 is excluded, -10): base weight 4."""
    persons, legs, households = _parameter_frames(srv_frames)
    persons.loc[(persons["HHNR"] == 1) & (persons["PNR"] == 2), "E_OEV_FK"] = code
    table, _ = _build((persons, legs, households))
    expected = {"deutschlandticket": 2.0, "other_flatrate": 0.0, "never_pt": 1.0,
                "occasional_ticket": 0.0}
    expected[group] += 1.0
    for name, weight in expected.items():
        assert _parameter_cell(table, "ticket_group4", name)["share"] == pytest.approx(weight / 4)
    flatrate = group in ("deutschlandticket", "other_flatrate")
    assert (_parameter_cell(table, "season_ticket", "flatrate")["weight_sum"]
            == (3.0 if flatrate else 2.0))


def test_both_education_leg_definitions_are_reported_and_differ(srv_frames):
    """Harmonised purpose: 1_2's first trip (E_ZWECK_9 1, work in the control definition) is
    changed to V_ZWECK 5 (education): education students 1_1 [2], 1_2 [1], 3_1 [1] = 4/5. Control
    definition (E_ZWECK_9 in 3, 4): 1_1 and 3_1 = 3/5."""
    frames = _parameter_frames(srv_frames)
    table, diagnostics = _build(frames, purpose_overrides={(1, 2, 1): 5})
    assert _parameter_cell(table, "education_leg", "yes")["share"] == pytest.approx(4 / 5)
    assert diagnostics["education_leg_share_harmonised_purpose"] == pytest.approx(4 / 5)
    assert diagnostics["education_leg_share_control_definition"] == pytest.approx(3 / 5)
    # without the override both definitions agree on this fixture
    _, plain = _build(frames)
    assert plain["education_leg_share_harmonised_purpose"] == pytest.approx(3 / 5)
    assert plain["education_leg_share_control_definition"] == pytest.approx(3 / 5)


@pytest.mark.parametrize("where, column, value", [
    ("persons", "V_GESCHLECHT", 7), ("persons", "E_ZUGANG_PKW", 5), ("persons", "V_FUEHR_PKW", 3),
    ("persons", "E_OEV_FK", 99), ("households", "E_HHTYP", 9),
    ("households", "V_ANZ_PKW", -5), ("households", "E_ANZ_RAD_ALLE_6", 9)])
def test_undocumented_parameter_codes_raise(srv_frames, where, column, value):
    persons, legs, households = _parameter_frames(srv_frames)
    frame = persons if where == "persons" else households
    # row 1 is 1_2 (an adult student) and household 1
    frame.loc[1 if where == "persons" else 0, column] = value
    with pytest.raises(ValueError, match=column):
        _build((persons, legs, households))


@pytest.mark.parametrize("more_divers, raises", [([], False), ([(1, 1)], False),
                                                  ([(1, 1), (1, 2)], True)])
def test_exclusion_limit_is_exactly_half_of_the_students(srv_frames, more_divers, raises):
    """The one deliberate limit case. 3_2 has no sex code already (1 of 4); one more is exactly
    half of the 4 adult students and passes, a third is above half and raises: that many students
    without a sex code is a broken mapping, not a measurement."""
    persons, legs, households = _parameter_frames(srv_frames)
    for hhnr, pnr in more_divers:
        persons.loc[(persons["HHNR"] == hhnr) & (persons["PNR"] == pnr), "V_GESCHLECHT"] = 3
    if raises:
        with pytest.raises(ValueError, match="sex"):
            _build((persons, legs, households))
    else:
        _, diagnostics = _build((persons, legs, households))
        assert diagnostics["n_excluded_sex"] == 1 + len(more_divers)


def test_young_adult_status_by_kreis_by_hand(srv_frames):
    """Persons 20-29 with a readable V_ERW: 03101 holds the students 1_1 [2], 1_2 [1] and the
    employed 2_1 [2] and 2_3 [1] (25, V_ERW 8: apprenticeship counts as employed): 3/6 each,
    n_effective 36/10. 03102 holds the student 3_1 [1] and 4_1 [2] (29, retired: other):
    1/3 and 2/3. Region: students 4/9, employed 3/9, other 2/9. 4_3 (V_ERW -10) is excluded."""
    persons, legs, households = _parameter_frames(srv_frames)
    persons.loc[(persons["HHNR"] == 2) & (persons["PNR"] == 3), "V_ALTER"] = 25
    persons.loc[(persons["HHNR"] == 4) & (persons["PNR"] == 1), "V_ALTER"] = 29
    persons.loc[(persons["HHNR"] == 4) & (persons["PNR"] == 2), "V_ALTER"] = -9    # no age
    universe, _ = R.student_universe(persons, households)
    table, diagnostics = R.build_young_adult_status_table(universe)
    assert list(table.columns) == ["code", "level", "status", "n_unweighted", "weight_sum",
                                   "share", "n_effective", "se_share"]
    assert len(table) == 9 * 3
    cell = table.set_index(["code", "status"])
    assert cell.loc[("03101", "student"), "share"] == pytest.approx(0.5)
    assert cell.loc[("03101", "employed"), "share"] == pytest.approx(0.5)
    assert cell.loc[("03101", "other"), "share"] == 0
    assert cell.loc[("03101", "student"), "n_unweighted"] == 2
    assert cell.loc[("03101", "student"), "n_effective"] == pytest.approx(36 / 10)
    assert cell.loc[("03101", "student"), "se_share"] == pytest.approx(math.sqrt(0.25 / 3.6))
    assert cell.loc[("03102", "student"), "share"] == pytest.approx(1 / 3)
    assert cell.loc[("03102", "other"), "share"] == pytest.approx(2 / 3)
    assert cell.loc[(REGION_CODE, "student"), "share"] == pytest.approx(4 / 9)
    assert cell.loc[(REGION_CODE, "employed"), "share"] == pytest.approx(3 / 9)
    assert cell.loc[(REGION_CODE, "other"), "share"] == pytest.approx(2 / 9)
    assert cell.loc[(REGION_CODE, "student"), "weight_sum"] == pytest.approx(4.0)
    assert cell.loc[(REGION_CODE, "other"), "n_unweighted"] == 1
    for status in ("student", "employed", "other"):
        assert np.isnan(cell.loc[(WOLFSBURG, status), "share"])
        assert cell.loc[(WOLFSBURG, status), "n_unweighted"] == 0
    assert set(table["level"]) == {"kreis", "total"}
    assert diagnostics["n_excluded_unreadable_employment_code"] == 1
    assert diagnostics["n_missing_age"] == 1                 # 4_2: counted, in no age band


@pytest.mark.parametrize("age, label", [(18, "18_19"), (19, "18_19"), (20, "20_24"),
                                        (24, "20_24"), (25, "25_29"), (29, "25_29"),
                                        (30, "30plus")])
def test_age_group_edges_are_right_inclusive(srv_frames, age, label):
    """1_2 [1] takes the age under test; 1_1 (22) and 3_1 (21) stay 20_24 [2 + 1], 3_2 (35)
    30plus [1]."""
    persons, legs, households = _parameter_frames(srv_frames)
    persons.loc[(persons["HHNR"] == 1) & (persons["PNR"] == 2), "V_ALTER"] = age
    table, _ = _build((persons, legs, households))
    expected = {"18_19": 0.0, "20_24": 3.0, "25_29": 0.0, "30plus": 1.0}
    expected[label] += 1.0
    for name, weight in expected.items():
        assert _parameter_cell(table, "age_group", name)["share"] == pytest.approx(weight / 5)


# ------------------------------------------------- Phase 1: education destination, post-MATSim
SITES = frozenset({"03101", "03158"})
NAN = float("nan")


def _destination_frames(students):
    """One-person households; ``students`` rows are ``(ags, weight, v_erw, legs)`` with legs
    ``(WNR, E_ZWECK_9, start hour, start minute, V_ZIEL_AGS)``. Returns ``(universe, legs)``."""
    n = len(students)
    households = pd.DataFrame({"HHNR": range(1, n + 1), "AGS": [s[0] for s in students]})
    persons = pd.DataFrame({
        "HHNR": range(1, n + 1), "PNR": 1, "V_ALTER": 22, "V_ERW": [s[2] for s in students],
        "E_ANZ_WEGE": 2, "GEWICHT_P_ZENSUS": [float(s[1]) for s in students],
        "MITTL_WERKTAG": 1, "E_OEV_FK": -8, "V_PKW_VERFUEG": 1, "V_FUEHR_PKW": 1,
        "V_FK_VERFUEG": 1})
    rows = [(hh, 1, *leg) for hh, student in enumerate(students, start=1) for leg in student[3]]
    legs = pd.DataFrame(rows, columns=["HHNR", "PNR", "WNR", "E_ZWECK_9", "V_BEGINN_STUNDE",
                                       "V_BEGINN_MINUTE", "V_ZIEL_AGS"])
    universe, _ = R.student_universe(persons, households)
    return universe, legs


def _edu(ziel, wnr=1, hour=8, minute=0):
    return (wnr, 4, hour, minute, ziel)


def _mixed_destination_frames():
    """Braunschweig (03101): b1 [2] own, b2 [1] to 03158 (a site), b3 [5] only a work leg (no
    education leg, outside the table), an employee [10] with an education leg (no student).
    Wolfenbuettel (03158, a site): w1 [3] has two education legs, WNR 1 at 09:45 to 03101 and
    WNR 2 at 09:15 to 03158: the FIRST by time is WNR 2 (own Kreis), by leg number it would be
    03101. Peine (03157, no site): p1 [1] to 03101."""
    return _destination_frames([
        (3101000, 2, 7, [_edu(3101000)]),
        (3101000, 1, 7, [_edu(3158000)]),
        (3101000, 5, 7, [(1, 1, 8, 0, 3101000)]),
        (3101000, 10, 9, [_edu(3101000)]),
        (3158000, 3, 7, [_edu(3101000, wnr=1, hour=9, minute=45),
                         _edu(3158000, wnr=2, hour=9, minute=15)]),
        (3157000, 1, 7, [_edu(3101000)]),
    ])


def test_destination_ags_is_padded_and_unknown_codes_are_excluded():
    """3101000 lost its leading zero: unpadded it reads 31010 (an 'other outside' Kreis). The
    codes -1 and NaN are unknown, excluded and counted (2 of 6 persons), never 'outside'."""
    universe, legs = _destination_frames([
        (3101000, 1, 7, [_edu(3101000)]), (3101000, 2, 7, [_edu(3158037)]),
        (3101000, 1, 7, [_edu(-1)]), (3101000, 1, 7, [_edu(NAN)]),
        (3101000, 3, 7, [_edu(3241001)]), (3101000, 1, 7, [_edu(9162000)])])
    table, diag = R.build_destination_reference(universe, legs, SITES, 50, 0)
    bs = table[table.home_group == R.HOME_GROUP_BRAUNSCHWEIG].set_index("destination_group")
    assert set(bs.index) >= {R.DEST_OWN, "site_kreis_03158", R.DEST_REGION_HANNOVER,
                             R.DEST_OTHER_OUTSIDE}
    assert diag["n_excluded_unknown_destination"] == 2
    assert bs["share"].sum() == pytest.approx(1.0)
    # weights: own 1, site 2, Hannover 3, outside 1 -> base 7
    assert bs.loc[R.DEST_OWN, "share"] == pytest.approx(1 / 7)
    assert bs.loc["site_kreis_03158", "share"] == pytest.approx(2 / 7)
    assert bs.loc[R.DEST_REGION_HANNOVER, "share"] == pytest.approx(3 / 7)
    assert bs.loc[R.DEST_OTHER_OUTSIDE, "share"] == pytest.approx(1 / 7)
    assert bs.loc[R.DEST_OTHER_OUTSIDE, "n_unweighted"] == 1
    assert bs.loc[R.DEST_LK_HILDESHEIM, "n_unweighted"] == 0
    # the other home groups hold nobody: every cell is emitted, empty, with a NaN share
    empty = table[table.home_group == R.HOME_GROUP_NO_SITE]
    assert len(empty) > 0 and (empty["n_unweighted"] == 0).all()
    assert empty["share"].isna().all() and empty["noise_floor_tvd_95"].isna().all()


def test_home_groups_and_first_education_leg():
    universe, legs = _mixed_destination_frames()
    table, diag = R.build_destination_reference(universe, legs, SITES, 50, 0)
    cell = table.set_index(["home_group", "destination_group"])
    # Braunschweig: b1 [2] own, b2 [1] site; b3 has no education leg, the employee is no student
    assert cell.loc[(R.HOME_GROUP_BRAUNSCHWEIG, R.DEST_OWN), "share"] == pytest.approx(2 / 3)
    assert cell.loc[(R.HOME_GROUP_BRAUNSCHWEIG, "site_kreis_03158"),
                    "share"] == pytest.approx(1 / 3)
    assert cell.loc[(R.HOME_GROUP_BRAUNSCHWEIG, R.DEST_OWN), "n_persons_group"] == 2
    # Wolfenbuettel counts by its FIRST leg (09:15, WNR 2): own Kreis, not 03101
    assert cell.loc[(R.HOME_GROUP_SITE, R.DEST_OWN), "share"] == pytest.approx(1.0)
    assert cell.loc[(R.HOME_GROUP_SITE, "site_kreis_03101"), "n_unweighted"] == 0
    # Peine has no site: its own group
    assert cell.loc[(R.HOME_GROUP_NO_SITE, "site_kreis_03101"), "share"] == pytest.approx(1.0)
    # region: own [2 + 3] of 7, site 03158 [1], site 03101 [1]
    assert cell.loc[(R.HOME_GROUP_ALL, R.DEST_OWN), "share"] == pytest.approx(5 / 7)
    assert cell.loc[(R.HOME_GROUP_ALL, "site_kreis_03158"), "share"] == pytest.approx(1 / 7)
    assert cell.loc[(R.HOME_GROUP_ALL, "site_kreis_03101"), "share"] == pytest.approx(1 / 7)
    assert diag["n_adult_students_without_education_leg"] == 1
    assert diag["n_persons_ordered_by_leg_number"] == 0


def test_negative_start_time_orders_by_leg_number_and_is_counted():
    """A negative start minute of one education leg makes the time order undeterminable: that
    person is ordered by WNR (first = WNR 1 to 03158) and counted, not dropped. Ordered by
    time it would be WNR 2 (03101)."""
    universe, legs = _destination_frames([
        (3101000, 1, 7, [_edu(3158000, wnr=1, hour=10, minute=-1),
                         _edu(3101000, wnr=2, hour=9, minute=0)]),
        (3101000, 1, 7, [_edu(3101000)])])
    table, diag = R.build_destination_reference(universe, legs, SITES, 50, 0)
    bs = table[table.home_group == R.HOME_GROUP_BRAUNSCHWEIG].set_index("destination_group")
    assert diag["n_persons_ordered_by_leg_number"] == 1
    assert bs.loc["site_kreis_03158", "n_unweighted"] == 1
    assert bs.loc[R.DEST_OWN, "n_unweighted"] == 1


@pytest.mark.parametrize("sites", [frozenset({"03158"}), frozenset({"03101", "09162"}),
                                   frozenset()])
def test_site_kreise_must_contain_braunschweig_and_lie_in_the_region(sites):
    with pytest.raises(ValueError, match="site_kreise"):
        R.build_destination_reference(pd.DataFrame(), pd.DataFrame(), sites, 50, 0)


def test_destination_noise_floor_is_seeded():
    universe, legs = _mixed_destination_frames()
    first, _ = R.build_destination_reference(universe, legs, SITES, 200, 7)
    second, _ = R.build_destination_reference(universe, legs, SITES, 200, 7)
    pd.testing.assert_frame_equal(first, second)
    floor = first.groupby("home_group")["noise_floor_tvd_95"].first()
    assert (floor >= 0).all()
    assert floor[R.HOME_GROUP_BRAUNSCHWEIG] > 0  # two persons with different destinations
    assert floor[R.HOME_GROUP_SITE] == 0.0       # a single person cannot differ from itself
    for n_bootstrap in (0, 1):
        with pytest.raises(ValueError, match="n_bootstrap"):
            R.build_destination_reference(universe, legs, SITES, n_bootstrap, 7)


def test_destination_exclusion_above_the_limit_raises():
    """3 of 4 persons have an unknown destination (75 %, away from the 0.5 limit)."""
    universe, legs = _destination_frames([
        (3101000, 1, 7, [_edu(-1)]), (3101000, 1, 7, [_edu(-1)]),
        (3101000, 1, 7, [_edu(NAN)]), (3101000, 1, 7, [_edu(3101000)])])
    with pytest.raises(ValueError, match="unknown destination"):
        R.build_destination_reference(universe, legs, SITES, 10, 0)


def test_bootstrap_standard_error_resamples_persons_not_legs():
    """Person A has two legs of value 1, person B one leg of value 0, equal weights. Resampled
    persons: AA -> 1 (p 1/4), BB -> 0 (1/4), AB -> 2/3 (1/2): mean 7/12, variance 19/144, so
    se = sqrt(19) / 12. A leg-level bootstrap of the same three legs would give another value."""
    se = R.bootstrap_standard_error([[1.0, 1.0], [0.0]], [1.0, 1.0], 4000, 3)
    assert se == pytest.approx(math.sqrt(19) / 12, abs=0.03)
    assert R.bootstrap_standard_error([[1.0, 1.0], [0.0]], [1.0, 1.0], 4000, 3) == se
    assert R.bootstrap_standard_error([[2.0], [2.0], [2.0]], [1.0, 5.0, 2.0], 50, 3) == 0.0
    assert math.isnan(R.bootstrap_standard_error([[1.0]], [1.0], 50, 3))
    for n_bootstrap in (0, 1):
        with pytest.raises(ValueError, match="n_bootstrap"):
            R.bootstrap_standard_error([[1.0], [2.0]], [1.0, 1.0], n_bootstrap, 3)


def _post_matsim_frames(srv_frames):
    """The standard frames plus E_HVM_5 per leg and the two daily totals per person (all
    adult students: 1_1 [2], 1_2 [1], 3_1 [1], 3_2 [1])."""
    persons, legs, households = srv_frames
    mode = {(1, 1, 3): 5, (1, 1, 1): 2, (1, 1, 2): 2, (1, 2, 1): 3, (1, 2, 2): 3,
            (3, 1, 1): -7, (3, 1, 2): 1}
    legs["E_HVM_5"] = [mode.get(key, 4) for key in zip(legs["HHNR"], legs["PNR"], legs["WNR"])]
    persons["E_VERKEHRSLEIST"] = [27.0, -7, 5, 5, 5, 6.0, 0.0, 5, 5, 5, 5]
    persons["E_DAUER_GESAMT"] = [90, 40, 5, 5, 5, -7, 0, 5, 5, 5, 5]
    return persons, legs, households


def _quantity(table, quantity, label):
    row = table[(table["quantity"] == quantity) & (table["label"] == label)]
    assert len(row) == 1
    return row.iloc[0]


def test_post_matsim_reference_by_hand(srv_frames):
    """Adult students' legs (weight = the person's): 1_1 [2] pt, bicycle, bicycle (the two
    education legs); 1_2 [1] car driver x2 (work, home); 3_1 [1] -7 (education, excluded) and
    walk (home); 3_2 no leg. Valid legs: pt 2, bicycle 2 x 2 = 4, car driver 1 + 1 = 2, walk 1:
    total 9. Education legs: bicycle 2 x 2 = 4 only (the -7 one excluded). Distance:
    1_1 27 [2], 1_2 -7 excluded, 3_1 6, 3_2 0 -> (54 + 6 + 0) / 4 = 15. Time: 90 [2], 40,
    3_1 -7 excluded, 0 -> (180 + 40 + 0) / 4 = 55."""
    persons, legs, households = _post_matsim_frames(srv_frames)
    universe, _ = R.student_universe(persons, households)
    table, diag = R.build_post_matsim_reference(universe, persons, legs, 200, 5)
    assert list(table.columns) == ["quantity", "label", "n_unweighted", "weight_sum", "value",
                                   "se_value"]
    for label, weight, n in (("walk", 1, 1), ("bicycle", 4, 2), ("car_driver", 2, 2),
                             ("car_passenger", 0, 0), ("pt", 2, 1)):
        row = _quantity(table, "main_mode_all_legs", label)
        assert row["value"] == pytest.approx(weight / 9) and row["n_unweighted"] == n
        assert row["weight_sum"] == pytest.approx(weight)
    assert _quantity(table, "main_mode_education_legs", "bicycle")["value"] == pytest.approx(1.0)
    # one person (1_1) holds all valid education legs: no person bootstrap is defined
    assert math.isnan(_quantity(table, "main_mode_education_legs", "bicycle")["se_value"])
    assert _quantity(table, "main_mode_education_legs", "pt")["value"] == pytest.approx(0.0)
    assert diag["n_excluded_mode_all_legs"] == 1 and diag["n_legs_all"] == 7
    assert diag["n_excluded_mode_education_legs"] == 1 and diag["n_legs_education"] == 3
    distance = _quantity(table, "daily_distance_km_mean", "mean")
    assert distance["value"] == pytest.approx(15.0)
    assert (distance["n_unweighted"], distance["weight_sum"]) == (3, 4.0)
    duration = _quantity(table, "daily_travel_time_min_mean", "mean")
    assert duration["value"] == pytest.approx(55.0)
    assert (duration["n_unweighted"], duration["weight_sum"]) == (3, 4.0)
    assert diag["n_not_computable_daily_distance"] == 1
    assert diag["n_not_computable_daily_travel_time"] == 1
    # bicycle share of all legs, hand-enumerated person bootstrap of the three persons with a leg
    # (1_1 [2]: 0 1 1 of 3 legs, 1_2 [1]: 0 0, 3_1 [1]: 0): the statistic 4a / (6a + 2b + c) over
    # the 27 equally likely draws (a, b, c = draws of the three persons) has standard deviation
    # 0.24086
    big, _ = R.build_post_matsim_reference(universe, persons, legs, 4000, 5)
    assert _quantity(big, "main_mode_all_legs", "bicycle")["se_value"] == pytest.approx(
        0.24086, abs=0.02)
    assert "valid trips" in diag["daily_total_sub_universe"]
    assert distance["se_value"] > 0
    again, _ = R.build_post_matsim_reference(universe, persons, legs, 200, 5)
    pd.testing.assert_frame_equal(table, again)


def test_post_matsim_undocumented_mode_code_raises(srv_frames):
    persons, legs, households = _post_matsim_frames(srv_frames)
    legs.loc[0, "E_HVM_5"] = 9
    universe, _ = R.student_universe(persons, households)
    with pytest.raises(ValueError, match="E_HVM_5"):
        R.build_post_matsim_reference(universe, persons, legs, 20, 5)


def test_post_matsim_needs_two_bootstrap_draws(srv_frames):
    persons, legs, households = _post_matsim_frames(srv_frames)
    universe, _ = R.student_universe(persons, households)
    with pytest.raises(ValueError, match="n_bootstrap"):
        R.build_post_matsim_reference(universe, persons, legs, 1, 5)


def test_not_computable_daily_totals_are_the_sub_universe_and_warn_above_the_limit(
        srv_frames, caplog):
    """3 of the 4 adult students carry -7 (75 %): the codebook sub-universe, so it builds with
    a WARNING (no raise) and the base is the one student left (1_1, 27 km)."""
    persons, legs, households = _post_matsim_frames(srv_frames)
    persons["E_VERKEHRSLEIST"] = [27.0, -7, 5, 5, 5, -7, -7, 5, 5, 5, 5]
    universe, _ = R.student_universe(persons, households)
    with caplog.at_level("WARNING", logger=R.logger.name):
        table, diag = R.build_post_matsim_reference(universe, persons, legs, 20, 5)
    assert diag["n_not_computable_daily_distance"] == 3 and diag["n_base_daily_distance"] == 1
    distance = _quantity(table, "daily_distance_km_mean", "mean")
    assert distance["value"] == pytest.approx(27.0) and distance["n_unweighted"] == 1
    assert math.isnan(distance["se_value"])
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert any("daily_distance_km_mean" in message and "75.0%" in message
               for message in warnings)


@pytest.mark.parametrize("column", ["E_VERKEHRSLEIST", "E_DAUER_GESAMT"])
def test_undocumented_negative_daily_total_raises(srv_frames, column):
    persons, legs, households = _post_matsim_frames(srv_frames)
    persons.loc[0, column] = -10
    universe, _ = R.student_universe(persons, households)
    with pytest.raises(ValueError, match=column):
        R.build_post_matsim_reference(universe, persons, legs, 20, 5)


def test_every_destination_group_is_reachable_and_pooled_by_hand():
    """Sites 03101, 03154, 03158. Braunschweig b1 [1] Landkreis Hildesheim (03254), b2 [2]
    Peine (03157, a region Kreis without a site: other_region_kreis), b3 [1] 03154. Wolfenbuettel
    (a site) w1 [3] to the other site 03154, w2 [1] to 03101. Peine (no site) p1 [2] to its own
    03157. Braunschweig base 4; site group base 4; no-site base 2; region base 10."""
    sites = frozenset({"03101", "03154", "03158"})
    universe, legs = _destination_frames([
        (3101000, 1, 7, [_edu(3254001)]), (3101000, 2, 7, [_edu(3157000)]),
        (3101000, 1, 7, [_edu(3154000)]), (3158000, 3, 7, [_edu(3154000)]),
        (3158000, 1, 7, [_edu(3101000)]), (3157000, 2, 7, [_edu(3157000)])])
    table, _ = R.build_destination_reference(universe, legs, sites, 200, 4)
    cell = table.set_index(["home_group", "destination_group"])["share"]
    assert cell[(R.HOME_GROUP_BRAUNSCHWEIG, R.DEST_LK_HILDESHEIM)] == pytest.approx(1 / 4)
    assert cell[(R.HOME_GROUP_BRAUNSCHWEIG, R.DEST_OTHER_REGION)] == pytest.approx(2 / 4)
    assert cell[(R.HOME_GROUP_BRAUNSCHWEIG, "site_kreis_03154")] == pytest.approx(1 / 4)
    assert (R.HOME_GROUP_BRAUNSCHWEIG, "site_kreis_03101") not in cell.index
    assert cell[(R.HOME_GROUP_SITE, "site_kreis_03154")] == pytest.approx(3 / 4)
    assert cell[(R.HOME_GROUP_SITE, "site_kreis_03101")] == pytest.approx(1 / 4)
    assert cell[(R.HOME_GROUP_NO_SITE, R.DEST_OWN)] == pytest.approx(1.0)
    expected_region = {R.DEST_LK_HILDESHEIM: 0.1, R.DEST_OTHER_REGION: 0.2,
                       "site_kreis_03154": 0.4, "site_kreis_03101": 0.1, R.DEST_OWN: 0.2}
    for label, share in expected_region.items():
        assert cell[(R.HOME_GROUP_ALL, label)] == pytest.approx(share)
    assert cell[R.HOME_GROUP_ALL].sum() == pytest.approx(1.0)
    # The region noise floor is the seeded person bootstrap over ALL six persons (b1, b2, b3,
    # w1, w2, p1 with their weights), not over one home group.
    vocabulary = [R.DEST_OWN, "site_kreis_03101", "site_kreis_03154", "site_kreis_03158",
                  R.DEST_OTHER_REGION, R.DEST_REGION_HANNOVER, R.DEST_LK_HILDESHEIM,
                  R.DEST_OTHER_OUTSIDE]
    expected_floor = SP.bootstrap_share_noise_floor(
        [R.DEST_LK_HILDESHEIM, R.DEST_OTHER_REGION, "site_kreis_03154", "site_kreis_03154",
         "site_kreis_03101", R.DEST_OWN], [1.0, 2.0, 1.0, 3.0, 1.0, 2.0], vocabulary, 200, 4)
    floors = table.groupby("home_group")["noise_floor_tvd_95"].first()
    assert floors[R.HOME_GROUP_ALL] > 0
    assert floors[R.HOME_GROUP_ALL] == pytest.approx(expected_floor)


def test_home_group_with_zero_weight_sum_has_nan_floor_and_is_counted():
    universe, legs = _destination_frames([
        (3101000, 0, 7, [_edu(3101000)]), (3101000, 0, 7, [_edu(3158000)]),
        (3157000, 2, 7, [_edu(3157000)])])
    table, diag = R.build_destination_reference(universe, legs, SITES, 50, 0)
    bs = table[table.home_group == R.HOME_GROUP_BRAUNSCHWEIG]
    assert bs["noise_floor_tvd_95"].isna().all() and bs["share"].isna().all()
    assert (bs["n_persons_group"] == 2).all()
    assert diag["n_home_groups_zero_weight"] == 1
    assert table[table.home_group == R.HOME_GROUP_NO_SITE]["noise_floor_tvd_95"].notna().all()


def test_fallback_rate_above_the_limit_raises_with_a_fallback_message():
    """3 of 4 persons have an unreadable start minute (75 %): the WNR order serves most of the
    base, which means the primary column is broken."""
    universe, legs = _destination_frames([
        (3101000, 1, 7, [_edu(3101000, minute=-1)]), (3101000, 1, 7, [_edu(3101000, minute=-1)]),
        (3101000, 1, 7, [_edu(3101000, minute=-1)]), (3101000, 1, 7, [_edu(3101000)])])
    with pytest.raises(ValueError, match="fallback served 3 of 4"):
        R.build_destination_reference(universe, legs, SITES, 10, 0)
