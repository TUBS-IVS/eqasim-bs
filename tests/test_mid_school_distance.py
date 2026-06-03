import pandas as pd
from braunschweig.data.mid.school_distance import (
    AGEGROUP_TO_LEVEL, build_target_table, routed_to_straight_line,
)


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
