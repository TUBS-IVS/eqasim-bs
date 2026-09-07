import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import diary_facts as df_mod


def _wege():
    # person (1,1): H->work, work->home direct, then 2 rbW legs (no times), km 10 + 15
    # person (1,2): arrives home first (W_SO1 == 2), then leaves to shop, no return (open end)
    # person (2,1): only rbW legs
    return pd.DataFrame({
        "H_ID":      [1, 1, 1, 1, 1, 1, 2, 2],
        "P_ID":      [1, 1, 1, 1, 2, 2, 1, 1],
        "W_ID":      [1, 2, 3, 4, 1, 2, 1, 2],
        "W_ZWECK":   [1, 8, 2, 2, 8, 4, 2, 2],
        "W_RBW":     [0, 0, 1, 1, 0, 0, 1, 1],
        "W_SO1":     [1, 809, 701, 701, 2, 809, 701, 701],
        "wegkm_imp": [5.0, 5.0, 10.0, 15.0, 3.0, 2.0, 7.0, 9994.0],
    })


def test_compute_diary_facts_counts_and_flags():
    facts = df_mod.compute_diary_facts(_wege())
    assert list(facts.columns) == list(df_mod.FACT_COLUMNS)
    p11 = facts.loc[(1, 1)]
    assert p11["n_direct_legs"] == 2 and p11["n_rbw_legs"] == 2
    assert p11["rbw_distance_km"] == pytest.approx(25.0)
    assert bool(p11["ends_at_home"]) is True and bool(p11["starts_arriving_home"]) is False
    p12 = facts.loc[(1, 2)]
    assert p12["first_so1"] == 2 and p12["first_direct_zweck"] == 8
    assert bool(p12["starts_arriving_home"]) is True and bool(p12["ends_at_home"]) is False
    p21 = facts.loc[(2, 1)]
    assert p21["n_direct_legs"] == 0 and p21["n_rbw_legs"] == 2
    assert p21["rbw_distance_km"] == pytest.approx(7.0)   # coded 9994 counted as 0
    assert p21["first_so1"] == -1 and bool(p21["ends_at_home"]) is False


def test_compute_diary_facts_requires_columns():
    with pytest.raises(KeyError, match="W_RBW"):
        df_mod.compute_diary_facts(_wege().drop(columns=["W_RBW"]))


@pytest.mark.parametrize("column", ["H_ID", "P_ID", "W_ID", "W_ZWECK", "W_SO1", "wegkm_imp"])
def test_compute_diary_facts_requires_each_column(column):
    with pytest.raises(KeyError, match=column):
        df_mod.compute_diary_facts(_wege().drop(columns=[column]))


def test_attach_plan_source_facts_uses_source_keys_and_fills_missing():
    facts = df_mod.compute_diary_facts(_wege())
    persons = pd.DataFrame({
        "H_ID": [10, 11, 12], "P_ID": [1, 1, 1],
        "source_H_ID": [1, 2, 3], "source_P_ID": [2, 1, 1],   # third source has no Wege
    })
    out = df_mod.attach_plan_source_facts(persons, facts)
    assert out.loc[0, "src_starts_arriving_home"] == True
    assert out.loc[1, "src_n_rbw_legs"] == 2 and out.loc[1, "src_n_direct_legs"] == 0
    assert out.loc[2, "src_n_direct_legs"] == 0 and out.loc[2, "src_rbw_distance_km"] == 0.0
    assert out.loc[2, "src_first_so1"] == -1 and out.loc[2, "src_ends_at_home"] == False
    assert len(out) == 3 and "src_n_direct_legs" in out.columns


@pytest.mark.parametrize("column", ["W_SO1", "W_ZWECK"])
def test_compute_diary_facts_raises_named_error_on_nan_direct_leg_code(column):
    """A NaN W_SO1 / W_ZWECK on a DIRECT leg must name the column and the count.

    Before this guard the failure surfaced as an opaque pandas "cannot convert NA
    to integer" from the internal astype(int) casts, naming neither the column nor
    how many rows were affected (final-review minor M9).
    """
    wege = _wege()
    wege.loc[0, column] = np.nan     # first DIRECT leg of person (1, 1)
    with pytest.raises(ValueError, match=rf"1/4 direct \(non-rbW\) Wege rows have a missing '{column}'"):
        df_mod.compute_diary_facts(wege)


def test_compute_diary_facts_ignores_nan_codes_on_rbw_legs():
    """Only DIRECT legs carry the first/last-leg facts, so an rbW leg's NaN is not an error."""
    wege = _wege()
    wege.loc[2, "W_SO1"] = np.nan    # an rbW leg of person (1, 1)
    facts = df_mod.compute_diary_facts(wege)
    assert facts.loc[(1, 1), "n_rbw_legs"] == 2
