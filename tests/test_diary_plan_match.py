import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import diary_facts as dfm
from braunschweig.popsim import diary_plan_match as dpm

FLAGS = dict(exclude_rbw_legs=True, exclude_holidays=True, drop_leading_arrive_home_leg=True)


def _donors():
    # real donor persons (weekday, kernwo 2). anzwege1: 803 mobile / 803 immobile / 804 / 0 only-rbW /
    # 2 valid / 2 valid on a public holiday / 1 valid arriving-home-only
    return pd.DataFrame({
        "H_ID": [1, 2, 3, 4, 5, 6, 7], "P_ID": [1, 1, 1, 1, 1, 1, 1],
        "anzwege1": [803, 803, 804, 0, 2, 2, 1],
        "mobil": [1, 0, 9, 1, 1, 1, 1],
        "mobil_diff": [5, 6, 9, 2, 1, 1, 1],
        "feiertag": [0, 0, 0, 0, 0, 1, 0],
        "kernwo": [2, 2, 2, 2, 2, 2, 2],
        "member_imputed": [False] * 7,
        "source_H_ID": [1, 2, 3, 4, 5, 6, 7], "source_P_ID": [1] * 7,
        "P_GEW": [1.0] * 7, "HP_ALTER": [40] * 7, "HP_SEX": [1] * 7,
        "P_FSCHEIN": [1] * 7, "P_TAET": [1] * 7, "P_FKARTE": [1] * 7,
    })


def _wege():
    return pd.DataFrame({
        "H_ID":      [4, 4, 5, 5, 6, 6, 7],
        "P_ID":      [1, 1, 1, 1, 1, 1, 1],
        "W_ID":      [1, 2, 1, 2, 1, 2, 1],
        "W_ZWECK":   [2, 2, 1, 8, 1, 8, 8],
        "W_RBW":     [1, 1, 0, 0, 0, 0, 0],
        "W_SO1":     [701, 701, 1, 809, 1, 809, 2],
        "wegkm_imp": [5.0, 5.0, 3.0, 3.0, 3.0, 3.0, 4.0],
    })


def test_classify_plan_sources_reasons():
    donors = _donors(); facts = dfm.compute_diary_facts(_wege())
    reasons = dpm.classify_plan_sources(donors, donors, facts, **FLAGS)
    assert list(reasons) == ["nodiary_mobile", "nodiary_immobile_keep", "nodiary_unknown", "only_rbw",
                             "realisable", "holiday", "emptied_by_arrive_home_drop"]


def test_classify_respects_flags_off():
    donors = _donors(); facts = dfm.compute_diary_facts(_wege())
    reasons = dpm.classify_plan_sources(donors, donors, facts, exclude_rbw_legs=False,
                                        exclude_holidays=False, drop_leading_arrive_home_leg=False)
    assert reasons.iloc[3] == "realisable"   # only-rbW keeps its legs when rbW stays
    assert reasons.iloc[5] == "realisable"   # holiday diaries allowed
    assert reasons.iloc[6] == "realisable"   # arriving leg kept


def test_realisable_pool_excludes_bad_sources_and_holidays():
    donors = _donors(); facts = dfm.compute_diary_facts(_wege())
    pool_any = dpm.build_realisable_pool(donors, facts, mobility="any", **FLAGS)
    pool_mobile = dpm.build_realisable_pool(donors, facts, mobility="mobile", **FLAGS)
    assert set(pool_any["H_ID"]) == {5}          # 6 = holiday, 7 = emptied, 1-4 = no diary / only rbW
    assert set(pool_mobile["H_ID"]) == {5}


def test_reassign_remaps_only_flagged_sources_and_is_deterministic():
    donors = _donors(); facts = dfm.compute_diary_facts(_wege())
    out1, trace1, rep1 = dpm.reassign_diaryless_plan_sources(donors, donors, facts, rng=np.random.RandomState(3), **FLAGS)
    out2, trace2, rep2 = dpm.reassign_diaryless_plan_sources(donors, donors, facts, rng=np.random.RandomState(3), **FLAGS)
    pd.testing.assert_frame_equal(out1, out2)
    remapped = out1[trace1["reason"].isin(dpm.REASONS_REMAP).to_numpy()]
    assert set(zip(remapped["source_H_ID"], remapped["source_P_ID"])) == {(5, 1)}
    kept = out1.loc[out1["H_ID"].isin([2, 5])]
    assert (kept["source_H_ID"] == kept["H_ID"]).all()     # immobile 803 and the valid diary keep their source
    assert rep1.n_remapped == 5 and rep1.counts_by_reason["holiday"] == 1
    assert rep1.share_remapped == pytest.approx(5 / 7)


def test_reassign_raises_on_empty_pool():
    donors = _donors().iloc[:4]; facts = dfm.compute_diary_facts(_wege())
    with pytest.raises(ValueError, match="realisable"):
        dpm.reassign_diaryless_plan_sources(donors, donors, facts, rng=np.random.RandomState(0), **FLAGS)


def test_reassign_requires_mobility_columns():
    donors = _donors().drop(columns=["mobil"]); facts = dfm.compute_diary_facts(_wege())
    with pytest.raises(KeyError, match="mobil"):
        dpm.reassign_diaryless_plan_sources(donors, donors, facts, rng=np.random.RandomState(0), **FLAGS)
