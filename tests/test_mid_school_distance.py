import pandas as pd
import braunschweig.data.mid.school_distance as sd
from braunschweig.data.mid.school_distance import (
    AGEGROUP_TO_LEVEL, build_target_table, routed_to_straight_line,
)
from braunschweig.calibration import circuity


def _raw():
    return pd.DataFrame({
        "regiostar7": [72, 74],
        "km_0_6": [2.0, 3.0],
        "km_7_10": [2.0, 5.0],
        "km_11_13": [4.0, 9.0],
        "km_14_17": [4.0, 9.0],
    })


def test_agegroup_to_level_mapping():
    assert AGEGROUP_TO_LEVEL == {
        "km_0_6": "kindergarten",
        "km_7_10": "grundschule",
        "km_11_13": "sekundar_1",
        "km_14_17": "oberstufe",
    }


def test_build_target_table_long_per_rs7_level():
    out = build_target_table(_raw(), detour_factor=1.0)
    row = out[(out.regiostar7 == 74) & (out.level == "sekundar_1")].iloc[0]
    assert row.routed_km == 9.0
    assert row.target_km == 9.0
    assert set(out.level.unique()) == {"kindergarten", "grundschule", "sekundar_1", "oberstufe"}


def test_detour_factor_shortens_target():
    out = build_target_table(_raw(), detour_factor=1.3)
    row = out[(out.regiostar7 == 72) & (out.level == "grundschule")].iloc[0]
    assert abs(row.target_km - 2.0 / 1.3) < 1e-9


def test_routed_to_straight_line():
    assert abs(routed_to_straight_line(6.5, 1.3) - 5.0) < 1e-9


# ---------------------------------------------------------------------------
# Tier 3C: network-aware inversion tests
# ---------------------------------------------------------------------------

def test_routed_to_straight_line_legacy_factor_preserved():
    assert sd.routed_to_straight_line(13.0, detour_factor=1.3) == 10.0


def test_build_target_table_default_is_constant():
    """Default (no mode) must use constant 1.3 — byte-identical to pre-Tier-3."""
    raw = pd.DataFrame([{"regiostar7": 77, "km_0_6": 2.0, "km_7_10": 3.0,
                         "km_11_13": 5.0, "km_14_17": 12.0}])
    tbl = sd.build_target_table(raw)
    gs = tbl[tbl["level"] == "grundschule"].iloc[0]
    ob = tbl[tbl["level"] == "oberstufe"].iloc[0]
    # constant mode: target_km = routed_km / 1.3 for every level
    assert abs(gs["target_km"] - 3.0 / circuity.LEGACY_DETOUR_FACTOR) < 1e-9
    assert abs(ob["target_km"] - 12.0 / circuity.LEGACY_DETOUR_FACTOR) < 1e-9


def test_build_target_table_curve_mode_uses_network_per_level():
    """Explicit mode='curve' uses per-level network inversion (opt-in)."""
    raw = pd.DataFrame([{"regiostar7": 77, "km_0_6": 2.0, "km_7_10": 3.0,
                         "km_11_13": 5.0, "km_14_17": 12.0}])
    tbl = sd.build_target_table(raw, mode="curve")  # opt-in: curve, per-level network
    gs = tbl[tbl["level"] == "grundschule"].iloc[0]
    ob = tbl[tbl["level"] == "oberstufe"].iloc[0]
    # grundschule -> walk inversion; oberstufe -> car inversion
    assert gs["target_km"] == circuity.routed_to_euclidean(3.0, "walk", mode="curve")
    assert ob["target_km"] == circuity.routed_to_euclidean(12.0, "car", mode="curve")
