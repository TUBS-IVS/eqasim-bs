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


def test_split_raises_when_total_weight_is_zero_after_coercion():
    # D11 (deferred review): all W_GEW values are non-numeric garbage -> every
    # one coerces to NaN -> fillna(0.0) -> total_weight == 0. Must raise
    # BEFORE any share is computed, not silently divide by zero into NaN
    # shares (CLAUDE.md "no silent fallbacks").
    df = pd.DataFrame({
        "W_ZWECK":   [6, 6, 13],
        "W_GEW":     ["abc", "xyz", "??"],
        "wegkm_imp": [1.0, 2.0, 3.0],
    })
    with pytest.raises(ValueError, match="total escort weight is zero"):
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


# ---------------------------------------------------------------------------
# Issue #372 task 4: the share of code-13 legs that STAY education once the pairing
# gives a paired child the accompanying adult's purpose (ADR-0112).
# ---------------------------------------------------------------------------

def _pairing_wege():
    """One household, three passive legs, one adult per case.

    P_ID 2 (age 5) has three code-13 legs: 08:00 pairs with the adult's ACTIVE escort leg
    (W_ZWECK 6 -> stays education), 12:00 pairs with the adult's SHOP leg (-> shop), 20:00
    pairs with nothing within the window (-> stays education by the passive rule).
    """
    return pd.DataFrame({
        "H_ID":      [1, 1, 1, 1, 1],
        "P_ID":      [1, 1, 2, 2, 2],
        "W_ID":      [1, 2, 1, 2, 3],
        "W_ZWECK":   [6, 4, 13, 13, 13],
        "W_GEW":     [1.0, 1.0, 2.0, 1.0, 1.0],
        "wegkm_imp": [1.0, 1.0, 1.0, 1.0, 1.0],
        "W_SZS":     [8, 12, 8, 12, 20],
        "W_SZM":     [0, 0, 0, 0, 0],
        "HP_ALTER":  [35, 35, 5, 5, 5],
        # The two TRIP-BUILD leg filters read these; every fixture leg is a direct diary leg of
        # a diary that starts at home, so both filters drop nothing here (fix round 1).
        "W_RBW":     [0, 0, 0, 0, 0],
        "W_SO1":     [1, 809, 1, 809, 809],
    })


def test_passive_education_share_counts_active_escort_pairs_and_unpaired_legs():
    from scripts.derive_escort_w_zweck_split import derive_passive_education_share
    share, stats = derive_passive_education_share(_pairing_wege())
    # passive weight 4.0: 2.0 paired to the ACTIVE escort leg, 1.0 paired to shop, 1.0 unpaired.
    assert stats["n_passive"] == 3 and stats["n_paired"] == 2
    assert stats["n_passive_raw"] == 3          # nothing dropped by the two leg filters here
    assert stats["share_paired_to_active_escort"] == pytest.approx(0.5)
    assert stats["share_unpaired"] == pytest.approx(0.25)
    assert share == pytest.approx(0.75)


def test_passive_purpose_fold_sums_to_one_and_names_every_destination():
    """Fix round 1, IMPORTANT 1 (ruling C-R11): the committed reference must carry the FULL
    fold, not only the education member, so trip_coherence can move the W1 mass instead of
    dropping it."""
    from scripts.derive_escort_w_zweck_split import (
        derive_passive_education_share, passive_fold_purposes)
    _share, stats = derive_passive_education_share(_pairing_wege())
    fold = stats["fold"]
    assert set(fold) == set(passive_fold_purposes())
    assert sum(fold.values()) == pytest.approx(1.0)
    assert fold["education"] == pytest.approx(0.75)   # 0.5 active-escort pair + 0.25 unpaired
    assert fold["shop"] == pytest.approx(0.25)        # the 12:00 leg pairs with the adult's shop leg
    assert all(fold[purpose] == pytest.approx(0.0)
               for purpose in fold if purpose not in {"education", "shop"})


def test_passive_education_share_pairs_on_the_trip_builds_leg_universe():
    """Fix round 1, IMPORTANT 3: an rbW summary leg that would win the pairing on the raw table
    must not, because the trip build drops it before map_purpose pairs."""
    from scripts.derive_escort_w_zweck_split import derive_passive_education_share
    wege = _pairing_wege()
    # Turn the adult's ACTIVE escort leg (08:00, the one the first child leg pairs with) into an
    # rbW summary record: with the filter ON it disappears and the child is left unpaired
    # (education by the passive rule); with the filter OFF it still wins and gives education too,
    # so make the difference visible through n_paired.
    wege.loc[(wege["P_ID"] == 1) & (wege["W_ID"] == 1), "W_RBW"] = 1
    filtered_share, filtered = derive_passive_education_share(wege, exclude_rbw_legs=True)
    raw_share, raw = derive_passive_education_share(wege, exclude_rbw_legs=False,
                                                    drop_leading_arrive_home_leg=False)
    assert filtered["n_paired"] == 1 and raw["n_paired"] == 2
    assert filtered["share_unpaired"] == pytest.approx(0.75)
    assert raw["share_unpaired"] == pytest.approx(0.25)
    assert filtered_share == pytest.approx(raw_share)   # both routes still read as education here


def test_passive_education_share_raises_when_a_pairing_column_is_missing():
    """No silent skip: without HP_ALTER nothing pairs and the share would read 1.0 --
    today's flat education relabel dressed up as a measurement."""
    from scripts.derive_escort_w_zweck_split import derive_passive_education_share
    with pytest.raises(KeyError, match="HP_ALTER"):
        derive_passive_education_share(_pairing_wege().drop(columns=["HP_ALTER"]))
