"""Tests of the student level table, omega, the level gate against SrV and the employed pre-check.

All expected values are hand-computed from the small synthetic inputs built here (no committed
table, no probe number).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration import student_control_evidence as E
from braunschweig.calibration import student_level_table as L
from tests.student_gate_inputs import ZGB, gate_inputs as _gate_inputs

WOLFSBURG = "03103"

# students living per Kreis: 600 + 7 * 200 = 2000 over the eight Kreise, 1800 over the seven
# surveyed ones (Wolfsburg holds 200)
LIVING = pd.Series({code: (600.0 if code == "03101" else 200.0) for code in ZGB})
# SrV share of the adult students by age group; the ages 20 to 29 are 0.5 + 0.25 = 0.75
AGE_SHARES = pd.Series({"18_19": 0.1, "20_24": 0.5, "25_29": 0.25, "30plus": 0.15})
# Joint MiD share of (sex, age group) among the adult students; the age margin is 0.1 / 0.5 / 0.3 / 0.1
JOINT = pd.Series({("female", "18_19"): 0.06, ("female", "20_24"): 0.30, ("female", "25_29"): 0.18,
                   ("female", "30plus"): 0.06, ("male", "18_19"): 0.04, ("male", "20_24"): 0.20,
                   ("male", "25_29"): 0.12, ("male", "30plus"): 0.04})
CENSUS = {code: (500.0 if code == "03101" else 100.0) for code in ZGB}


def _zensus_students(per_kreis=125.0):
    """Eight Kreise (one count for all, or a dict per Kreis), plus the Land and Germany rows that
    must not count. With 125 each the eight Kreise hold 1000."""
    counts = per_kreis if isinstance(per_kreis, dict) else {code: per_kreis for code in ZGB}
    rows = [(code, "20-29", counts[code], 5000.0) for code in ZGB]
    rows += [("03", "20-29", 9999.0, 99999.0), ("DG", "20-29", 99999.0, 999999.0)]
    return pd.DataFrame(rows, columns=["region", "age_group", "students_nonemployed_persons",
                                       "all_persons"])


def _mid(students=0.18, pupils_and_students=0.20):
    return pd.DataFrame({"region": ["DE", "03"], "share_students": [students, 0.5],
                         "share_pupils_and_students": [pupils_and_students, 0.5]})


def test_omega_by_hand():
    # Zensus 1000 over eight Kreise, MiD 0.18 / 0.20 -> Z = 900; level 2000, share 20-29 = 0.75
    # -> S = 1500; omega = 1 - 900 / 1500 = 0.4
    value, parts = L.omega(LIVING, _zensus_students(), _mid(), AGE_SHARES)
    assert value == pytest.approx(0.4)
    assert parts["Z"] == pytest.approx(900.0) and parts["S"] == pytest.approx(1500.0)
    assert parts["mid_students_to_pupils_and_students_ratio"] == pytest.approx(0.9)
    assert parts["age_share_20_29_among_adult_students"] == pytest.approx(0.75)
    assert "srv_age_share_20_29_among_adult_students" not in parts


@pytest.mark.parametrize("per_kreis", [250.0, 0.0])  # omega = -0.2 and exactly 1
def test_omega_outside_zero_one_raises(per_kreis):
    with pytest.raises(ValueError, match="omega"):
        L.omega(LIVING, _zensus_students(per_kreis), _mid(), AGE_SHARES)


def test_omega_uses_eight_kreise_and_the_gate_seven():
    srv, census, mid, sites = _gate_inputs()

    def pooled_balance(living):
        gate = E.build_level_gate(srv, census, mid, sites,
                                  residents_implied=living.to_dict())
        return gate.set_index("code").loc[E.REGION_CODE,
                                          "residents_implied_by_enrollment_persons"]

    base, _ = L.omega(LIVING, _zensus_students(), _mid(), AGE_SHARES)
    more_wolfsburg = LIVING.copy()
    more_wolfsburg[WOLFSBURG] += 400.0
    moved, parts = L.omega(more_wolfsburg, _zensus_students(), _mid(), AGE_SHARES)
    # S = 2400 * 0.75 = 1800 -> omega = 1 - 900 / 1800 = 0.5: the Wolfsburg change moves omega
    assert moved == pytest.approx(0.5) and moved != pytest.approx(base)
    assert parts["S"] == pytest.approx(1800.0)
    # ... and leaves the pooled gate row (the seven surveyed Kreise = 1800) where it was
    assert pooled_balance(LIVING) == pytest.approx(1800.0)
    assert pooled_balance(more_wolfsburg) == pytest.approx(1800.0)


def test_omega_refuses_a_missing_kreis():
    with pytest.raises(ValueError, match="03158"):
        L.omega(LIVING.drop("03158"), _zensus_students(), _mid(), AGE_SHARES)


def test_census_pattern_keeps_the_total_and_follows_the_census():
    level, parts = L.census_pattern_level(LIVING, _zensus_students(CENSUS))
    assert list(level.index) == list(ZGB)
    assert level.sum() == pytest.approx(2000.0)
    assert level["03101"] == pytest.approx(2000.0 * 500 / 1200)
    assert level["03102"] == pytest.approx(2000.0 * 100 / 1200)
    assert parts["balance_share"]["03101"] == pytest.approx(0.3)
    assert parts["level_share"]["03101"] == pytest.approx(500 / 1200)
    assert parts["level_total_persons"] == pytest.approx(2000.0)
    assert parts["population_change_applied"] is False
    assert "population_change" not in parts


def test_census_pattern_carries_the_population_change():
    change = pd.Series({code: (0.9 if code == "03101" else 1.0) for code in ZGB})
    level, parts = L.census_pattern_level(LIVING, _zensus_students(CENSUS), change)
    assert level["03101"] == pytest.approx(2000.0 * 450 / 1150)
    assert level["03158"] == pytest.approx(2000.0 * 100 / 1150)
    assert parts["population_change_applied"] is True
    assert parts["population_change"]["03101"] == pytest.approx(0.9)


def test_census_pattern_logs_both_share_vectors_and_the_largest_shift(caplog):
    with caplog.at_level("INFO", logger=L.logger.name):
        L.census_pattern_level(LIVING, _zensus_students(CENSUS))
    text = " ".join(record.getMessage() for record in caplog.records)
    # Braunschweig moves from 0.30 to 0.4167 of the level: the largest shift
    assert "balance share" in text and "level share" in text
    assert "largest shift 03101: 0.3000 -> 0.4167" in text


@pytest.mark.parametrize("problem", ["zero_pattern", "missing_kreis_change", "zero_change",
                                     "missing_kreis_living", "wrong_age_group"])
def test_census_pattern_refuses_unusable_inputs(problem):
    census, change, living = CENSUS, None, LIVING
    zensus = None
    if problem == "zero_pattern":
        census = {code: 0.0 for code in ZGB}
    elif problem == "missing_kreis_change":
        change = pd.Series({code: 1.0 for code in ZGB if code != "03154"})
    elif problem == "zero_change":
        change = pd.Series({code: (0.0 if code == "03154" else 1.0) for code in ZGB})
    elif problem == "missing_kreis_living":
        living = LIVING.drop("03158")
    else:
        zensus = _zensus_students(CENSUS).assign(age_group="30-39")
    with pytest.raises(ValueError):
        L.census_pattern_level(living, _zensus_students(census) if zensus is None else zensus, change)


def test_census_pattern_error_names_an_infinite_zensus_count():
    census = {**CENSUS, "03154": float("inf")}
    with pytest.raises(ValueError, match="03154.*inf"):
        L.census_pattern_level(LIVING, _zensus_students(census))


def test_population_change_by_hand_and_refuses_gaps():
    rows = [(code, date, persons) for code in ZGB
            for date, persons in (("2022-12-31", 1000), ("2025-12-31", 900 if code == "03101" else 1100))]
    frame = pd.DataFrame(rows + [("03", "2022-12-31", 9), ("03101", "2023-12-31", 5)],
                         columns=["district_ars5", "date", "persons_20_29"])
    change = L.population_change(frame, "2022-12-31", "2025-12-31")
    assert list(change.index) == list(ZGB)
    assert change["03101"] == pytest.approx(0.9) and change["03102"] == pytest.approx(1.1)
    with pytest.raises(ValueError, match="03158.*missing at date 2025-12-31"):
        L.population_change(frame[~((frame.district_ars5 == "03158") & (frame.date == "2025-12-31"))],
                            "2022-12-31", "2025-12-31")
    zero = frame.assign(persons_20_29=frame["persons_20_29"].where(frame["district_ars5"] != "03102", 0))
    with pytest.raises(ValueError, match="non-positive.*at date 2022-12-31.*03102"):
        L.population_change(zero, "2022-12-31", "2025-12-31")
    doubled = pd.concat([frame, frame[(frame.district_ars5 == "03154") & (frame.date == "2022-12-31")]])
    with pytest.raises(ValueError, match="duplicated.*03154.*2022-12-31"):
        L.population_change(doubled, "2022-12-31", "2025-12-31")


def test_level_cells_are_the_joint_share_of_the_kreis_level():
    table = L.level_table(LIVING, JOINT, 0.4)
    assert list(table.columns) == L.LEVEL_COLUMNS
    assert len(table) == 64 and tuple(dict.fromkeys(table["age_group"])) == L.AGE_LABELS
    assert tuple(dict.fromkeys(table["sex"])) == L.SEXES
    assert list(dict.fromkeys(table["code"])) == list(ZGB)
    cell = table[table["code"] == "03101"].set_index(["sex", "age_group"])
    assert cell.loc[("female", "20_24"), "students_persons"] == pytest.approx(180.0)
    assert cell.loc[("female", "20_24"), "students_counted_employed_persons"] == pytest.approx(72.0)
    assert table.groupby("code")["students_persons"].sum()["03102"] == pytest.approx(200.0)


def test_age_margin_sums_the_sexes():
    margin = L.age_margin(JOINT)
    assert list(margin.index) == list(L.AGE_LABELS)
    assert margin["20_24"] == pytest.approx(0.5) and margin["30plus"] == pytest.approx(0.1)


@pytest.mark.parametrize("bad", ["missing_cell", "nan_cell", "sum", "negative"])
def test_level_table_and_age_margin_refuse_incomplete_joint_shares(bad):
    joint = JOINT.copy()
    if bad == "missing_cell":
        joint = joint.drop(("male", "30plus"))
    elif bad == "nan_cell":
        joint[("male", "30plus")] = float("nan")
    elif bad == "negative":
        joint[("male", "30plus")] = -0.04
        joint[("male", "25_29")] = 0.20
    else:
        joint[("male", "30plus")] = 0.5
    with pytest.raises(ValueError):
        L.level_table(LIVING, joint, 0.4)
    with pytest.raises(ValueError):
        L.age_margin(joint)


def test_level_table_refuses_omega_outside_zero_one():
    with pytest.raises(ValueError):
        L.level_table(LIVING, JOINT, 1.0)


def _employed(female_16_29, male_16_29):
    """Zensus employed persons of 03101 in two classes of the 16_29 group plus 30-39."""
    rows = [("03101", "15-19", "F", 10), ("03101", "20-29", "F", female_16_29 - 10),
            ("03101", "30-39", "F", 50),
            ("03101", "15-19", "M", 5), ("03101", "20-29", "M", male_16_29 - 5),
            ("03101", "30-39", "M", 100),
            ("03102", "20-29", "F", 7)]  # another Kreis: must not leak into 03101
    return pd.DataFrame(rows, columns=["region", "age_class", "sex", "employed_persons"])


def test_employed_precheck_by_hand():
    level = L.level_table(LIVING.loc[["03101"]], JOINT, 0.4)
    out = L.employed_target_precheck(level, _employed(310, 205)).set_index(["code", "sex",
                                                                         "grid_age_group"])
    assert len(out) == 4
    # the three young groups of a sex land in ONE grid row (A13): female 600 * (0.06 + 0.30 + 0.18) = 324
    # students * 0.4 = 129.6 counted employed of 310 -> 180.4
    row = out.loc[("03101", "female", "16_29")]
    assert row["zensus_employed_persons"] == 310
    assert row["students_counted_employed_persons"] == pytest.approx(129.6)
    assert row["remainder_persons"] == pytest.approx(180.4)
    # male 18_19 + 20_24 + 25_29: 600 * (0.04 + 0.20 + 0.12) = 216 * 0.4 = 86.4 of 205 -> 118.6
    assert out.loc[("03101", "male", "16_29"), "remainder_persons"] == pytest.approx(118.6)
    # the students aged 30 or older go to the grid group 30 to 39: 600 * 0.04 = 24 * 0.4 = 9.6 of 100
    assert out.loc[("03101", "male", "30_39"), "remainder_persons"] == pytest.approx(90.4)
    assert out.loc[("03101", "female", "30_39"), "remainder_persons"] == pytest.approx(
        50 - 600 * 0.06 * 0.4)


def test_employed_precheck_raises_on_a_negative_remainder():
    level = L.level_table(LIVING.loc[["03101"]], JOINT, 0.4)
    # 129.6 students counted employed against 100 employed persons of that cell
    with pytest.raises(ValueError, match="03101"):
        L.employed_target_precheck(level, _employed(100, 205))


def test_employed_precheck_refuses_a_kreis_missing_from_the_zensus():
    level = L.level_table(LIVING.loc[["03151"]], JOINT, 0.4)
    with pytest.raises(ValueError, match="03151"):
        L.employed_target_precheck(level, _employed(310, 205))


# --------------------------------------------------------------------------- gate against SrV
AWAY_SHARE = 0.1
# The adopted level and the level under the census-year enrollment per Kreis (hand values):
# 100, 200, ... 800 and half of it; Wolfsburg (03103) is the third Kreis.
ADOPTED = pd.Series({code: 100.0 * (i + 1) for i, code in enumerate(ZGB)})
CENSUS_YEAR = ADOPTED / 2.0


def _gate_against_srv(balance_03102=1150.0):
    """The synthetic gate inputs of the evidence tests with hand-picked residents implied.

    03102: SrV 900 persons, away share 0.1 -> 1000; its se_share / share is set to
    0.0025 / 0.05 = 0.05 -> standard error 50; balance 1150 -> +3.0 se. 03101: SrV 12000 -> 13333.3,
    se_share / share = 0.01 / 0.2 -> se 666.7, balance 13000. The other surveyed Kreise: SrV 900
    -> 1000, se_share / share = 0.2 -> se 200, balance 1000. Wolfsburg is not surveyed.
    """
    srv, census, mid, sites = _gate_inputs()
    srv = srv.copy()
    srv.loc[srv["code"] == "03102", "se_student_share_adults"] = 0.0025
    implied = {code: 1000.0 for code in ZGB}
    implied.update({"03101": 13000.0, "03102": balance_03102, WOLFSBURG: 5000.0})
    level_gate = E.build_level_gate(srv, census, mid, sites, residents_implied=implied)
    model_today = pd.Series({**{code: 10.0 + i for i, code in enumerate(ZGB)},
                             E.REGION_CODE: 99.0})
    return srv, level_gate, model_today


def test_gate_adds_the_away_students_and_flags_beyond_two_se():
    srv, level_gate, model_today = _gate_against_srv()
    gate = L.gate_table(level_gate, srv, AWAY_SHARE, AGE_SHARES, model_today, ADOPTED, CENSUS_YEAR)
    assert list(gate.columns) == L.GATE_COLUMNS
    assert gate["code"].tolist() == list(ZGB) + [E.REGION_CODE]
    row = gate.set_index("code").loc["03102"]
    assert row["balance_students_persons"] == 1150.0
    assert row["srv_students_weighted_persons"] == pytest.approx(900.0)
    assert row["srv_away_all_day_share"] == pytest.approx(0.1)
    assert row["srv_students_incl_away_persons"] == pytest.approx(1000.0)
    assert row["srv_se_students_incl_away_persons"] == pytest.approx(50.0)
    assert row["difference_persons"] == pytest.approx(150.0)
    assert row["difference_in_srv_se"] == pytest.approx(3.0)
    assert bool(row["beyond_two_se"]) is True
    assert row["balance_students_20_29_persons"] == pytest.approx(1150.0 * 0.75)
    assert row["model_today_students_persons"] == 11.0

    by_code = gate.set_index("code")
    # 03101: 12000 / 0.9 = 13333.3 against a balance of 13000 -> -0.5 se, not beyond
    assert by_code.loc["03101", "difference_in_srv_se"] == pytest.approx(
        (13000.0 - 12000.0 / 0.9) / (12000.0 / 0.9 * 0.05))
    assert not bool(by_code.loc["03101", "beyond_two_se"])
    # the two 20-29 estimates are the level gate's columns, unchanged
    shared = level_gate.set_index("code")
    # (the srv estimate only on the Kreis rows: the pooled row is the sum of the Kreise, see below)
    kreis_codes = [code for code in by_code.index if code != E.REGION_CODE]
    pd.testing.assert_series_equal(by_code["census_estimate_students_20_29_persons"],
                                   shared.loc[by_code.index, "census_estimate_students_20_29_persons"])
    pd.testing.assert_series_equal(by_code.loc[kreis_codes, "srv_estimate_students_20_29_persons"],
                                   shared.loc[kreis_codes, "srv_estimate_students_20_29_persons"])
    # the pooled row compares the seven surveyed Kreise: 13000 + 1150 + 5 * 1000
    assert by_code.loc[E.REGION_CODE, "balance_students_persons"] == pytest.approx(19150.0)


def test_gate_compares_the_students_20_29_with_the_whole_kreis_srv_estimate():
    # SrV share 20-29 x Zensus population 20-29 (tests/student_gate_inputs.py); the balance side is the
    # balance x the age share 20-29 (0.75). 03102: share 0.1, se 0.06, population 2000 -> 200 +- 120;
    # balance 1150 * 0.75 = 862.5 -> +662.5 = +5.52 se. 03101: 0.25 +- 0.05 of 4000 -> 1000 +- 200;
    # 13000 * 0.75 = 9750 -> +43.75 se. Pooled = sum of the seven Kreise:
    # 1000 + 6 * 200 = 2200, se = sqrt(200^2 + 6 * 120^2) = 355.5; 19150 * 0.75 = 14362.5 -> +12162.5 = +34.2 se
    # (NOT the pooled share 0.15 x 16000 persons = 2400 +- 320).
    srv, level_gate, model_today = _gate_against_srv()
    gate = L.gate_table(level_gate, srv, AWAY_SHARE, AGE_SHARES, model_today, ADOPTED, CENSUS_YEAR).set_index("code")
    columns = ["srv_estimate_students_20_29_persons", "srv_se_estimate_students_20_29_persons",
               "difference_20_29_persons", "difference_20_29_in_srv_se"]
    assert gate.loc["03102", columns].tolist() == pytest.approx([200.0, 120.0, 662.5, 662.5 / 120.0])
    assert gate.loc["03101", columns].tolist() == pytest.approx([1000.0, 200.0, 8750.0, 43.75])
    pooled_se = float(np.sqrt(200.0 ** 2 + 6 * 120.0 ** 2))
    assert gate.loc[E.REGION_CODE, columns].tolist() == pytest.approx(
        [2200.0, pooled_se, 12162.5, 12162.5 / pooled_se])
    # 03151 and the other surveyed Kreise: 200 +- 120 against 1000 * 0.75 = 750: +4.58 se
    assert gate.loc["03151", "difference_20_29_in_srv_se"] == pytest.approx(550.0 / 120.0)
    assert gate["beyond_two_se_20_29"].to_dict() == {code: code != WOLFSBURG for code in gate.index}
    for column in columns:
        assert np.isnan(gate.loc[WOLFSBURG, column]), column
    assert L.gate_triggered_area_consistent(gate.reset_index())
    # Only the Braunschweig and the pooled row trigger, and only on the 20-29 flag.
    others_only = gate.reset_index().assign(
        beyond_two_se_20_29=lambda frame: ~frame["code"].isin(["03101", E.REGION_CODE]), beyond_two_se=True)
    assert not L.gate_triggered_area_consistent(others_only)


def test_gate_pooled_20_29_estimate_is_the_sum_of_the_kreis_estimates():
    # Two surveyed Kreise with different shares (hand values): 03101 share 0.25 +- 0.05 of 4000 persons ->
    # 1000 +- 200; 03102 share 0.10 +- 0.06 of 2000 -> 200 +- 120. Pooled: 1200 +- sqrt(200^2 + 120^2) = 233.2,
    # not the pooled share (0.15 +- 0.02) times the pooled population (6000 -> 900 +- 120).
    srv, level_gate, model_today = _gate_against_srv()
    keep = ["03101", "03102", E.REGION_CODE, WOLFSBURG]
    level_gate = level_gate.copy()
    other = ~level_gate["code"].isin(keep)
    level_gate.loc[other, "srv_student_share_adults"] = np.nan
    for column in ("srv_estimate_students_20_29_persons", "srv_se_student_share_20_29"):
        level_gate.loc[other, column] = np.nan
    pooled_row = level_gate["code"] == E.REGION_CODE
    level_gate.loc[pooled_row, "srv_estimate_students_20_29_persons"] = 900.0
    level_gate.loc[pooled_row, "srv_se_student_share_20_29"] = 0.02
    level_gate.loc[pooled_row, "census_population_20_29_persons"] = 6000.0
    gate = L.gate_table(level_gate, srv, AWAY_SHARE, AGE_SHARES, model_today, ADOPTED, CENSUS_YEAR).set_index("code")
    pooled = gate.loc[E.REGION_CODE]
    se = float(np.sqrt(200.0 ** 2 + 120.0 ** 2))
    assert pooled["srv_estimate_students_20_29_persons"] == pytest.approx(1200.0)
    assert pooled["srv_se_estimate_students_20_29_persons"] == pytest.approx(se)
    assert pooled["difference_20_29_persons"] == pytest.approx(pooled["balance_students_20_29_persons"] - 1200.0)
    assert pooled["difference_20_29_in_srv_se"] == pytest.approx(pooled["difference_20_29_persons"] / se)
    # the Kreis rows keep their own estimates
    assert gate.loc["03101", "srv_estimate_students_20_29_persons"] == pytest.approx(1000.0)


def test_gate_keeps_wolfsburg_with_nan_on_the_srv_side():
    srv, level_gate, model_today = _gate_against_srv()
    row = L.gate_table(level_gate, srv, AWAY_SHARE, AGE_SHARES,
                       model_today, ADOPTED, CENSUS_YEAR).set_index("code").loc[WOLFSBURG]
    for column in ("srv_students_weighted_persons", "srv_away_all_day_share",
                   "srv_students_incl_away_persons", "srv_se_students_incl_away_persons",
                   "difference_persons", "difference_in_srv_se"):
        assert np.isnan(row[column]), column
    assert not bool(row["beyond_two_se"])
    assert row["balance_students_persons"] == 5000.0  # the balance side exists without SrV


def test_gate_carries_the_level_and_its_students_20_29():
    # 20-29 = share 0.5 + 0.25 of the age Series. Kreis 03102: 200 -> 150, census year 100 -> 75.
    # Wolfsburg keeps its own row (300 -> 225, 150 -> 112.5). The pooled row is the sum of the seven
    # surveyed Kreise: 3600 - 300 = 3300 -> 2475, and 1650 -> 1237.5.
    srv, level_gate, model_today = _gate_against_srv()
    gate = L.gate_table(level_gate, srv, AWAY_SHARE, AGE_SHARES, model_today, ADOPTED,
                        CENSUS_YEAR).set_index("code")
    columns = ["level_students_persons", "level_students_20_29_persons",
               "level_census_year_students_20_29_persons"]
    assert list(L.GATE_COLUMNS[-3:]) == columns
    assert gate.loc["03102", columns].tolist() == pytest.approx([200.0, 150.0, 75.0])
    assert gate.loc[WOLFSBURG, columns].tolist() == pytest.approx([300.0, 225.0, 112.5])
    assert gate.loc[E.REGION_CODE, columns].tolist() == pytest.approx([3300.0, 2475.0, 1237.5])


@pytest.mark.parametrize("problem", ["no_wolfsburg", "nan", "negative", "census_year_missing"])
def test_gate_refuses_an_unusable_level_series(problem):
    srv, level_gate, model_today = _gate_against_srv()
    adopted, census_year = ADOPTED, CENSUS_YEAR
    if problem == "no_wolfsburg":
        adopted = ADOPTED.drop(WOLFSBURG)
    elif problem == "nan":
        adopted = ADOPTED.where(ADOPTED.index != "03154", float("nan"))
    elif problem == "negative":
        census_year = CENSUS_YEAR.where(CENSUS_YEAR.index != "03154", -1.0)
    else:
        census_year = CENSUS_YEAR.drop("03158")
    with pytest.raises(ValueError):
        L.gate_table(level_gate, srv, AWAY_SHARE, AGE_SHARES, model_today, adopted, census_year)


@pytest.mark.parametrize("problem", ["no_balance", "away_share", "model_missing", "age_label"])
def test_gate_refuses_bad_inputs(problem):
    srv, level_gate, model_today = _gate_against_srv()
    away, age = AWAY_SHARE, AGE_SHARES
    if problem == "no_balance":
        level_gate = level_gate.assign(residents_implied_by_enrollment_persons=np.nan)
    elif problem == "away_share":
        away = 1.0
    elif problem == "model_missing":
        model_today = model_today.drop("03154")
    else:
        age = AGE_SHARES.drop("25_29")
    with pytest.raises(ValueError):
        L.gate_table(level_gate, srv, away, age, model_today, ADOPTED, CENSUS_YEAR)


@pytest.mark.parametrize("code", ["03101", E.REGION_CODE])
def test_gate_raises_when_a_trigger_row_has_a_zero_standard_error(code):
    srv, level_gate, model_today = _gate_against_srv()
    srv.loc[srv["code"] == code, "se_student_share_adults"] = 0.0
    with pytest.raises(ValueError, match=code):
        L.gate_table(level_gate, srv, AWAY_SHARE, AGE_SHARES, model_today, ADOPTED, CENSUS_YEAR)


def test_gate_warns_and_counts_a_zero_standard_error_of_another_kreis(caplog):
    srv, level_gate, model_today = _gate_against_srv()
    srv.loc[srv["code"] == "03151", "se_student_share_adults"] = 0.0
    with caplog.at_level("WARNING", logger=L.logger.name):
        gate = L.gate_table(level_gate, srv, AWAY_SHARE, AGE_SHARES, model_today, ADOPTED, CENSUS_YEAR)
    row = gate.set_index("code").loc["03151"]
    assert row["srv_se_students_incl_away_persons"] == 0.0
    assert np.isnan(row["difference_in_srv_se"]) and not bool(row["beyond_two_se"])
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "03151" in warnings[0].getMessage()


def test_away_share_is_one_pooled_value_on_every_surveyed_row():
    srv, level_gate, model_today = _gate_against_srv()
    gate = L.gate_table(level_gate, srv, AWAY_SHARE, AGE_SHARES, model_today, ADOPTED, CENSUS_YEAR)
    assert set(gate["srv_away_all_day_share"].dropna()) == {AWAY_SHARE}


def _flags(**by_code):
    codes = [*ZGB, E.REGION_CODE]
    return pd.DataFrame({"code": codes, "beyond_two_se": [by_code.get(c, False) for c in codes]})


def test_gate_triggered_only_by_braunschweig_or_the_pooled_row():
    assert not L.gate_triggered(_flags())
    assert not L.gate_triggered(_flags(**{"03102": True, WOLFSBURG: True, "03158": True}))
    assert L.gate_triggered(_flags(**{"03101": True}))
    assert L.gate_triggered(_flags(**{E.REGION_CODE: True}))
    with pytest.raises(ValueError, match="03101"):
        L.gate_triggered(_flags().query("code != '03101'"))
