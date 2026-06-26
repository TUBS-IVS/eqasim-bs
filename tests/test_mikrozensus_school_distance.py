import pandas as pd
from braunschweig.data.mikrozensus.school_distance import (
    BAND_MIDPOINTS_KM, banded_mean_km, bbs_target_km,
)
import braunschweig.data.mikrozensus.school_distance as ms
from braunschweig.calibration import circuity


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


def test_hochschule_target_applies_detour():
    from braunschweig.data.mikrozensus.school_distance import hochschule_target_km
    raw = _raw()
    t = hochschule_target_km(raw, detour_factor=1.3)
    # banded mean for hochschule row (33.9/19.9/17.8/14.3/13.7) ~ 19.72 km routed
    assert abs(t - 19.72 / 1.3) < 0.1


# ---------------------------------------------------------------------------
# Tier 3C: network-aware inversion tests
# ---------------------------------------------------------------------------

def test_bbs_target_default_is_constant():
    """Default (no mode) must use constant detour 1.3 — byte-identical to pre-Tier-3."""
    raw = pd.DataFrame([{"school_type": "berufsbildend",
                         "lt5": 10, "b5_10": 20, "b10_25": 30, "b25_50": 30, "ge50": 10}])
    routed = ms.banded_mean_km([10, 20, 30, 30, 10])
    expected = routed / circuity.LEGACY_DETOUR_FACTOR
    assert abs(ms.bbs_target_km(raw) - expected) < 1e-9


def test_bbs_target_curve_mode_uses_car_inversion():
    """Explicit mode='curve' inverts the fitted circuity curve (opt-in)."""
    raw = pd.DataFrame([{"school_type": "berufsbildend",
                         "lt5": 10, "b5_10": 20, "b10_25": 30, "b25_50": 30, "ge50": 10}])
    routed = ms.banded_mean_km([10, 20, 30, 30, 10])
    assert ms.bbs_target_km(raw, mode="curve") == circuity.routed_to_euclidean(
        routed, "car", mode="curve"
    )
