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
SEX_SHARES = pd.Series({"female": 0.6, "male": 0.4})


def _zensus_students(per_kreis=125.0):
    """Eight Kreise of 125 persons = 1000, plus the Land and Germany rows that must not count."""
    rows = [(code, "20-29", per_kreis, 5000.0) for code in ZGB]
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
    assert parts["srv_age_share_20_29_among_adult_students"] == pytest.approx(0.75)


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


def test_level_cells_sum_to_the_kreis_level_and_carry_omega():
    level = L.level_table(LIVING, AGE_SHARES, SEX_SHARES, 0.4)
    assert list(level.columns) == L.LEVEL_COLUMNS
    assert len(level) == 8 * 2 * 2
    sums = level.groupby("code")["students_persons"].sum()
    pd.testing.assert_series_equal(sums, LIVING.rename("students_persons"), check_names=False,
                                   check_freq=False)
    cell = level.set_index(["code", "age_group", "sex"]).loc[("03101", "18_29", "female")]
    assert cell["students_persons"] == pytest.approx(600 * (0.1 + 0.5 + 0.25) * 0.6)  # 306
    assert cell["students_counted_employed_persons"] == pytest.approx(306 * 0.4)
    old = level.set_index(["code", "age_group", "sex"]).loc[("03101", "30plus", "male")]
    assert old["students_persons"] == pytest.approx(600 * 0.15 * 0.4)  # 36


@pytest.mark.parametrize("age, sex, omega", [
    (pd.Series({"18_19": 0.1, "20_24": 0.5, "25_29": 0.25, "30plus": 0.2}), SEX_SHARES, 0.4),
    (AGE_SHARES, pd.Series({"female": 0.6, "male": 0.5}), 0.4),
    (AGE_SHARES, SEX_SHARES, 1.0)])
def test_level_table_refuses_shares_that_do_not_sum_to_one_and_omega_outside(age, sex, omega):
    with pytest.raises(ValueError):
        L.level_table(LIVING, age, sex, omega)


def _employed(female_16_29, male_16_29):
    """Zensus employed persons of 03101 in two classes of the 16_29 group plus 30-39."""
    rows = [("03101", "15-19", "F", 10), ("03101", "20-29", "F", female_16_29 - 10),
            ("03101", "30-39", "F", 50),
            ("03101", "15-19", "M", 5), ("03101", "20-29", "M", male_16_29 - 5),
            ("03101", "30-39", "M", 100),
            ("03102", "20-29", "F", 7)]  # another Kreis: must not leak into 03101
    return pd.DataFrame(rows, columns=["region", "age_class", "sex", "employed_persons"])


def test_employed_precheck_by_hand():
    level = L.level_table(LIVING.loc[["03101"]], AGE_SHARES, SEX_SHARES, 0.4)
    out = L.employed_target_precheck(level, _employed(310, 205)).set_index(["code", "sex",
                                                                         "grid_age_group"])
    assert len(out) == 4
    # female 18_29: 306 students * 0.4 = 122.4 counted employed of 310 -> 187.6
    row = out.loc[("03101", "female", "16_29")]
    assert row["zensus_employed_persons"] == 310
    assert row["students_counted_employed_persons"] == pytest.approx(122.4)
    assert row["remainder_persons"] == pytest.approx(187.6)
    # male 18_29: 600 * 0.85 * 0.4 = 204 * 0.4 = 81.6 of 205 -> 123.4
    assert out.loc[("03101", "male", "16_29"), "remainder_persons"] == pytest.approx(123.4)
    # the students aged 30 or older go to the grid group 30 to 39 (A13): 36 * 0.4 = 14.4 of 100
    assert out.loc[("03101", "male", "30_39"), "remainder_persons"] == pytest.approx(85.6)
    assert out.loc[("03101", "female", "30_39"), "remainder_persons"] == pytest.approx(
        50 - 600 * 0.15 * 0.6 * 0.4)


def test_employed_precheck_raises_on_a_negative_remainder():
    level = L.level_table(LIVING.loc[["03101"]], AGE_SHARES, SEX_SHARES, 0.4)
    # 122.4 students counted employed against 100 employed persons of that cell
    with pytest.raises(ValueError, match="03101"):
        L.employed_target_precheck(level, _employed(100, 205))


def test_employed_precheck_refuses_a_kreis_missing_from_the_zensus():
    level = L.level_table(LIVING.loc[["03151"]], AGE_SHARES, SEX_SHARES, 0.4)
    with pytest.raises(ValueError, match="03151"):
        L.employed_target_precheck(level, _employed(310, 205))


# --------------------------------------------------------------------------- gate against SrV
AWAY_SHARE = 0.1


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
    gate = L.gate_table(level_gate, srv, AWAY_SHARE, AGE_SHARES, model_today)
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
    for column in ("srv_estimate_students_20_29_persons",
                   "census_estimate_students_20_29_persons"):
        pd.testing.assert_series_equal(by_code[column], shared.loc[by_code.index, column])
    # the pooled row compares the seven surveyed Kreise: 13000 + 1150 + 5 * 1000
    assert by_code.loc[E.REGION_CODE, "balance_students_persons"] == pytest.approx(19150.0)


def test_gate_keeps_wolfsburg_with_nan_on_the_srv_side():
    srv, level_gate, model_today = _gate_against_srv()
    row = L.gate_table(level_gate, srv, AWAY_SHARE, AGE_SHARES,
                       model_today).set_index("code").loc[WOLFSBURG]
    for column in ("srv_students_weighted_persons", "srv_away_all_day_share",
                   "srv_students_incl_away_persons", "srv_se_students_incl_away_persons",
                   "difference_persons", "difference_in_srv_se"):
        assert np.isnan(row[column]), column
    assert not bool(row["beyond_two_se"])
    assert row["balance_students_persons"] == 5000.0  # the balance side exists without SrV


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
        L.gate_table(level_gate, srv, away, age, model_today)


@pytest.mark.parametrize("code", ["03101", E.REGION_CODE])
def test_gate_raises_when_a_trigger_row_has_a_zero_standard_error(code):
    srv, level_gate, model_today = _gate_against_srv()
    srv.loc[srv["code"] == code, "se_student_share_adults"] = 0.0
    with pytest.raises(ValueError, match=code):
        L.gate_table(level_gate, srv, AWAY_SHARE, AGE_SHARES, model_today)


def test_gate_warns_and_counts_a_zero_standard_error_of_another_kreis(caplog):
    srv, level_gate, model_today = _gate_against_srv()
    srv.loc[srv["code"] == "03151", "se_student_share_adults"] = 0.0
    with caplog.at_level("WARNING", logger=L.logger.name):
        gate = L.gate_table(level_gate, srv, AWAY_SHARE, AGE_SHARES, model_today)
    row = gate.set_index("code").loc["03151"]
    assert row["srv_se_students_incl_away_persons"] == 0.0
    assert np.isnan(row["difference_in_srv_se"]) and not bool(row["beyond_two_se"])
    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1 and "03151" in warnings[0].getMessage()


def test_away_share_note_says_it_is_one_pooled_value():
    # the value really is the same on every surveyed row; the note says so for the CLI header
    srv, level_gate, model_today = _gate_against_srv()
    gate = L.gate_table(level_gate, srv, AWAY_SHARE, AGE_SHARES, model_today)
    assert set(gate["srv_away_all_day_share"].dropna()) == {AWAY_SHARE}
    assert "ONE value" in L.AWAY_SHARE_COLUMN_NOTE


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
