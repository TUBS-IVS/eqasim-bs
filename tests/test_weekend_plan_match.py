# tests/test_weekend_plan_match.py
import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import weekend_plan_match as wpm


def _households():
    return pd.DataFrame({
        "H_ID": [1, 2],
        "H_GR": [2, 1],
        "hh_type5": ["couple", "single"],
        "oek_status": [3, 2],
        "RegioStaR7": [71, 77],
        "H_ANZAUTO": [2, 0],
    })


def _persons():
    return pd.DataFrame({
        "H_ID": [1, 1, 2],
        "P_ID": [1, 2, 1],
        "HP_ALTER": [40, 38, 25],
        "HP_SEX": [1, 2, 2],
        "P_FSCHEIN": [1, 2, 1],
        "P_FKARTE": [1, 1, 4],
    })


def test_build_hh_features_columns_and_values():
    feats = wpm.build_hh_features(_households(), _persons())
    assert feats.loc[1, "size"] == 2
    assert feats.loc[1, "car_class"] == "2plus"
    assert feats.loc[2, "car_class"] == "0"
    assert bool(feats.loc[1, "any_license"]) is True   # P1 has licence
    assert bool(feats.loc[1, "any_pt"]) is False        # neither has a sub code
    assert bool(feats.loc[2, "any_pt"]) is True         # P_FKARTE 4 is a sub


def test_match_household_prefers_zero_relaxation():
    weekday = pd.DataFrame({
        "size": [2, 2], "hh_type5": ["couple", "couple"], "oek_status": [3, 9],
        "regiostar7": [71, 71], "car_class": ["2plus", "0"],
        "any_license": [True, False], "any_pt": [False, False],
    }, index=pd.Index([100, 101], name="H_ID"))
    target = pd.Series({
        "size": 2, "hh_type5": "couple", "oek_status": 3, "regiostar7": 71,
        "car_class": "2plus", "any_license": True, "any_pt": False,
    })
    mid, level = wpm.match_household(7, target, weekday, rng=np.random.RandomState(0))
    assert mid == 100 and level == 0


def test_match_household_relaxes_until_pool_nonempty():
    weekday = pd.DataFrame({
        "size": [2], "hh_type5": ["single"], "oek_status": [1], "regiostar7": [77],
        "car_class": ["0"], "any_license": [False], "any_pt": [True],
    }, index=pd.Index([200], name="H_ID"))
    target = pd.Series({
        "size": 2, "hh_type5": "couple", "oek_status": 3, "regiostar7": 71,
        "car_class": "2plus", "any_license": True, "any_pt": False,
    })
    mid, level = wpm.match_household(7, target, weekday, rng=np.random.RandomState(0))
    assert mid == 200 and level == len(wpm.SOFT_KEYS_BY_PRIORITY)  # all soft keys dropped


def test_match_household_returns_none_when_no_equal_size():
    weekday = pd.DataFrame({
        "size": [3], "hh_type5": ["couple"], "oek_status": [3], "regiostar7": [71],
        "car_class": ["2plus"], "any_license": [True], "any_pt": [False],
    }, index=pd.Index([300], name="H_ID"))
    target = pd.Series({
        "size": 2, "hh_type5": "couple", "oek_status": 3, "regiostar7": 71,
        "car_class": "2plus", "any_license": True, "any_pt": False,
    })
    mid, level = wpm.match_household(7, target, weekday, rng=np.random.RandomState(0))
    assert mid is None and level is None


def test_align_members_pairs_by_age_band_then_sex():
    target = pd.DataFrame({"HP_ALTER": [40, 8], "HP_SEX": [1, 2]}).reset_index(drop=True)
    donor = pd.DataFrame({"HP_ALTER": [10, 42], "HP_SEX": [2, 1]}).reset_index(drop=True)
    pairs = wpm.align_members(target, donor)
    # adult target (pos 0) -> adult donor (pos 1); child target (pos 1) -> child donor (pos 0)
    assert sorted(pairs) == [(0, 1), (1, 0)]


def test_match_person_exact_then_relaxes():
    weekday = pd.DataFrame({
        "H_ID": [50, 51], "P_ID": [1, 1],
        "HP_ALTER": [40, 9], "HP_SEX": [1, 2],
        "P_FSCHEIN": [1, 2], "P_TAET": [1, 11], "P_FKARTE": [1, 1],
    })
    target = pd.Series({
        "HP_ALTER": 41, "HP_SEX": 1, "P_FSCHEIN": 1, "P_TAET": 1, "P_FKARTE": 1,
    })
    h, p, level = wpm.match_person(target, weekday, rng=np.random.RandomState(0))
    assert (h, p) == (50, 1) and level == 0


def test_match_person_raises_on_empty_pool():
    target = pd.Series({"HP_ALTER": 30, "HP_SEX": 1, "P_FSCHEIN": 1, "P_TAET": 1, "P_FKARTE": 1})
    with pytest.raises(ValueError, match="empty weekday person pool"):
        wpm.match_person(target, pd.DataFrame(columns=["H_ID", "P_ID", "HP_ALTER", "HP_SEX", "P_FSCHEIN", "P_TAET", "P_FKARTE"]), rng=np.random.RandomState(0))
