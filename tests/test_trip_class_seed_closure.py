"""trip_class seed counts the closed day; 803/804 guard (issues #365, #367).

Task 7 of the plan-structure-fix plan: with ``counts_closure=True``, a plan
source that carries a realised (non-diary-code) trip count AND does not end
at home (``src_ends_at_home == False``) gets its seed trip count incremented
by one -- the synthetic return-home leg the completed-donor plan will carry
after trip-chain closure (spec 2026-09-05-plan-structure-fix-design.md). The
803/804 diary non-response codes stay codes (imputed exactly as before);
``forbid_no_diary_sources=True`` raises when a resolved source is one the
diary plan match PROMISES to have remapped -- anzwege1 804, or 803 with the
source's mobil == 1 -- which signals the completed_donor build failed to remap
it even though diary_plan_match is on. A source with anzwege1 803 and
mobil != 1 is KEPT by diary_plan_match by design (REASON_KEEP_IMMOBILE: zero
trips IS the observed day) and must NOT raise; it is seeded as 0 trips instead
of being age-band imputed (controller ruling R18, see
braunschweig.popsim.mid.participation.derive_trip_class_seed).
"""
import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim.mid import participation as P


def _persons(mobil=(1, 1, 1)):
    """Three persons sourcing their own diary; the third carries the 803 code.

    ``mobil`` is the SOURCE's MiD mobility flag, which decides whether the 803
    code is one diary_plan_match promises to remap (mobil == 1) or one it keeps
    by design (mobil != 1).
    """
    return pd.DataFrame({
        "H_ID": [1, 2, 3], "P_ID": [1, 1, 1], "member_imputed": [False] * 3,
        "source_H_ID": [1, 2, 3], "source_P_ID": [1, 1, 1],
        "anzwege1": [2, 4, 803], "alter_gr1": [3, 3, 3], "mobil": list(mobil),
        "src_ends_at_home": [False, True, False], "src_n_direct_legs": [2, 4, 0],
        "src_starts_arriving_home": [False, False, False],
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


def test_forbid_no_diary_sources_raises_for_mobile_803_source():
    # 803 with mobil == 1 is a source diary_plan_match PROMISES to remap; surviving
    # here means the remap did not run (a flag-wiring defect) -> fail loudly.
    with pytest.raises(ValueError, match="803"):
        P.derive_trip_class_seed(_persons(mobil=(1, 1, 1)), rng=np.random.RandomState(0),
                                 forbid_no_diary_sources=True)


def test_forbid_no_diary_sources_raises_for_804_source():
    persons = _persons(mobil=(1, 1, 0))
    persons.loc[2, "anzwege1"] = 804      # "Mobilitaet unbekannt" is remapped regardless of mobil
    with pytest.raises(ValueError, match="804"):
        P.derive_trip_class_seed(persons, rng=np.random.RandomState(0),
                                 forbid_no_diary_sources=True)


def test_forbid_no_diary_sources_keeps_immobile_803_source(caplog):
    """Ruling R18: 803 with mobil != 1 is KEPT by diary_plan_match by design.

    The guard must therefore not raise on it, and the seed must count the day the
    (empty) plan will realise -- zero trips, class 0 -- instead of imputing the code
    within the age band, whose pool here (2 and 4 trips) contains no zero at all.
    """
    with caplog.at_level("INFO"):
        out = P.derive_trip_class_seed(_persons(mobil=(1, 1, 0)), rng=np.random.RandomState(0),
                                       forbid_no_diary_sources=True)
    assert out.loc[2, "trip_class"] == 0
    assert out.loc[0, "trip_class"] == 1 and out.loc[1, "trip_class"] == 2   # unaffected
    assert "kept immobile by diary_plan_match" in caplog.text


def test_forbid_no_diary_sources_requires_mobil_column():
    # No weaker guard when the column that decides "kept" vs "must have been remapped"
    # is absent: fail fast, naming the column.
    with pytest.raises(KeyError, match="mobil"):
        P.derive_trip_class_seed(_persons().drop(columns=["mobil"]),
                                 rng=np.random.RandomState(0), forbid_no_diary_sources=True)


def test_counts_closure_subtracts_dropped_leading_arrive_home_leg():
    """Ruling R20: the seed must not count a leg the trip build drops.

    Person 0's source starts by ARRIVING home (src_starts_arriving_home) and does not
    end at home, so it gets +1 for the synthetic closure and -1 for the dropped leading
    leg: 2 trips stay 2 trips -> class 1 (1-2), not class 2 (3-4).
    """
    persons = _persons()
    persons.loc[0, "src_starts_arriving_home"] = True
    out = P.derive_trip_class_seed(persons, rng=np.random.RandomState(0), counts_closure=True,
                                   drop_leading_arrive_home_leg=True)
    assert out.loc[0, "trip_class"] == 1
    # Without the flag the same frame keeps the +1 only -> 3 trips -> class 2.
    out_off = P.derive_trip_class_seed(persons, rng=np.random.RandomState(0), counts_closure=True)
    assert out_off.loc[0, "trip_class"] == 2


def test_drop_leading_arrive_home_leg_never_below_zero():
    # A single-leg arriving-home diary that also "does not end at home" would be
    # 1 + 1 - 1 = 1; a source with no direct legs at all gets neither term and stays 0.
    persons = pd.DataFrame({
        "H_ID": [1, 2], "P_ID": [1, 1], "member_imputed": [False, False],
        "source_H_ID": [1, 2], "source_P_ID": [1, 1],
        "anzwege1": [0, 1], "alter_gr1": [3, 3], "mobil": [1, 1],
        "src_ends_at_home": [True, False], "src_n_direct_legs": [0, 1],
        "src_starts_arriving_home": [True, True],
    })
    out = P.derive_trip_class_seed(persons, rng=np.random.RandomState(0), counts_closure=True,
                                   drop_leading_arrive_home_leg=True)
    assert out.loc[0, "trip_class"] == 0      # 0 - 1 floored at 0
    assert out.loc[1, "trip_class"] == 1      # 1 + 1 - 1 = 1 trip


def test_drop_leading_arrive_home_leg_requires_the_fact_column():
    with pytest.raises(KeyError, match="src_starts_arriving_home"):
        P.derive_trip_class_seed(_persons().drop(columns=["src_starts_arriving_home"]),
                                 rng=np.random.RandomState(0), counts_closure=True,
                                 drop_leading_arrive_home_leg=True)


def test_counts_closure_requires_fact_columns():
    with pytest.raises(KeyError, match="src_ends_at_home"):
        P.derive_trip_class_seed(_persons().drop(columns=["src_ends_at_home"]), rng=np.random.RandomState(0), counts_closure=True)


def test_counts_closure_clips_at_50_no_exception():
    # Controller ruling R11: attributes.map_trip_class's value_map only enumerates
    # anzwege1 0..50 (the MiD codebook's valid range); an anzwege1==50 source with an
    # open-ended diary must be clipped at 50 (not incremented to the unenumerated 51,
    # which missing.resolve would raise on). 51 falls in the same 5+ class as 50, so
    # the classification is unaffected.
    persons = pd.DataFrame({
        "H_ID": [1], "P_ID": [1], "member_imputed": [False],
        "source_H_ID": [1], "source_P_ID": [1],
        "anzwege1": [50], "alter_gr1": [3], "mobil": [1],
        "src_ends_at_home": [False], "src_n_direct_legs": [12],
    })
    out = P.derive_trip_class_seed(persons, rng=np.random.RandomState(0), counts_closure=True)
    assert out.loc[0, "trip_class"] == 3


def test_counts_closure_zero_direct_legs_not_incremented():
    # A source with zero direct legs (e.g. an all-rbW diary already reduced to 0 legs)
    # never qualifies for the +1, regardless of src_ends_at_home -- guards against the
    # closure incorrectly firing on a source with no realised trip to close.
    persons = pd.DataFrame({
        "H_ID": [1], "P_ID": [1], "member_imputed": [False],
        "source_H_ID": [1], "source_P_ID": [1],
        "anzwege1": [0], "alter_gr1": [3], "mobil": [1],
        "src_ends_at_home": [False], "src_n_direct_legs": [0],
    })
    out = P.derive_trip_class_seed(persons, rng=np.random.RandomState(0), counts_closure=True)
    assert out.loc[0, "trip_class"] == 0
