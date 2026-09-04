"""Unit tests for braunschweig.calibration.commute_day_state_reference (synthetic frames)."""
import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration import commute_day_state_reference as R


@pytest.mark.parametrize("km, label", [
    (3.0, "lt10"), (10.0, "10_25"), (24.9, "10_25"), (25.0, "25_50"), (99.9, "50_100"),
    (100.0, "100_200"), (200.0, "100_200"), (250.0, "gt200"), (0.0, None), (-1.0, None), (np.nan, None),
])
def test_classify_commute_distance(km, label):
    assert R.classify_commute_distance(km) == label


def _mid_persons():
    # 6 weekday module persons: 4 at 5 km (2 workplace, 1 home, 1 not worked), 2 at 150 km (1 home, 1 other)
    return pd.DataFrame({
        "P_GEW":     [1.0, 1.0, 2.0, 1.0, 1.0, 1.0, 5.0],
        "arbwo":     [1,   1,   1,   1,   1,   1,   0],      # last row: weekend -> excluded
        "M_HOFF":    [1,   1,   1,   1,   1,   1,   1],
        "P_STARB1":  [1,   1,   1,   2,   1,   1,   1],
        "starb2":    [2,   2,   1,   409, 1,   3,   2],
        "P_ARB_ENTF":[5.0, 6.0, 4.0, 7.0, 150.0, 120.0, 3.0],
    })


def test_build_mid_workday_location_table_shares():
    t = R.build_mid_workday_location_table(_mid_persons()).set_index("distance_class")
    lt10 = t.loc["lt10"]
    assert lt10["n_unweighted"] == 4
    assert lt10["share_at_workplace"] == pytest.approx(2 / 5)      # weights 1+1 of 5
    assert lt10["share_at_home"] == pytest.approx(2 / 5)           # weight 2
    assert lt10["share_did_not_work"] == pytest.approx(1 / 5)
    far = t.loc["100_200"]
    assert far["share_at_home"] == pytest.approx(0.5) and far["share_other_place"] == pytest.approx(0.5)
    share_cols = [c for c in t.columns if c.startswith("share_")]
    assert np.allclose(t[share_cols].sum(axis=1), 1.0)
    assert "all" in t.index and t.loc["all", "n_unweighted"] == 6


def test_build_mid_workday_location_table_missing_distance_row():
    p = _mid_persons(); p.loc[0, "P_ARB_ENTF"] = 996.0
    t = R.build_mid_workday_location_table(p).set_index("distance_class")
    assert t.loc["all", "n_missing_distance"] == 1
    assert t.loc["all", "share_missing"] == 0.0
    assert "lt10" in t.index and t.loc["lt10", "n_unweighted"] == 3


def _mid_pool_persons():
    return pd.DataFrame({
        "HP_ID":   [1, 2, 3, 4, 5],
        "H_ID":    [10, 10, 20, 30, 30],
        "P_GEW":   [1.0] * 5,
        "arbwo":   [1, 1, 1, 1, 1],
        "M_HOFF":  [1, 0, 1, 1, 1],
        "P_STARB1":[1, 2, 1, 1, 1],
        "starb2":  [1, 409, 1, 2, 1],
        "P_ARB_ENTF": [8.0, 0.0, 150.0, 30.0, 996.0],
        "HP_ALTER":[40, 6, 35, 50, 45],
        "HP_SEX":  [2, 1, 1, 1, 2],
    })


def _mid_pool_trips():
    return pd.DataFrame({"HP_ID": [1, 1, 1, 3, 3], "W_ZWECK": [6, 4, 8, 7, 8]})


def test_build_mid_home_office_donor_pool_cells():
    pool = R.build_mid_home_office_donor_pool(_mid_pool_persons(), _mid_pool_trips())
    total = pool[(pool["distance_class"] == "all") & (pool["has_children"] == "all")].iloc[0]
    assert total["n_donors"] == 3            # HP 1 (8 km, escort, child in hh), HP 3 (150 km), HP 5 (missing distance)
    lt10 = pool[(pool["distance_class"] == "lt10") & (pool["has_children"] == True) & (pool["has_active_escort"] == True)].iloc[0]  # noqa: E712
    assert lt10["n_donors"] == 1 and lt10["n_mobile"] == 1 and lt10["mean_trips_mobile"] == pytest.approx(3.0)
    far = pool[(pool["distance_class"] == "100_200") & (pool["has_children"] == False) & (pool["has_active_escort"] == False)].iloc[0]  # noqa: E712
    assert far["n_donors"] == 1 and far["share_female"] == pytest.approx(0.0)
    missing = pool[(pool["distance_class"] == "missing") & (pool["has_children"] == "all")].iloc[0]
    assert missing["n_donors"] == 1 and missing["n_mobile"] == 0
