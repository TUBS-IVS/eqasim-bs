"""Unit tests for braunschweig.calibration.commute_day_state_reference (synthetic frames)."""
import logging

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


@pytest.mark.parametrize("raw_km, cleaned_km", [
    (996.0, None), (999.0, None), (2202.0, None), (200.0, 200.0), (15.5, 15.5),
])
def test_clean_mid_commute_distance_km(raw_km, cleaned_km):
    cleaned = R.clean_mid_commute_distance_km([raw_km])
    if cleaned_km is None:
        assert pd.isna(cleaned.iloc[0])
    else:
        assert cleaned.iloc[0] == pytest.approx(cleaned_km)


def _mid_persons():
    # 6 weekday module persons: 4 at 5 km (2 workplace, 1 home, 1 not worked), 2 at 150 km (1 home, 1 other);
    # row 6 is a weekend row (excluded); row 7 has P_STARB1 == 9 ("no answer" -> state-missing) at 60 km;
    # row 8 has a P_STARB1 filter code (202, "not employed/not asked" -> excluded from the universe).
    return pd.DataFrame({
        "P_GEW":     [1.0, 1.0, 2.0, 1.0, 1.0, 1.0, 5.0, 1.0, 1.0],
        "arbwo":     [1,   1,   1,   1,   1,   1,   0,   1,   1],
        "M_HOFF":    [1,   1,   1,   1,   1,   1,   1,   1,   1],
        "P_STARB1":  [1,   1,   1,   2,   1,   1,   1,   9,   202],
        "starb2":    [2,   2,   1,   409, 1,   3,   2,   1,   1],
        "P_ARB_ENTF":[5.0, 6.0, 4.0, 7.0, 150.0, 120.0, 3.0, 60.0, 10.0],
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
    # Row 7 (P_STARB1 == 9, "no answer") is the only 50-100 km person; its state is undetermined,
    # so share_missing on that row is 1.0 (Ruling R4: state-missing, non-zero).
    fifty_to_hundred = t.loc["50_100"]
    assert fifty_to_hundred["n_unweighted"] == 1
    assert fifty_to_hundred["share_missing"] == pytest.approx(1.0)
    share_cols = list(R.SHARE_COLUMNS)
    assert np.allclose(t[share_cols].sum(axis=1), 1.0)
    # Row 8 (P_STARB1 == 202, a "not employed/not asked" filter code) is outside the universe and
    # must not be counted anywhere: 7 = 9 input rows - 1 weekend (row 6) - 1 filter code (row 8).
    assert "all" in t.index and t.loc["all", "n_unweighted"] == 7


def test_build_mid_workday_location_table_missing_distance_row():
    p = _mid_persons(); p.loc[0, "P_ARB_ENTF"] = 996.0
    t = R.build_mid_workday_location_table(p).set_index("distance_class")
    assert t.loc["all", "n_missing_distance"] == 1
    # Distance-missing (row 0) does not affect share_missing -- only row 7's state-missing
    # (P_STARB1 == 9) does: weight 1 of the universe's total weight 8 (1+1+2+1+1+1+1).
    assert t.loc["all", "share_missing"] == pytest.approx(1 / 8)
    assert "lt10" in t.index and t.loc["lt10", "n_unweighted"] == 3


def test_inconsistent_starb1_starb2_pair_logs_warning(caplog):
    p = _mid_persons()
    # Row 3 (P_STARB1 == 2, starb2 == 409) is consistent; flip its starb2 so P_STARB1 == 2 with
    # starb2 != 409, which must be flagged as an inconsistent pair (Ruling R4).
    p.loc[3, "starb2"] = 2
    with caplog.at_level(logging.WARNING):
        R.build_mid_workday_location_table(p)
    assert any("consistency check" in message for message in caplog.messages)


def test_load_workday_location_table_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        R.load_workday_location_table(tmp_path)


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
    lt10_mask = (
        (pool["distance_class"] == "lt10")
        & (pool["has_children"] == True)  # noqa: E712
        & (pool["has_active_escort"] == True)  # noqa: E712
    )
    lt10 = pool[lt10_mask].iloc[0]
    assert lt10["n_donors"] == 1 and lt10["n_mobile"] == 1 and lt10["mean_trips_mobile"] == pytest.approx(3.0)
    far_mask = (
        (pool["distance_class"] == "100_200")
        & (pool["has_children"] == False)  # noqa: E712
        & (pool["has_active_escort"] == False)  # noqa: E712
    )
    far = pool[far_mask].iloc[0]
    assert far["n_donors"] == 1 and far["share_female"] == pytest.approx(0.0)
    missing = pool[(pool["distance_class"] == "missing") & (pool["has_children"] == "all")].iloc[0]
    assert missing["n_donors"] == 1 and missing["n_mobile"] == 0
