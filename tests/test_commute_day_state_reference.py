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
