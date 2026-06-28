import numpy as np, pandas as pd
from braunschweig.synthesis.locations.secondary_other_potential import derive_other_potential

def _mapping():
    return pd.DataFrame({
        "bosserhof_class": ["customer oriented services", "industrial operations production",
                            "services"],
        "eqasim_purpose": ["other", "work", "other"],
        "other_destination": [True, False, True],
    })

def test_derive_caps_whitelist_giant_and_downweights_nonwhitelist():
    b = pd.DataFrame({
        "bosserhof_class_clean": ["customer oriented services",  # VW-like giant, whitelist
                                  "industrial operations production",  # giant, non-whitelist
                                  "services",                          # normal whitelist
                                  "services"],                         # tiny whitelist
        "volume_m3":        [8_886_475.0, 2_295_808.0, 5_000.0, 10.0],
        "potential_generic":[26_659_425.0, 3_099_341.0, 11_550.0, 23.0],
    })
    pot, stats = derive_other_potential(
        b, _mapping(), broad_share=0.54, errand_share=0.46,
        min_volume_m3=50.0, cap_percentile=0.99)
    cap = stats["cap_value"]
    # whitelist giant -> capped * 1.0 (NOT 26.7M)
    assert pot.iloc[0] == cap and pot.iloc[0] < 26_659_425.0
    # non-whitelist giant -> capped * broad_share (0.54), still a candidate, not 0
    assert np.isclose(pot.iloc[1], cap * 0.54)
    # normal whitelist below cap -> generic * 1.0 unchanged
    assert np.isclose(pot.iloc[2], 11_550.0)
    # tiny whitelist (volume < 50) -> 0
    assert pot.iloc[3] == 0.0
    assert stats["n_tiny"] == 1 and stats["n_whitelist"] >= 2

def test_unknown_class_treated_as_broad_and_counted():
    b = pd.DataFrame({
        "bosserhof_class_clean": ["something new"],
        "volume_m3": [1000.0], "potential_generic": [2000.0]})
    pot, stats = derive_other_potential(
        b, _mapping(), broad_share=0.54, errand_share=0.46,
        min_volume_m3=50.0, cap_percentile=0.99)
    assert stats["n_unknown_class"] == 1
    assert np.isclose(pot.iloc[0], min(2000.0, stats["cap_value"]) * 0.54)
