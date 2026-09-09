import numpy as np
import pandas as pd
import pytest

from braunschweig.synthesis.day_absence import absence as D


def _reference(p_band=0.10, p_size=None):
    p_size = p_size if p_size is not None else {1: 0.5, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0}
    return D.AbsenceReference(p_absent_by_band={b: p_band for b in D.AGE_BAND_LABELS},
                              p_all_absent_by_size=p_size)


def _persons(n_households=400):
    # 200 singles aged 40, 200 couples (35, 36): singles feed the household stage, couples only the residual.
    rows = []
    pid = 0
    for h in range(n_households):
        ages = [40] if h % 2 == 0 else [35, 36]
        for age in ages:
            rows.append({"person_id": pid, "household_id": h, "age": age}); pid += 1
    return pd.DataFrame(rows)


def test_household_stage_marks_whole_households_and_nothing_else():
    ref = _reference(p_band=0.0)   # no residual: only the household stage acts
    out, diag = D.draw_absence(_persons(), ref, np.random.RandomState(1))
    by_hh = out.groupby("household_id")["day_absence_state"].agg(lambda s: set(s))
    assert all(states in ({D.STATE_PRESENT}, {D.STATE_ABSENT_HOUSEHOLD}) for states in by_hh)
    singles = out[out["household_size_class"] == 1]
    assert 0.35 < (singles["day_absence_state"] == D.STATE_ABSENT_HOUSEHOLD).mean() < 0.65
    assert (out.loc[out["household_size_class"] == 2, "day_absence_state"] == D.STATE_PRESENT).all()
    assert diag["n_absent_individual"] == 0


def test_residual_stage_hits_the_band_target_in_expectation():
    ref = _reference(p_band=0.10, p_size={k: 0.0 for k in range(1, 6)})
    out, diag = D.draw_absence(_persons(2000), ref, np.random.RandomState(2))
    realised = (out["day_absence_state"] != D.STATE_PRESENT).mean()
    assert 0.085 < realised < 0.115
    assert diag["by_band"]["30-44"]["residual_p"] == pytest.approx(0.10)


def test_residual_probability_is_reduced_by_what_the_household_stage_realised():
    ref = _reference(p_band=0.10, p_size={1: 0.20, 2: 0.0, 3: 0.0, 4: 0.0, 5: 0.0})
    out, diag = D.draw_absence(_persons(4000), ref, np.random.RandomState(3))
    band = diag["by_band"]["30-44"]  # 40-year-old singles share this band with the couples
    assert 0 < band["residual_p"] < 0.10
    assert abs(band["realised_rate"] - 0.10) < 0.015


def test_overshoot_gives_zero_residual_and_a_warning(caplog):
    ref = _reference(p_band=0.01, p_size={1: 0.5, 2: 0.5, 3: 0.5, 4: 0.5, 5: 0.5})
    with caplog.at_level("WARNING"):
        out, diag = D.draw_absence(_persons(), ref, np.random.RandomState(4))
    assert diag["by_band"]["30-44"]["residual_p"] == 0.0
    assert diag["n_bands_overshoot"] >= 1
    assert "overshoot" in caplog.text


def test_household_stage_off_is_an_individual_draw_at_the_band_rate():
    ref = _reference(p_band=0.10)
    out, diag = D.draw_absence(_persons(2000), ref, np.random.RandomState(5), household_stage=False)
    assert diag["n_absent_household"] == 0
    assert (out["p_household"] == 0.0).all()
    assert 0.085 < (out["day_absence_state"] != D.STATE_PRESENT).mean() < 0.115


def test_deterministic_and_row_order_independent():
    ref = _reference()
    persons = _persons()
    a, _ = D.draw_absence(persons, ref, np.random.RandomState(7))
    b, _ = D.draw_absence(persons.sample(frac=1.0, random_state=0), ref, np.random.RandomState(7))
    pd.testing.assert_frame_equal(a, b)
    assert list(a.columns) == list(D.ABSENCE_COLUMNS) and len(a) == len(persons)


def test_missing_age_raises():
    persons = _persons(); persons.loc[0, "age"] = np.nan
    with pytest.raises(ValueError, match="age"):
        D.draw_absence(persons, _reference(), np.random.RandomState(0))


def test_load_reference_reads_the_committed_tables_by_column_name():
    import os
    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ref = D.load_absence_reference(os.path.join(repo, "eqasim-data", "data", "braunschweig", "srv"))
    assert set(ref.p_absent_by_band) == set(D.AGE_BAND_LABELS)
    assert set(ref.p_all_absent_by_size) == {1, 2, 3, 4, 5}
    assert 0.01 < ref.p_absent_by_band["6-17"] < 0.03 and 0.07 < ref.p_absent_by_band["18-29"] < 0.10
