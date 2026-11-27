"""Student target builder (design A3, Task 6): one rounded student count, the other adults, area values.

Expected values are computed by hand in the comments; none re-implements the builder.
"""

from pathlib import Path

import pandas as pd
import pytest

from braunschweig.popsim import student_targets as ST
from braunschweig.popsim.student_level import AGE_LABELS, SEXES, StudentLevel

ALL_FOUR = list(ST.STUDENT_ENTRY_CATEGORIES)
REPO_DATA = Path(__file__).resolve().parents[1] / "eqasim-data" / "data"


def _shares(**overrides):
    values = dict(education_leg=0.4, work_leg=0.2,
                  season_ticket_by_area={"braunschweig": 0.9, "surrounding": 0.8},
                  season_ticket_region=0.5,
                  other_education_by_kreis={"03101": 0.03, "03102": 0.04, "03103": 0.05},
                  other_education_by_age_range={"18_29": 0.10, "30plus": 0.01})
    values.update(overrides)
    return ST.StudentShares(**values)


def _counts(rows):
    """rows: (kreis, students 18-29, students 30plus)."""
    records = []
    for kreis, young, old in rows:
        records.append({"ars5": kreis, "age_range": "18_29", "students_persons": young, "students_count": young})
        records.append({"ars5": kreis, "age_range": "30plus", "students_persons": old, "students_count": old})
    return pd.DataFrame(records)


def _level(cells):
    """cells: (kreis, age_group, female, male)."""
    records = []
    for kreis, age_group, female, male in cells:
        for sex, value in zip(SEXES, (female, male)):
            records.append({"code": kreis, "sex": sex, "age_group": age_group,
                            "students_persons": value, "students_counted_employed_persons": 0.0})
    return StudentLevel(table=pd.DataFrame(records), omega=0.1)


def _row(tables, entry, kreis):
    return tables[entry].set_index("ARS_kreis").loc[kreis]


def test_student_targets_by_hand():
    # 03101: persons 18-29 1000, 30+ 4000; students 200 / 15; e .4, w .2, ticket .9; s_k .03; p .10 / .01.
    # N = 800 / 3985; O = .03 x 4785 = 143.55; weights 80 / 39.85 -> 95.82 / 47.73; round(O) = 144 ->
    # floors 95 + 47 = 142, both remainders get one -> 96 / 48.
    persons = {("03101", "18_29"): 1000, ("03101", "30plus"): 4000}
    t = ST.student_kreis_targets(_counts([("03101", 200, 15)]), persons, _shares(),
                                 {"03101": "braunschweig"}, ALL_FOUR)
    a = _row(t, "student_education_18_29", "03101")
    assert [a["student_education_18_29_" + c] for c in ("student_edu", "student_noedu", "other_edu",
                                                         "other_noedu")] == [80, 120, 96, 704]
    b = _row(t, "student_education_30plus", "03101")
    assert [b["student_education_30plus_" + c] for c in ("student", "other_edu", "other_noedu")] == [15, 48, 3937]
    w = _row(t, "student_work_leg", "03101")
    assert [w["student_work_leg_" + c] for c in ("student_work", "student_nowork", "not_student")] == [40, 160, 800]
    k = _row(t, "student_season_ticket", "03101")
    assert [k["student_season_ticket_" + c] for c in ("student_flatrate", "student_no_flatrate",
                                                       "not_student")] == [180, 20, 800]
    universe = {"student_education_18_29": 1000, "student_education_30plus": 4000,
                "student_work_leg": 1000, "student_season_ticket": 1000}
    for name, table in t.items():
        assert table.drop(columns="ARS_kreis").sum(axis=1).iloc[0] == universe[name]
        assert (table.drop(columns="ARS_kreis").dtypes == "int64").all()


def test_student_labels_of_every_entry_add_up_to_one_count():
    # 03102 (surrounding): students 33 / 7; e .4 -> round(13.2) = 13 + 20; w .2 -> round(6.6) = 7 + 26;
    # ticket .8 -> round(26.4) = 26 + 7. Each Kreis: the student labels of all three entries sum to 33.
    persons = {("03101", "18_29"): 1000, ("03101", "30plus"): 4000,
               ("03102", "18_29"): 500, ("03102", "30plus"): 2000}
    t = ST.student_kreis_targets(_counts([("03101", 200, 15), ("03102", 33, 7)]), persons, _shares(),
                                 {"03101": "braunschweig", "03102": "surrounding"}, ALL_FOUR)
    p = "student_education_18_29_"
    for kreis, students in (("03101", 200), ("03102", 33)):
        edu = _row(t, "student_education_18_29", kreis)
        work = _row(t, "student_work_leg", kreis)
        ticket = _row(t, "student_season_ticket", kreis)
        assert edu[p + "student_edu"] + edu[p + "student_noedu"] == students
        assert work["student_work_leg_student_work"] + work["student_work_leg_student_nowork"] == students
        assert (ticket["student_season_ticket_student_flatrate"]
                + ticket["student_season_ticket_student_no_flatrate"]) == students
    r = _row(t, "student_education_18_29", "03102")
    assert (r[p + "student_edu"], r[p + "student_noedu"]) == (13, 20)
    # other adults of 03102: O = .04 x (467 + 1993) = 98.4 -> 68.97 / 29.43 -> 69 / 29 (round(O) = 98)
    assert (r[p + "other_edu"], r[p + "other_noedu"]) == (69, 398)
    assert _row(t, "student_education_30plus", "03102")["student_education_30plus_other_edu"] == 29


def test_one_rounding_keeps_the_kreis_level():
    # Level 4.4 (18-29: .5+.5+... per cell) and 4.45 (30plus) = 8.85 -> round 9. Rounding each range alone
    # gives 4 + 4 = 8; the largest remainder (.45 > .40) gives the extra student to 30plus: 4 / 5.
    level = _level([("03101", "18_19", 1.0, 0.5), ("03101", "20_24", 1.0, 0.5),
                    ("03101", "25_29", 0.7, 0.7), ("03101", "30plus", 2.0, 2.45)])
    counts = ST.student_counts(level).set_index("age_range")
    assert counts.loc["18_29", "students_persons"] == pytest.approx(4.4)
    assert counts.loc["30plus", "students_persons"] == pytest.approx(4.45)
    assert (counts.loc["18_29", "students_count"], counts.loc["30plus", "students_count"]) == (4, 5)


def test_student_counts_cover_every_kreis_with_both_ranges():
    cells = []
    for kreis in ("03101", "03102"):
        cells += [(kreis, label, 1.0, 1.0) for label in AGE_LABELS]
    counts = ST.student_counts(_level(cells))
    assert list(counts["ars5"]) == ["03101", "03101", "03102", "03102"]
    assert list(counts["age_range"]) == ["18_29", "30plus"] * 2
    # 18_29 = 3 groups x 2 sexes = 6; 30plus = 2; level 8 -> exactly 6 / 2
    assert list(counts["students_count"]) == [6, 2, 6, 2]


def _write(root, regional=None, by_area=None, other=None):
    folder = root / "braunschweig" / "srv"
    folder.mkdir(parents=True, exist_ok=True)
    regional = regional or (
        "# education_leg_share_control_definition=0.4, work_leg_share_control_definition=0.2\n"
        "group,parameter,base,label,n_unweighted,weight_sum,share,n_effective,se_share\n"
        "mobility_tools,season_ticket,students_present,flatrate,10,10,0.5,10,0.1\n")
    by_area = by_area or (
        "area,group,parameter,base,label,n_unweighted,weight_sum,share,n_effective,se_share\n"
        "braunschweig,mobility_tools,season_ticket,students_present,flatrate,10,10,0.9,10,0.1\n"
        "surrounding,mobility_tools,season_ticket,students_present,flatrate,10,10,0.8,10,0.1\n")
    other = other or (
        "code,level,group,age_range,n_unweighted,n_with_leg_unweighted,weight_sum,share,n_effective,se_share\n"
        "03101,kreis,other_adult,18plus,100,3,100,0.03,100,0.01\n"
        "03102,kreis,other_adult,18plus,100,4,100,0.04,100,0.01\n"
        "03103,kreis,other_adult,18plus,0,0,0,,,\n"
        "03ZGB,total,other_adult,18plus,300,15,300,0.05,300,0.01\n"
        "03ZGB,total,other_adult,18_29,300,15,300,0.10,300,0.01\n"
        "03ZGB,total,other_adult,30plus,300,15,300,0.02,300,0.01\n")
    (folder / "srv2023_student_parameters.csv").write_text(regional, encoding="utf-8")
    (folder / "srv2023_student_parameters_by_area.csv").write_text(by_area, encoding="utf-8")
    (folder / "srv2023_education_leg_by_student_status.csv").write_text(other, encoding="utf-8")
    return root


def test_wolfsburg_takes_the_region_values(tmp_path):
    shares = ST.read_student_shares(_write(tmp_path), ["03101", "03102", "03103"])
    # SrV does not survey 03103: its NaN row is replaced by the 03ZGB value (A21), the others keep their own
    assert shares.other_education_by_kreis == {"03101": 0.03, "03102": 0.04, "03103": 0.05}
    assert shares.season_ticket_region == 0.5
    assert shares.education_leg == 0.4 and shares.work_leg == 0.2
    assert shares.other_education_by_age_range == {"18_29": 0.10, "30plus": 0.02}
    # 03103: students 10 / 2, persons 100 / 400, area None -> ticket round(10 x .5) = 5 + 5;
    # O = .05 x (90 + 398) = 24.4 -> 12.95 / 11.45 -> round(O) 24 -> 13 / 11
    persons = {("03103", "18_29"): 100, ("03103", "30plus"): 400,
               ("03101", "18_29"): 1000, ("03101", "30plus"): 4000}
    t, diagnostics = ST.student_kreis_targets_with_diagnostics(
        _counts([("03101", 200, 15), ("03103", 10, 2)]), persons, shares,
        {"03101": "braunschweig", "03103": None}, ALL_FOUR)
    # one counter for both substitutions: 03103 other-adult share + 03103 ticket = 2 of the 4 cells
    # (other-adult share and ticket of two Kreise)
    assert diagnostics == {"kreise": 2, "region_value_substitutions": 2, "region_value_cells": 4,
                           "region_value_rate": 0.5}
    ticket = _row(t, "student_season_ticket", "03103")
    assert (ticket["student_season_ticket_student_flatrate"], ticket["student_season_ticket_student_no_flatrate"],
            ticket["student_season_ticket_not_student"]) == (5, 5, 90)
    assert _row(t, "student_education_18_29", "03103")["student_education_18_29_other_edu"] == 13
    assert _row(t, "student_education_30plus", "03103")["student_education_30plus_other_edu"] == 11


@pytest.mark.parametrize("students_young, persons_young, other_share",
                         [(1200, 1000, 0.03), (200, 210, 0.5)])
def test_negative_remainder_raises(students_young, persons_young, other_share):
    # first case: more students than persons. Second: N = 10 young non-students, but a share of .5
    # gives O = .5 x 3995 = 1997.5, of which 1 / 40.85 = 48.9 go to the young range (weights 10 x .1 and
    # 3985 x .01), so other_noedu = 10 - 49 < 0
    persons = {("03101", "18_29"): persons_young, ("03101", "30plus"): 4000}
    shares = _shares(other_education_by_kreis={"03101": other_share})
    with pytest.raises(ValueError, match="negative remainder"):
        ST.student_kreis_targets(_counts([("03101", students_young, 15)]), persons, shares,
                                 {"03101": "braunschweig"}, ALL_FOUR)


def test_kreis_without_non_students_gets_no_other_adults_and_an_empty_universe_has_no_share():
    # Kreis A: only students (persons == students in both ranges) -> O = 0, no division by zero, the
    # rest of every row is 0. Kreis B: nobody aged 18-29 (0 persons, 0 students) -> the work entry rows
    # are 0 and the long form has a NaN share for that universe, not a ZeroDivisionError.
    persons = {("A", "18_29"): 200, ("A", "30plus"): 15, ("B", "18_29"): 0, ("B", "30plus"): 400}
    shares = _shares(other_education_by_kreis={"A": 0.03, "B": 0.03})
    t = ST.student_kreis_targets(_counts([("A", 200, 15), ("B", 0, 2)]), persons, shares,
                                 {"A": "braunschweig", "B": "braunschweig"}, ALL_FOUR)
    a = _row(t, "student_education_18_29", "A")
    assert (a["student_education_18_29_other_edu"], a["student_education_18_29_other_noedu"]) == (0, 0)
    a30 = _row(t, "student_education_30plus", "A")
    assert (a30["student_education_30plus_other_edu"], a30["student_education_30plus_other_noedu"]) == (0, 0)
    assert list(_row(t, "student_work_leg", "B")) == [0, 0, 0]
    long = ST.student_targets_long(t, persons).set_index(["ars5", "control"])
    assert long.loc[("B", "student_work_leg"), "target_share"].isna().all()
    assert long.loc[("A", "student_work_leg"), "target_share"].sum() == 1.0


def test_missing_area_share_or_persons_raises():
    persons = {("03101", "18_29"): 1000, ("03101", "30plus"): 4000}
    with pytest.raises(ValueError, match="area 'surrounding'"):
        ST.student_kreis_targets(_counts([("03101", 200, 15)]), persons,
                                 _shares(season_ticket_by_area={"braunschweig": 0.9}),
                                 {"03101": "surrounding"}, ["student_season_ticket"])
    with pytest.raises(ValueError, match="persons_by_range has no value"):
        ST.student_kreis_targets(_counts([("03101", 200, 15)]), {("03101", "18_29"): 1000}, _shares(),
                                 {"03101": "braunschweig"}, ALL_FOUR)


def test_targets_long_lists_every_category_with_its_universe():
    persons = {("03101", "18_29"): 1000, ("03101", "30plus"): 4000}
    t = ST.student_kreis_targets(_counts([("03101", 200, 15)]), persons, _shares(),
                                 {"03101": "braunschweig"}, ["student_work_leg"])
    long = ST.student_targets_long(t, persons)
    assert list(long.columns) == ["ars5", "control", "category", "target_persons", "universe_persons",
                                  "target_share"]
    assert list(long["category"]) == ["student_work", "student_nowork", "not_student"]
    assert list(long["target_persons"]) == [40, 160, 800]
    assert set(long["universe_persons"]) == {1000}
    assert list(long["target_share"]) == [0.04, 0.16, 0.8]


def test_missing_srv_row_or_column_raises_naming_the_file(tmp_path):
    kreise = ["03101", "03102", "03103"]
    with pytest.raises(ValueError, match="srv2023_student_parameters.csv does not exist"):
        ST.read_student_shares(tmp_path / "nothing", kreise)
    no_key = ("# work_leg_share_control_definition=0.2\n"
              "group,parameter,base,label,n_unweighted,weight_sum,share,n_effective,se_share\n"
              "mobility_tools,season_ticket,students_present,flatrate,10,10,0.5,10,0.1\n")
    with pytest.raises(ValueError, match=r"srv2023_student_parameters.csv.*education_leg_share_control_definition"):
        ST.read_student_shares(_write(tmp_path / "a", regional=no_key), kreise)
    no_column = ("code,level,group,age_range,n_unweighted\n03ZGB,total,other_adult,18plus,3\n")
    with pytest.raises(ValueError, match=r"srv2023_education_leg_by_student_status.csv.*\['share'\]"):
        ST.read_student_shares(_write(tmp_path / "b", other=no_column), kreise)
    no_flatrate = ("area,group,parameter,base,label,n_unweighted,weight_sum,share,n_effective,se_share\n"
                   "braunschweig,mobility_tools,season_ticket,students_present,flatrate,10,10,0.9,10,0.1\n")
    with pytest.raises(ValueError, match=r"srv2023_student_parameters_by_area.csv.*area=surrounding"):
        ST.read_student_shares(_write(tmp_path / "c", by_area=no_flatrate), kreise,
                               areas=["braunschweig", "surrounding"])
    nan_kreis = ("code,level,group,age_range,n_unweighted,n_with_leg_unweighted,weight_sum,share,n_effective,se_share\n"
                 "03101,kreis,other_adult,18plus,0,0,0,,,\n"
                 "03ZGB,total,other_adult,18plus,300,15,300,0.05,300,0.01\n"
                 "03ZGB,total,other_adult,18_29,300,15,300,0.10,300,0.01\n"
                 "03ZGB,total,other_adult,30plus,300,15,300,0.02,300,0.01\n")
    with pytest.raises(ValueError, match=r"srv2023_education_leg_by_student_status.csv.*code=03101"):
        ST.read_student_shares(_write(tmp_path / "d", other=nan_kreis), ["03101"])


def test_committed_tables_are_readable_and_wolfsburg_follows_the_region():
    kreise = ["03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158"]
    shares = ST.read_student_shares(REPO_DATA, kreise, areas=["braunschweig", "surrounding"])
    region = pd.read_csv(REPO_DATA / ST.OTHER_ADULTS_RELPATH, comment="#", dtype={"code": str})
    region = region[(region["code"] == "03ZGB") & (region["group"] == "other_adult")
                    & (region["age_range"] == "18plus")]["share"].iloc[0]
    assert shares.other_education_by_kreis["03103"] == region
    assert set(shares.season_ticket_by_area) == {"braunschweig", "surrounding"}
    assert shares.season_ticket_by_area["braunschweig"] > shares.season_ticket_region > \
        shares.season_ticket_by_area["surrounding"]
