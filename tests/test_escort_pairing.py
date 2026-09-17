"""Tests for pairing MiD passive escort legs (W_ZWECK 13) with the accompanying adult's leg.

Tiny synthetic households only; see braunschweig/popsim/escort_pairing.py for the pairing
rule and its provenance (issue #372).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import escort_pairing as EP


def _wege():
    # household 1: mother (P 1, 35) shop 8:00 + home 9:00; child (P 2, 4) code 13 at 8:00 and 9:02; father (P 3, 40) work 7:00
    # household 2: child alone (P 1, 9) code 13 at 7:30, no adult leg -> no adult
    # household 3: child (P 2, 5) code 13 at 10:00, adult (P 1, 30) leisure at 11:00 -> gap 60 > 15
    # household 4: child (P 2, 6) code 13 with coded time 99 -> no time
    return pd.DataFrame({
        "H_ID":    [1, 1, 1, 1, 1, 2, 3, 3, 4, 4],
        "P_ID":    [1, 1, 3, 2, 2, 1, 1, 2, 1, 2],
        "W_ID":    [1, 2, 1, 1, 2, 1, 1, 1, 1, 1],
        "W_ZWECK": [4, 8, 1, 13, 13, 13, 7, 13, 7, 13],
        "W_SZS":   [8, 9, 7, 8, 9, 7, 11, 10, 8, 99],
        "W_SZM":   [0, 0, 0, 0, 2, 30, 0, 0, 0, 99],
        "HP_ALTER": [35, 35, 40, 4, 4, 9, 30, 5, 30, 6],
        "wegkm_imp": [2.0, 2.0, 15.0, 2.0, 2.0, 1.0, 3.0, 3.0, 2.0, 2.0],
    })


def test_pairs_to_the_nearest_adult_leg_and_reports_status():
    out, diag = EP.pair_passive_legs(_wege())
    child = out[(out["H_ID"] == 1) & (out["P_ID"] == 2)].sort_values("W_ID")
    assert child["passive_pair_status"].tolist() == [EP.STATUS_PAIRED, EP.STATUS_PAIRED]
    assert child["passive_pair_adult_w_zweck"].tolist() == [4, 8]          # shop at 8:00, home at 9:00 (gap 2 min)
    assert child["passive_pair_adult_p_id"].tolist() == [1, 1]
    assert child["passive_pair_gap_minutes"].tolist() == [0.0, 2.0]
    assert out.loc[(out["H_ID"] == 2), "passive_pair_status"].item() == EP.STATUS_UNPAIRED_NO_ADULT
    assert out.loc[(out["H_ID"] == 3) & (out["W_ZWECK"] == 13), "passive_pair_status"].item() == EP.STATUS_UNPAIRED_GAP
    assert out.loc[(out["H_ID"] == 4) & (out["W_ZWECK"] == 13), "passive_pair_status"].item() == EP.STATUS_UNPAIRED_NO_TIME
    assert diag["n_passive"] == 5 and diag["n_paired"] == 2 and diag["adult_w_zweck_counts"] == {4: 1, 8: 1}
    assert diag["n_unpaired_no_adult"] == 1 and diag["n_unpaired_gap"] == 1 and diag["n_unpaired_no_time"] == 1
    assert diag["n_paired"] + diag["n_unpaired_no_adult"] + diag["n_unpaired_gap"] + diag["n_unpaired_no_time"] == diag["n_passive"]


def test_non_passive_legs_carry_no_pairing_and_the_frame_length_is_unchanged():
    wege = _wege(); out, _ = EP.pair_passive_legs(wege)
    assert len(out) == len(wege) and out.loc[out["W_ZWECK"] != 13, "passive_pair_status"].isna().all()
    assert list(out.columns[:len(wege.columns)]) == list(wege.columns)


def test_same_minute_tie_prefers_the_adult_with_the_same_distance_then_lowest_p_id():
    wege = _wege()
    # add a second adult in household 1 leaving at 8:00 with a different distance and a lower P_ID than the mother? mother is P 1 -> add P 4 with km 9.0
    extra = pd.DataFrame({"H_ID": [1], "P_ID": [4], "W_ID": [1], "W_ZWECK": [7], "W_SZS": [8], "W_SZM": [0], "HP_ALTER": [70], "wegkm_imp": [9.0]})
    out, _ = EP.pair_passive_legs(pd.concat([wege, extra], ignore_index=True))
    first = out[(out["H_ID"] == 1) & (out["P_ID"] == 2) & (out["W_ID"] == 1)]
    assert first["passive_pair_adult_p_id"].item() == 1                    # same km 2.0 wins over km 9.0


def test_tie_break_level_three_lowest_adult_p_id_wins_when_gap_and_distance_both_tie():
    # Two DIFFERENT adults both depart at the same minute as the child AND both match the
    # child's wegkm_imp exactly, so tie-break levels 1 (gap) and 2 (distance) both stay tied.
    # Level 3 (lowest adult P_ID) must then decide. The winning adult (P_ID 1) is given a
    # HIGHER W_ID (9) than the losing adult (P_ID 2, W_ID 1), to prove P_ID is compared BEFORE
    # W_ID, not that W_ID alone happens to pick the right answer.
    wege = pd.DataFrame({
        "H_ID":     [9, 9, 9],
        "P_ID":     [3, 1, 2],
        "W_ID":     [1, 9, 1],
        "W_ZWECK":  [13, 7, 8],
        "W_SZS":    [8, 8, 8],
        "W_SZM":    [0, 0, 0],
        "HP_ALTER": [6, 40, 35],
        "wegkm_imp": [5.0, 5.0, 5.0],
    })
    out, _ = EP.pair_passive_legs(wege)
    row = out[(out["H_ID"] == 9) & (out["P_ID"] == 3)]
    assert row["passive_pair_adult_p_id"].item() == 1
    assert row["passive_pair_adult_w_id"].item() == 9


def test_tie_break_level_four_lowest_adult_w_id_wins_for_the_same_adults_own_tied_legs():
    # A single adult (P_ID 1) has TWO legs, both at the child's departure minute and both
    # matching the child's wegkm_imp -- gap, distance-match, and (trivially, same person) adult
    # P_ID all tie, so level 4 (lowest adult W_ID) must decide between the adult's own legs.
    wege = pd.DataFrame({
        "H_ID":     [10, 10, 10],
        "P_ID":     [2, 1, 1],
        "W_ID":     [1, 5, 2],
        "W_ZWECK":  [13, 7, 8],
        "W_SZS":    [8, 8, 8],
        "W_SZM":    [0, 0, 0],
        "HP_ALTER": [6, 40, 40],
        "wegkm_imp": [3.0, 3.0, 3.0],
    })
    out, _ = EP.pair_passive_legs(wege)
    row = out[(out["H_ID"] == 10) & (out["P_ID"] == 2)]
    assert row["passive_pair_adult_w_id"].item() == 2


def test_sole_household_adult_with_a_passive_leg_and_no_other_adult_is_no_adult():
    # Critical regression: a single-occupant household where the passive person is themselves
    # old enough to count as an "adult" by age. Before the fix, the self-match filter removed
    # the ONLY merged candidate row, the leg vanished from the candidate set entirely, and it
    # silently kept the initial STATUS_UNPAIRED_NO_TIME default even though its own departure
    # time (10:00) is perfectly valid. There is no OTHER eligible adult in the household, so
    # the correct status is UNPAIRED_NO_ADULT.
    wege = pd.DataFrame({
        "H_ID": [5], "P_ID": [1], "W_ID": [1], "W_ZWECK": [13],
        "W_SZS": [10], "W_SZM": [0], "HP_ALTER": [20], "wegkm_imp": [3.0],
    })
    out, diag = EP.pair_passive_legs(wege)
    assert out["passive_pair_status"].item() == EP.STATUS_UNPAIRED_NO_ADULT
    assert diag["n_passive"] == 1 and diag["n_unpaired_no_adult"] == 1 and diag["n_paired"] == 0


def test_lone_adult_with_a_second_leg_of_their_own_is_still_no_adult_for_their_own_passive_leg():
    # Deeper variant of the Critical regression above ("one adult plus minors" in the review):
    # the household's only age-eligible person (P_ID 1, age 20) has TWO legs -- one passive
    # (code 13, being escorted) and one ordinary leg 5 minutes later. That ordinary leg alone
    # would qualify as an adult candidate, but it belongs to the SAME person as the passive leg,
    # so it must be excluded as a self-match; with no other household member, the correct
    # status is still UNPAIRED_NO_ADULT, not a self-pairing and not a fall-through to no-time.
    wege = pd.DataFrame({
        "H_ID":     [11, 11],
        "P_ID":     [1, 1],
        "W_ID":     [1, 2],
        "W_ZWECK":  [13, 7],
        "W_SZS":    [10, 10],
        "W_SZM":    [0, 5],
        "HP_ALTER": [20, 20],
        "wegkm_imp": [3.0, 3.0],
    })
    out, diag = EP.pair_passive_legs(wege)
    passive_row = out[out["W_ZWECK"] == 13]
    assert passive_row["passive_pair_status"].item() == EP.STATUS_UNPAIRED_NO_ADULT
    assert diag["n_unpaired_no_adult"] == 1 and diag["n_paired"] == 0


def test_two_passive_legs_in_the_same_household_never_pair_with_each_other():
    # Ruling C-R8: a person who is themselves passively escorted on a leg cannot be counted as
    # the escorting adult for that leg (code 13 is not a real destination purpose either). With
    # no OTHER eligible adult in the household, both legs must end up UNPAIRED_NO_ADULT, and
    # PASSIVE_W_ZWECK (13) must never appear in adult_w_zweck_counts.
    wege = pd.DataFrame({
        "H_ID":     [7, 7],
        "P_ID":     [1, 2],
        "W_ID":     [1, 1],
        "W_ZWECK":  [13, 13],
        "W_SZS":    [9, 9],
        "W_SZM":    [0, 0],
        "HP_ALTER": [20, 25],
        "wegkm_imp": [2.0, 2.5],
    })
    out, diag = EP.pair_passive_legs(wege)
    assert (out["passive_pair_status"] == EP.STATUS_UNPAIRED_NO_ADULT).all()
    assert 13 not in diag["adult_w_zweck_counts"]


def test_max_gap_is_configurable_and_low_pairing_warns(caplog):
    out, diag = EP.pair_passive_legs(_wege(), max_gap_minutes=90)
    assert out.loc[(out["H_ID"] == 3) & (out["W_ZWECK"] == 13), "passive_pair_status"].item() == EP.STATUS_PAIRED
    with caplog.at_level("WARNING"):
        EP.pair_passive_legs(_wege().assign(HP_ALTER=5))                  # no adults at all -> 0 % paired
    assert "paired" in caplog.text


def test_missing_required_column_raises():
    with pytest.raises(KeyError, match="HP_ALTER"):
        EP.pair_passive_legs(_wege().drop(columns=["HP_ALTER"]))
