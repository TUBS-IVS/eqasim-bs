"""trip_class seed counts the closed day; 803/804 guard (issues #365, #367).

Task 7 of the plan-structure-fix plan: with ``counts_closure=True``, a plan
source that carries a realised (non-diary-code) trip count AND does not end
at home (``src_ends_at_home == False``) gets its seed trip count incremented
by one -- the synthetic return-home leg the completed-donor plan will carry
after trip-chain closure (spec 2026-09-05-plan-structure-fix-design.md). The
803/804 diary non-response codes stay codes (imputed exactly as before);
``forbid_no_diary_sources=True`` raises when a resolved source's anzwege1 is
still 803/804, which signals the completed_donor build failed to remap it
even though diary_plan_match is on (see
braunschweig.popsim.mid.participation.derive_trip_class_seed).
"""
import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim.mid import participation as P


def _persons():
    return pd.DataFrame({
        "H_ID": [1, 2, 3], "P_ID": [1, 1, 1], "member_imputed": [False] * 3,
        "source_H_ID": [1, 2, 3], "source_P_ID": [1, 1, 1],
        "anzwege1": [2, 4, 803], "alter_gr1": [3, 3, 3],
        "src_ends_at_home": [False, True, False], "src_n_direct_legs": [2, 4, 0],
    })


def test_counts_closure_adds_one_for_open_ended_sources():
    out = P.derive_trip_class_seed(_persons(), rng=np.random.RandomState(0), counts_closure=True)
    assert out.loc[0, "trip_class"] == 2      # 2 + 1 = 3 trips -> class 2 (3-4)
    assert out.loc[1, "trip_class"] == 2      # 4 trips, ends at home -> unchanged class 2
    assert out.loc[2, "trip_class"] in (0, 1, 2, 3)   # 803 imputed, never crashes


def test_counts_closure_off_is_previous_behaviour():
    a = P.derive_trip_class_seed(_persons(), rng=np.random.RandomState(0))
    b = P.derive_trip_class_seed(_persons().drop(columns=["src_ends_at_home", "src_n_direct_legs"]), rng=np.random.RandomState(0))
    pd.testing.assert_series_equal(a["trip_class"], b["trip_class"])
    assert a.loc[0, "trip_class"] == 1        # 2 trips -> class 1


def test_forbid_no_diary_sources_raises():
    with pytest.raises(ValueError, match="803"):
        P.derive_trip_class_seed(_persons(), rng=np.random.RandomState(0), forbid_no_diary_sources=True)


def test_counts_closure_requires_fact_columns():
    with pytest.raises(KeyError, match="src_ends_at_home"):
        P.derive_trip_class_seed(_persons().drop(columns=["src_ends_at_home"]), rng=np.random.RandomState(0), counts_closure=True)
