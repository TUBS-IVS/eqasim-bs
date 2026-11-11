import numpy as np
import pandas as pd
import pytest
from braunschweig.popsim.shop_subtype import (
    estimate_daily_probability, impute_subtype, tt_band, SHOP_DAILY_W_ZWD,
)


def test_daily_probability_weighted_and_excludes_sentinels():
    # 3 labelled daily (501) walk legs + 1 non-daily (502) walk leg, + 1 PAPI sentinel (2202)
    w = pd.DataFrame({
        "W_ZWECK": [4, 4, 4, 4, 4],
        "mode":    ["walk"] * 5,
        "travel_time": [120, 120, 120, 120, 120],
        "W_ZWD":   [501, 501, 501, 502, 2202],
        "W_GEW":   [1.0, 1.0, 1.0, 1.0, 5.0],   # sentinel has big weight but must be excluded
    })
    prob = estimate_daily_probability(w, min_obs=1)
    # walk, band(120)=0 -> 3 daily / 4 labelled = 0.75 (sentinel excluded)
    assert abs(prob[("walk", tt_band(120))] - 0.75) < 1e-9


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_tt_band_refuses_a_non_finite_travel_time(value):
    # np.digitize sorts NaN after every edge, so an unknown duration used to land in the
    # longest band silently (audit B1).
    with pytest.raises(ValueError, match="finite"):
        tt_band(value)


def test_daily_probability_leaves_legs_of_unknown_duration_out():
    # 40 long non-daily legs, 40 short daily legs and 40 daily legs whose duration is unknown:
    # the unknown ones must neither enter the long-leg cell nor the marginal.
    w = pd.DataFrame({
        "W_ZWECK": [4] * 120,
        "mode": ["car"] * 120,
        "travel_time": [2000.0] * 40 + [100.0] * 40 + [np.nan] * 40,
        "W_ZWD": [502] * 40 + [501] * 80,
        "W_GEW": [1.0] * 120,
    })
    prob = estimate_daily_probability(w, min_obs=30)
    assert prob[("car", 3)] == 0.0
    assert prob[("__marginal__", -1)] == pytest.approx(0.5)


def test_daily_probability_refuses_an_estimate_without_a_usable_leg():
    w = pd.DataFrame({"W_ZWECK": [4, 4], "mode": ["car", "car"],
                      "travel_time": [np.nan, np.nan], "W_ZWD": [501, 502], "W_GEW": [1.0, 1.0]})
    with pytest.raises(ValueError):
        estimate_daily_probability(w, min_obs=1)


def test_impute_is_deterministic_and_uses_marginal_for_missing_cell():
    prob = {("walk", 0): 1.0}
    marginal = 0.0
    modes = np.array(["walk", "car"])
    tts = np.array([100, 100])
    out = impute_subtype(modes, tts, prob, marginal, np.random.RandomState(0))
    assert out[0] == True            # walk cell -> p=1.0 -> daily
    assert out[1] == False           # car cell absent -> marginal 0.0 -> non-daily
