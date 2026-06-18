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
