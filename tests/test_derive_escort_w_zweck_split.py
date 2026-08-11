"""Pinned MiD escort W_ZWECK active/passive split (#256)."""
import pandas as pd
import pytest

from scripts.derive_escort_w_zweck_split import derive_split, BAND_COLUMNS


def _wege():
    # 3 active legs (code 6, weight 2 each) at 1 km; 2 passive legs (code 13,
    # weight 3 each) at 10 km; 1 non-escort leg that must be ignored.
    return pd.DataFrame({
        "W_ZWECK":   [6, 6, 6, 13, 13, 4],
        "W_GEW":     [2.0, 2.0, 2.0, 3.0, 3.0, 9.0],
        "wegkm_imp": [1.0, 1.0, 1.0, 10.0, 10.0, 5.0],
    })


def test_split_shares_are_weighted():
    table, stats = derive_split(_wege())
    t = table.set_index("w_zweck")
    assert stats["n_escort_legs"] == 5
    # weights: active 6.0, passive 6.0 -> 50/50
    assert t.loc["code_6", "share_weighted"] == pytest.approx(0.5)
    assert t.loc["code_13", "share_weighted"] == pytest.approx(0.5)
    assert t.loc["both", "share_weighted"] == pytest.approx(1.0)


def test_split_lengths_and_bands():
    table, _ = derive_split(_wege())
    t = table.set_index("w_zweck")
    assert t.loc["code_6", "median_km"] == pytest.approx(1.0)
    assert t.loc["code_13", "median_km"] == pytest.approx(10.0)
    assert t.loc["code_6", "mean_km"] == pytest.approx(1.0)
    # bands are row-percentages: code 6 fully in 1-2 km, code 13 fully in 10-20 km
    assert t.loc["code_6", "d_1_2km"] == pytest.approx(100.0)
    assert t.loc["code_13", "d_10_20km"] == pytest.approx(100.0)
    assert set(BAND_COLUMNS) == {
        "d_unter_0_5km", "d_0_5_1km", "d_1_2km", "d_2_5km", "d_5_10km",
        "d_10_20km", "d_20_50km", "d_50_100km", "d_100km_plus"}


def test_split_raises_without_escort_legs():
    df = _wege()
    df = df[df["W_ZWECK"] == 4]
    with pytest.raises(ValueError, match="no escort legs"):
        derive_split(df)


def test_split_median_argument_order():
    """Detect if weighted_median arguments are swapped (values, weights order).

    code_6: [1.0, 2.0, 9.0] km with W_GEW [1.0, 1.0, 10.0]
    -> cumulative weights [1, 2, 12], half=6, weighted median = 9.0.
    If (values, weights) arguments are swapped in weighted_median call,
    median_km would be incorrect.
    """
    df = pd.DataFrame({
        "W_ZWECK":   [6, 6, 6, 13],
        "W_GEW":     [1.0, 1.0, 10.0, 5.0],
        "wegkm_imp": [1.0, 2.0, 9.0, 5.0],
    })
    table, _ = derive_split(df)
    t = table.set_index("w_zweck")
    assert t.loc["code_6", "median_km"] == pytest.approx(9.0)
