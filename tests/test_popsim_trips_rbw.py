"""Tests for dropping rbW legs and leading arrive-home legs from the popsim trip table.

MiD Wege carry W_RBW (1 = regelmaessiger beruflicher Weg -- a regular-commuter
summary record standing in for the diary leg, see time_imputation.py's module
docstring for the audited rbW background) and W_SO1 (start location of the
FIRST trip: 1 home, 2 elsewhere, 809 = not asked from the 2nd trip on). Both
flags are OFF by default so every existing caller stays byte-identical.
"""

import logging

import pandas as pd

from braunschweig.popsim import trips as T


def _persons():
    return pd.DataFrame({"person_id": [0, 1, 2], "H_ID": [1, 1, 2], "P_ID": [1, 2, 1],
                         "source_H_ID": [1, 1, 2], "source_P_ID": [1, 2, 1]})


def _wege():
    base = dict(hvm_imp=3, W_SZS=8, W_SZM=0, W_AZS=8, W_AZM=30, wegkm_imp=5.0, wegmin_imp1=30)
    rows = [
        # person (1,1): H->work (direct), work->home (direct), rbW, rbW
        dict(H_ID=1, P_ID=1, W_ID=1, W_ZWECK=1, W_RBW=0, W_SO1=1, **base),
        dict(H_ID=1, P_ID=1, W_ID=2, W_ZWECK=8, W_RBW=0, W_SO1=809, **{**base, "W_SZS": 17, "W_AZS": 17}),
        dict(H_ID=1, P_ID=1, W_ID=3, W_ZWECK=2, W_RBW=1, W_SO1=701, **{**base, "W_SZS": 701, "W_SZM": 701, "W_AZS": 701, "W_AZM": 701}),
        dict(H_ID=1, P_ID=1, W_ID=4, W_ZWECK=2, W_RBW=1, W_SO1=701, **{**base, "W_SZS": 701, "W_SZM": 701, "W_AZS": 701, "W_AZM": 701}),
        # person (1,2): arrives home first (from elsewhere), then shop
        dict(H_ID=1, P_ID=2, W_ID=1, W_ZWECK=8, W_RBW=0, W_SO1=2, **{**base, "W_SZS": 6, "W_AZS": 6}),
        dict(H_ID=1, P_ID=2, W_ID=2, W_ZWECK=4, W_RBW=0, W_SO1=809, **{**base, "W_SZS": 10, "W_AZS": 10}),
        # person (2,1): only rbW
        dict(H_ID=2, P_ID=1, W_ID=1, W_ZWECK=2, W_RBW=1, W_SO1=701, **{**base, "W_SZS": 701, "W_SZM": 701, "W_AZS": 701, "W_AZM": 701}),
    ]
    return pd.DataFrame(rows)


def test_default_keeps_every_leg():
    out = T.expand_persons_to_trips(_persons(), _wege())
    assert len(out) == 7


def test_exclude_rbw_legs_drops_them_and_empties_only_rbw_person(caplog):
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.trips"):
        out = T.expand_persons_to_trips(_persons(), _wege(), exclude_rbw_legs=True)
    assert len(out) == 4 and set(out["person_id"]) == {0, 1}
    assert "rbW legs dropped: 3/7" in caplog.text
    # Donor (2,1) IS referenced by person 2, so it counts as emptied and warns.
    assert "referenced donor persons emptied by the drop: 1/3" in caplog.text
    assert any(r.levelno == logging.WARNING and "ONLY rbW legs" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# Controller ruling R21: the "emptied by the drop" counters are about PLANS, so
# their universe is the donors the synthetic persons actually source from. Over
# the WHOLE MiD Wege table the warning fired on every production run (the raw
# file always contains only-rbW and leading-arrive-home donors nobody uses),
# which made a real signal indistinguishable from the permanent noise.
# ---------------------------------------------------------------------------

def test_emptied_counters_ignore_donors_no_person_references(caplog):
    # Only persons 0 and 1 exist; donor (2,1) (only rbW) and a second unreferenced
    # donor (3,1) (a single leading arrive-home leg) stay in the Wege table.
    persons = _persons().iloc[:2].copy()
    wege = pd.concat([_wege(), pd.DataFrame([dict(
        H_ID=3, P_ID=1, W_ID=1, W_ZWECK=8, W_RBW=0, W_SO1=2, hvm_imp=3,
        W_SZS=6, W_SZM=0, W_AZS=6, W_AZM=30, wegkm_imp=5.0, wegmin_imp1=30)])],
        ignore_index=True)

    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.trips"):
        out = T.expand_persons_to_trips(persons, wege, exclude_rbw_legs=True,
                                        drop_leading_arrive_home_leg=True)

    assert set(out["person_id"]) == {0, 1}
    # Both drops still happen and are still counted at leg level ...
    assert "rbW legs dropped: 3/8" in caplog.text
    # ... but no REFERENCED donor is emptied, so no warning at all.
    assert "referenced donor persons emptied by the drop: 0/2" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


def test_drop_leading_arrive_home_leg_only_when_first_leg_arrives_home():
    out = T.expand_persons_to_trips(_persons(), _wege(), drop_leading_arrive_home_leg=True)
    p1 = out[out["person_id"] == 1]
    assert len(p1) == 1 and p1["purpose"].iloc[0] == "shop"
    assert len(out[out["person_id"] == 0]) == 4


def test_build_trip_table_threads_both_flags():
    out = T.build_trip_table(_persons(), _wege(), exclude_rbw_legs=True, drop_leading_arrive_home_leg=True)
    assert set(out["person_id"]) == {0, 1} and len(out) == 3
    first = out[out["person_id"] == 1].sort_values("trip_index").iloc[0]
    assert first["preceding_purpose"] == "home" and first["following_purpose"] == "shop"
