import pandas as pd
from braunschweig.data.mikrozensus.school_distance import (
    BAND_MIDPOINTS_KM, banded_mean_km, bbs_target_km,
)


def _raw():
    return pd.DataFrame({
        "school_type": ["allgemeinbildend", "berufsbildend", "hochschule"],
        "lt5": [64.4, 18.8, 33.9],
        "b5_10": [21.9, 20.9, 19.9],
        "b10_25": [11.9, 33.0, 17.8],
        "b25_50": [1.5, 17.9, 14.3],
        "ge50": [0.2, 9.3, 13.7],
    })


def test_band_midpoints():
    assert BAND_MIDPOINTS_KM == (2.5, 7.5, 17.5, 37.5, 65.0)


def test_banded_mean_km_matches_hand_calc():
    shares = [18.8, 20.9, 33.0, 17.9, 9.3]   # BBS, percentages
    m = banded_mean_km(shares)
    assert abs(m - 20.57) < 0.1   # routed km


def test_bbs_target_applies_detour():
    raw = _raw()
    t = bbs_target_km(raw, detour_factor=1.3)
    assert abs(t - 20.57 / 1.3) < 0.05
