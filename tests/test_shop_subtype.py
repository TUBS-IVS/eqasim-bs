import numpy as np
import pandas as pd
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


def test_impute_is_deterministic_and_uses_marginal_for_missing_cell():
    prob = {("walk", 0): 1.0}
    marginal = 0.0
    modes = np.array(["walk", "car"])
    tts = np.array([100, 100])
    out = impute_subtype(modes, tts, prob, marginal, np.random.RandomState(0))
    assert out[0] == True            # walk cell -> p=1.0 -> daily
    assert out[1] == False           # car cell absent -> marginal 0.0 -> non-daily
