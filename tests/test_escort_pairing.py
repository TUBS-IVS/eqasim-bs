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


def test_max_gap_is_configurable_and_low_pairing_warns(caplog):
    out, diag = EP.pair_passive_legs(_wege(), max_gap_minutes=90)
    assert out.loc[(out["H_ID"] == 3) & (out["W_ZWECK"] == 13), "passive_pair_status"].item() == EP.STATUS_PAIRED
    with caplog.at_level("WARNING"):
        EP.pair_passive_legs(_wege().assign(HP_ALTER=5))                  # no adults at all -> 0 % paired
    assert "paired" in caplog.text


def test_missing_required_column_raises():
    with pytest.raises(KeyError, match="HP_ALTER"):
        EP.pair_passive_legs(_wege().drop(columns=["HP_ALTER"]))
