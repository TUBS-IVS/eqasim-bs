"""Unit tests for the MiD 2023 university-student aggregates (issue #140, Phase 0, table R4).

Synthetic MiD-shaped frames only -- the raw scientific-use microdata is restricted and must
never be read by a test. Every expected value is computed by hand from the table in the
docstring of :func:`_raw`; the weights are chosen so that a weighted share, an unweighted share
and a share over the wrong universe all differ.
"""
import importlib.util
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration import mid_student_reference as M
from braunschweig.calibration.student_behaviour_labels import (
    CONTROLS, NOT_STUDENT, STUDENT_DISTANCE_LABELS, UNIVERSE_ADULTS, UNIVERSE_STUDENTS)

REPO = Path(__file__).resolve().parents[1]


def _raw():
    """Thirteen persons and their legs; ``wd`` is the reporting weekday (2-4 = Tue-Thu).

    ====  ====  ===  ====  ======  ======  =====  =====================================
    key   TAET  age  wd    weight  anz     FKARTE legs (W_ZWECK; start; arrival h; km; hvm_imp)
    ====  ====  ===  ====  ======  ======  =====  =====================================
    a     10    22   2     1       4       3      edu 12:30 ->13 3 km walk (listed first),
                                                  edu 09:00 ->9 12 km pt, work rbW leg
    b     10    24   3     2       2       1      work direct leg
    c     10    21   4     1       2       99     edu 07:00 ->7 1500 km car (invalid length)
    d     10    26   2     1       803     5      edu 14:00 ->99 (arrival filter) 5 km bike
    e     10    17   2     5       2       1      edu (a student under 18)
    f     10    23   1     4       2       1      edu (a Monday)
    g     1     40   3     2       2       8      none (employed)
    h     9     16   2     1       2       8      edu (pupil)
    i     8     17   2     1       2       8      edu (apprentice)
    j     17    45   2     1       2       8      edu W_ZWECK 11 (a course)
    l     12    60   4     2       0       8      none
    m     99    30   2     100     2       8      edu (P_TAET no answer: excluded, counted)
    n     10    28   2     0       2       1      (weight 0: excluded, counted)
    ====  ====  ===  ====  ======  ======  =====  =====================================

    Behaviour universe (core weekday, adult, readable P_TAET, valid weight): a, b, c, d (the
    students, weights 1, 2, 1, 1) and g, j, l (non-students, weights 2, 1, 2): 10 in total.
    """
    persons = pd.DataFrame({
        "H_ID": [1, 1, 2, 2, 3, 3, 3, 4, 4, 4, 5, 5, 5],
        "P_ID": [1, 2, 1, 2, 1, 2, 3, 1, 2, 3, 1, 2, 3],
        "HP_ID": list(range(1, 14)),
        "P_TAET": [10, 10, 10, 10, 10, 10, 1, 9, 8, 17, 12, 99, 10],
        "HP_ALTER": [22, 24, 21, 26, 17, 23, 40, 16, 17, 45, 60, 30, 28],
        "ST_WOTAG": [2, 3, 4, 2, 2, 1, 3, 2, 2, 2, 4, 2, 2],
        "P_GEW": [1.0, 2.0, 1.0, 1.0, 5.0, 4.0, 2.0, 1.0, 1.0, 1.0, 2.0, 100.0, 0.0],
        "anzwege1": [4, 2, 2, 803, 2, 2, 2, 2, 2, 2, 0, 2, 2],
        "P_FKARTE": [3, 1, 99, 5, 1, 1, 8, 8, 8, 8, 8, 8, 1],
    })
    rows = [
        # H_ID, P_ID, W_ZWECK, W_SZS, W_SZM, W_AZS, wegkm_imp, hvm_imp, W_RBW
        (1, 1, 3, 12, 30, 13, 3.0, 1, 0),
        (1, 1, 3, 9, 0, 9, 12.0, 5, 0),
        (1, 1, 1, 7, 0, 8, 20.0, 4, 1),
        (1, 2, 1, 8, 0, 8, 20.0, 4, 0),
        (2, 1, 3, 7, 0, 7, 1500.0, 4, 0),
        (2, 2, 3, 14, 0, 99, 5.0, 2, 0),
        (3, 1, 3, 8, 0, 8, 3.0, 1, 0),
        (3, 2, 3, 8, 0, 8, 3.0, 1, 0),
        (4, 1, 3, 8, 0, 8, 2.0, 1, 0),
        (4, 2, 3, 8, 0, 8, 2.0, 1, 0),
        (4, 3, 11, 18, 0, 19, 4.0, 1, 0),
        (5, 2, 3, 8, 0, 8, 1.0, 1, 0),
    ]
    trips = pd.DataFrame(rows, columns=["H_ID", "P_ID", "W_ZWECK", "W_SZS", "W_SZM", "W_AZS",
                                        "wegkm_imp", "hvm_imp", "W_RBW"])
    return persons, trips


@pytest.fixture
def mid_frames():
    return _raw()


def _cell(table, control, universe_name, label):
    row = table[(table["control"] == control) & (table["universe"] == universe_name)
                & (table["label"] == label)]
    assert len(row) == 1
    return row.iloc[0]


def test_behaviour_seed_restricts_to_core_weekdays_and_adults(mid_frames):
    persons, trips = mid_frames
    table, diagnostics = M.build_behaviour_seed_table(persons, trips)
    # The Monday student f (weight 4), the 17-year-old student e (weight 5) and the unreadable
    # P_TAET m (weight 100) are in the frame and must not count: 3 / 10, not a number they move.
    adults = table[(table["control"] == "status_x_education_leg")
                   & (table["universe"] == UNIVERSE_ADULTS)].set_index("label")
    assert adults.loc["student_edu", "share"] == pytest.approx(3 / 10)
    assert adults.loc["student_noedu", "share"] == pytest.approx(2 / 10)
    assert adults.loc[NOT_STUDENT, "share"] == pytest.approx(5 / 10)
    assert adults.loc["student_edu", "n_unweighted"] == 3
    # the same vocabulary as the SrV side, nothing else
    assert set(table["label"]) <= {label for labels in CONTROLS.values() for label in labels} \
        | {NOT_STUDENT}
    assert diagnostics["n_excluded_unreadable_p_taet"] == 1
    assert diagnostics["n_excluded_invalid_weight"] == 1


def test_behaviour_seed_controls_by_hand(mid_frames):
    persons, trips = mid_frames
    table, diagnostics = M.build_behaviour_seed_table(persons, trips)
    # Every control partitions each universe.
    for (control, universe_name), group in table.groupby(["control", "universe"]):
        assert group["share"].sum() == pytest.approx(1.0)
        expected = set(CONTROLS[control]) | ({NOT_STUDENT} if universe_name == UNIVERSE_ADULTS
                                             else set())
        assert set(group["label"]) == expected
    # trip class: d (803, a filter code) leaves the control; a is 3-4, b and c are 1-2
    assert _cell(table, "trip_class", UNIVERSE_STUDENTS, "student_trips_1_2")["share"] \
        == pytest.approx(3 / 4)
    assert _cell(table, "trip_class", UNIVERSE_ADULTS, "student_trips_3_4")["share"] \
        == pytest.approx(1 / 9)
    assert diagnostics["n_students_unreadable_trip_count"] == 1
    # arrival: a's FIRST education leg is the 09:00 one although the 12:30 leg is listed first
    assert _cell(table, "arrival", UNIVERSE_STUDENTS, "student_arrive_9_11")["share"] \
        == pytest.approx(1 / 4)
    assert _cell(table, "arrival", UNIVERSE_STUDENTS, "student_arrive_12_later")["share"] == 0.0
    assert _cell(table, "arrival", UNIVERSE_STUDENTS, "student_arrive_before_9")["share"] \
        == pytest.approx(1 / 4)                                   # c
    assert _cell(table, "arrival", UNIVERSE_STUDENTS, "student_no_education_leg")["share"] \
        == pytest.approx(2 / 4)                                   # b, weight 2
    assert diagnostics["n_students_unreadable_arrival_hour"] == 1   # d, arrival code 99
    # season ticket: weights a 1 flat, b 2 no flat, d 1 flat; c has no readable code
    assert _cell(table, "season_ticket", UNIVERSE_STUDENTS, "student_flatrate")["share"] \
        == pytest.approx(2 / 4)                                   # unweighted would be 2 / 3
    # Kish size over exactly the rows of the share's universe: weights 1, 2, 1, 1
    assert _cell(table, "work_leg", UNIVERSE_STUDENTS, "student_work")["n_effective"] \
        == pytest.approx(25 / 7)


def test_rbw_legs_do_not_count(mid_frames):
    persons, trips = mid_frames
    production, diagnostics = M.build_behaviour_seed_table(persons, trips, exclude_rbw_legs=True)
    assert diagnostics["n_work_legs_dropped_rbw"] == 1       # counted, not silent
    # a's only work leg has W_RBW == 1: only b (weight 2 of 5) has a work leg the plan contains
    assert _cell(production, "work_leg", UNIVERSE_STUDENTS, "student_work")["share"] \
        == pytest.approx(2 / 5)
    everything, _ = M.build_behaviour_seed_table(persons, trips, exclude_rbw_legs=False)
    assert _cell(everything, "work_leg", UNIVERSE_STUDENTS, "student_work")["share"] \
        == pytest.approx(3 / 5)


def test_missing_ticket_code_is_excluded_and_counted(mid_frames):
    persons, trips = mid_frames
    table, diagnostics = M.build_behaviour_seed_table(persons, trips)
    # c answered P_FKARTE 99 (no answer): absent from the season_ticket shares, never "no flatrate"
    students = table[(table["universe"] == UNIVERSE_STUDENTS)]
    assert students[students["control"] == "season_ticket"]["n_unweighted"].sum() == 3
    assert students[students["control"] == "work_leg"]["n_unweighted"].sum() == 4
    assert _cell(table, "season_ticket", UNIVERSE_STUDENTS, "student_no_flatrate")["share"] \
        == pytest.approx(2 / 4)
    assert diagnostics["n_students_unreadable_season_ticket"] == 1


def test_status_by_age_by_hand(mid_frames):
    persons, trips = mid_frames
    table, diagnostics = M.build_status_by_age_table(persons, trips)
    assert len(table) == 12                                   # 3 age bands x 4 statuses, all emitted
    by_cell = table.set_index(["age_band", "status"])
    # under 20: student e (weight 5), pupil h, apprentice i (weight 1 each); the Monday student f
    # and the unreadable m are not in the base
    assert by_cell.loc[("lt20", "student"), "share"] == pytest.approx(5 / 7)
    assert by_cell.loc[("lt20", "pupil"), "share"] == pytest.approx(1 / 7)
    assert by_cell.loc[("lt20", "apprentice"), "share"] == pytest.approx(1 / 7)
    assert by_cell.loc[("lt20", "other"), "share"] == 0.0
    assert by_cell.loc[("lt20", "student"), "n_unweighted"] == 1
    assert by_cell.loc[("20_29", "student"), "share"] == pytest.approx(1.0)   # a, c, d
    assert by_cell.loc[("20_29", "student"), "n_unweighted"] == 3
    assert by_cell.loc[("30plus", "other"), "share"] == pytest.approx(1.0)    # j
    assert diagnostics["n_excluded_unreadable_p_taet"] == 1


def test_distance_band_uses_the_first_education_trip_and_drops_invalid_lengths(mid_frames):
    persons, trips = mid_frames
    table, diagnostics = M.build_distance_mode_table(persons, trips)
    assert len(table) == len(STUDENT_DISTANCE_LABELS) * len(M.MODE_BY_HVM)
    by_cell = table.set_index(["distance_band", "mode"])
    # a: first leg 09:00, 12 km public transport (not the 12:30 walk of 3 km); d: exactly 5.0 km
    # is in 5_10 (left-inclusive), by bike; c's 1500 km is a filter value: counted, never banded;
    # e is under 18 and f is a Monday
    assert by_cell.loc[("10_25", "pt"), "n_unweighted"] == 1
    assert by_cell.loc[("5_10", "bike"), "n_unweighted"] == 1
    assert by_cell.loc[("10_25", "pt"), "share_of_all"] == pytest.approx(1 / 2)
    assert by_cell.loc[("10_25", "pt"), "share_of_band"] == pytest.approx(1.0)
    assert by_cell.loc[("10_25", "car"), "share_of_band"] == 0.0
    assert np.isnan(by_cell.loc[("0_5", "walk"), "share_of_band"])           # empty band
    assert by_cell["n_unweighted"].sum() == 2
    assert diagnostics["n_students_invalid_education_length"] == 1
    assert diagnostics["n_students_not_adult"] == 1

    # a length of exactly zero is as invalid as one at or above MID_TRIP_LENGTH_MAX_KM
    trips = trips.copy()
    trips.loc[(trips["H_ID"] == 2) & (trips["P_ID"] == 1), "wegkm_imp"] = 0.0
    table, diagnostics = M.build_distance_mode_table(persons, trips)
    assert table["n_unweighted"].sum() == 2
    assert diagnostics["n_students_invalid_education_length"] == 1

    # a length column that is invalid for most students is a broken mapping: raise, not a table
    trips.loc[(trips["H_ID"] == 2) & (trips["P_ID"] == 2), "wegkm_imp"] = 0.0
    with pytest.raises(ValueError, match="excluded"):
        M.build_distance_mode_table(persons, trips)


def test_share_20_29_per_land_and_germany():
    """Land 3 (household 1): aged 20-29 are a student [2], a pupil [1] and an employed [1]; a 35
    year old student is outside the band. Land 6 (household 2): a student [3] and an apprentice
    [1]; a 30 year old pupil is outside, and a 24 year old with P_TAET 99 is excluded. Germany is
    all of them: students 5 of 8, pupils 1 of 8."""
    persons = pd.DataFrame({
        "H_ID": [1, 1, 1, 1, 2, 2, 2, 2],
        "P_ID": [1, 2, 3, 4, 1, 2, 3, 4],
        "P_TAET": [10, 9, 1, 10, 10, 8, 9, 99],
        "HP_ALTER": [21, 25, 28, 35, 20, 29, 30, 24],
        "ST_WOTAG": [1, 6, 7, 2, 3, 4, 5, 2],              # every weekday counts here
        "P_GEW": [2.0, 1.0, 1.0, 9.0, 3.0, 1.0, 5.0, 2.0],
    })
    households = pd.DataFrame({"H_ID": [1, 2], "BLAND": [3, 6]})
    table, diagnostics = M.build_share_20_29_table(persons, households)
    by_region = table.set_index("region")
    assert by_region.loc["03", "share_students"] == pytest.approx(2 / 4)
    assert by_region.loc["03", "share_pupils"] == pytest.approx(1 / 4)
    assert by_region.loc["03", "share_pupils_and_students"] == pytest.approx(3 / 4)
    assert by_region.loc["06", "share_students"] == pytest.approx(3 / 4)
    assert by_region.loc["06", "share_pupils"] == pytest.approx(0.0)
    assert by_region.loc["06", "n_unweighted"] == 2
    assert by_region.loc[M.REGION_GERMANY, "share_students"] == pytest.approx(5 / 8)
    assert by_region.loc[M.REGION_GERMANY, "share_pupils"] == pytest.approx(1 / 8)
    assert by_region.loc[M.REGION_GERMANY, "share_pupils_and_students"] == pytest.approx(6 / 8)
    assert by_region.loc[M.REGION_GERMANY, "n_unweighted"] == 5
    # empty Laender are emitted with NaN shares, never dropped or 0.0
    assert len(table) == 17
    assert by_region.loc["01", "n_unweighted"] == 0
    assert np.isnan(by_region.loc["01", "share_students"])
    assert diagnostics["n_excluded_unreadable_p_taet"] == 1


def test_land_must_come_from_a_column_that_exists_and_is_valid():
    persons = pd.DataFrame({"H_ID": [1], "P_ID": [1], "P_TAET": [10], "HP_ALTER": [22],
                            "P_GEW": [1.0]})
    with pytest.raises(ValueError, match="BLAND"):
        M.build_share_20_29_table(persons, pd.DataFrame({"H_ID": [1]}))
    with pytest.raises(ValueError, match="BLAND"):
        M.build_share_20_29_table(persons.assign(BLAND=[17]), None)   # outside the 16 Laender


def _load_script():
    path = REPO / "scripts" / "extract_mid_student_reference.py"
    spec = importlib.util.spec_from_file_location("extract_mid_student_reference", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_default_rbw_setting_matches_the_production_config():
    assert M.EXCLUDE_RBW_LEGS_DEFAULT == _load_script().production_exclude_rbw_legs()


def test_cli_writes_all_tables_with_headers(mid_frames, tmp_path):
    persons, trips = mid_frames
    raw = tmp_path / "mid_raw"
    raw.mkdir()
    # this delivery's persons file carries BLAND itself (no household join needed)
    persons = persons.assign(BLAND=persons["H_ID"].map({1: 3, 2: 3, 3: 6, 4: 6, 5: 6}))
    persons.to_csv(raw / "MiD2023_Personen.csv", index=False)
    trips.assign(HP_ID=1).to_csv(raw / "MiD2023_Wege.csv", index=False)
    out = tmp_path / "out"
    module = _load_script()

    assert module.main(["--raw", str(raw), "--out-dir", str(out),
                        "--source-commit", "abc1234"]) == 0

    expected_columns = {
        M.STATUS_TABLE: M.STATUS_COLUMNS,
        M.DISTANCE_MODE_TABLE: M.DISTANCE_MODE_COLUMNS,
        M.SHARE_20_29_TABLE: M.SHARE_20_29_COLUMNS,
        M.BEHAVIOUR_TABLE: M.BEHAVIOUR_COLUMNS,
    }
    tables = {}
    for name, columns in expected_columns.items():
        text = (out / name).read_text(encoding="utf-8")
        assert text.startswith("#") and "\r\n" not in text
        header = [line for line in text.splitlines() if line.startswith("#")]
        assert sum(line.startswith("# Exclusions:") for line in header) == 1
        assert any("abc1234" in line for line in header)
        assert any("exclude_rbw_legs" in line for line in header)
        assert any("P_GEW" in line for line in header)
        assert any("ST_WOTAG" in line for line in header)
        tables[name] = pd.read_csv(out / name, comment="#", dtype={"region": str})
        assert list(tables[name].columns) == list(columns)
    assert len(tables[M.STATUS_TABLE]) == 12
    assert len(tables[M.BEHAVIOUR_TABLE]) == sum(2 * len(labels) + 1
                                                  for labels in CONTROLS.values())
    assert set(tables[M.SHARE_20_29_TABLE]["region"]) >= {M.REGION_GERMANY, "03", "06"}
