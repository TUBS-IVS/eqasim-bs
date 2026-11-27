"""Integration test: weekend_plan_match wired into the donor-build seam.

Verifies that after ``reassign_weekend_plan_sources`` a weekend person's
``source_H_ID``/``source_P_ID`` is remapped to the weekday donor, AND that
``trips.build_trip_table`` therefore gives the weekend person a trip row via
the source-id join.

The plan (Task 7 Step 1) specifies a full ``build_trip_table`` path if feasible,
or a direct remap + source-id join assertion if end-to-end is too brittle.
We go the full path: the Wege frame carries the MiD time columns
(W_SZS/W_SZM/W_AZS/W_AZM) that ``build_trip_table`` requires.  Columns are
chosen to be minimally valid: single trip, all times in the valid clock range,
no midnight crossing.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from braunschweig.popsim import weekend_plan_match as wpm
from braunschweig.popsim import trips as trips_mod


def _make_households():
    """One weekday donor HH (id=1, size=1) and one weekend HH (id=2, size=1)."""
    return pd.DataFrame({
        "H_ID": [1, 2],
        "H_GR": [1, 1],
        "hh_type5": ["single", "single"],
        "oek_status": [2, 2],
        "RegioStaR7": [71, 71],
        "H_ANZAUTO": [1, 1],
        "H_GEW": [1.0, 1.0],
    })


def _make_persons():
    """Weekday person (H_ID=1, kernwo=2) and weekend person (H_ID=2, kernwo=6)."""
    return pd.DataFrame({
        "H_ID": [1, 2],
        "P_ID": [1, 1],
        "HP_ALTER": [40, 41],
        "HP_SEX": [1, 1],
        "P_FSCHEIN": [1, 1],
        "P_TAET": [1, 1],
        "P_FKARTE": [1, 1],
        "kernwo": [2, 6],
        "source_H_ID": [1, 2],
        "source_P_ID": [1, 1],
        "member_imputed": [False, False],
        "person_id": ["c_1_1", "c_2_1"],
        "P_GEW": [1.0, 1.0],
    })


def _make_wege():
    """Minimal valid MiD Wege row for the weekday donor (H_ID=1, P_ID=1).

    Columns required by ``build_trip_table``/``expand_persons_to_trips``:
    H_ID, P_ID, W_ID, W_ZWECK, hvm_imp  (for purpose + mode mapping)
    W_SZS, W_SZM, W_AZS, W_AZM          (for time conversion in mid_time_seconds)

    One trip: depart 8:00, arrive 8:30, purpose=work (W_ZWECK=1), mode=car (hvm_imp=4).
    """
    return pd.DataFrame({
        "H_ID": [1],
        "P_ID": [1],
        "W_ID": [1],
        "W_ZWECK": [1],   # Arbeit -> "work"
        "hvm_imp": [4],   # MIV-Fahrer -> "car"
        "W_SZS": [8],     # departure hour
        "W_SZM": [0],     # departure minute
        "W_AZS": [8],     # arrival hour
        "W_AZM": [30],    # arrival minute
    })


def test_weekend_person_source_remapped_to_weekday_donor():
    """After reassign, the weekend person's source_H_ID points at the weekday HH."""
    households = _make_households()
    persons = _make_persons()

    out, trace, report = wpm.reassign_weekend_plan_sources(
        households, persons, rng=np.random.RandomState(0)
    )

    # weekday person (H_ID=1) is untouched
    wd_row = out[out["H_ID"] == 1].iloc[0]
    assert wd_row["source_H_ID"] == 1

    # weekend person (H_ID=2) is now sourced from weekday HH 1
    we_row = out[out["H_ID"] == 2].iloc[0]
    assert we_row["source_H_ID"] == 1, (
        f"Weekend person source_H_ID should be 1 (weekday donor), got {we_row['source_H_ID']}"
    )

    assert report.n_weekend_households == 1
    assert report.n_hh_matched == 1


def test_weekend_persons_receive_weekday_wege_through_trips_join():
    """After remap, build_trip_table gives the weekend person a trip row via the
    source-id join — the core end-to-end assertion of the Task 7 spec.
    """
    households = _make_households()
    persons = _make_persons()
    wege = _make_wege()

    out, _trace, _report = wpm.reassign_weekend_plan_sources(
        households, persons, rng=np.random.RandomState(0)
    )

    # Confirm the remap happened so the trip assertion is meaningful.
    we_row = out[out["H_ID"] == 2].iloc[0]
    assert we_row["source_H_ID"] == 1, "Precondition: weekend person must be remapped"

    table = trips_mod.build_trip_table(out, wege)

    # The weekend person (person_id="c_2_1") must appear in the trip table because
    # build_trip_table overwrites H_ID/P_ID from source_H_ID/source_P_ID before
    # joining — so it joins on (H_ID=1, P_ID=1) and finds the weekday Wege.
    assert (table["person_id"] == "c_2_1").any(), (
        "Weekend person 'c_2_1' has no trip row; source-id join via source_H_ID "
        "did not propagate the weekday donor's Wege to the weekend person."
    )


# --- A1: the student status key in the weekend pass (ADR-0211) ---------------------------------------------

def _student_pass_frames():
    """Weekday HH1 (worker + student), HH2 (two workers); weekend HH3 (student + worker, size 2) and HH4 (size 3,
    no weekday household of that size, so the person-level fallback runs)."""
    households = pd.DataFrame({
        "H_ID": [1, 2, 3, 4], "H_GR": [2, 2, 2, 3], "hh_type5": ["couple"] * 4, "oek_status": [3] * 4,
        "RegioStaR7": [71] * 4, "H_ANZAUTO": [1] * 4, "H_GEW": [1.0] * 4,
    })
    rows = [  # H_ID, P_ID, age, sex, P_TAET, kernwo
        (1, 1, 40, 1, 1, 2), (1, 2, 21, 2, 10, 2),
        (2, 1, 40, 1, 1, 2), (2, 2, 38, 2, 1, 2),
        (3, 1, 22, 2, 10, 6), (3, 2, 40, 1, 1, 6),
        (4, 1, 20, 2, 10, 6), (4, 2, 41, 1, 1, 6), (4, 3, 8, 2, 9, 6),
    ]
    persons = pd.DataFrame({
        "H_ID": [r[0] for r in rows], "P_ID": [r[1] for r in rows], "HP_ALTER": [r[2] for r in rows],
        "HP_SEX": [r[3] for r in rows], "P_FSCHEIN": 1, "P_TAET": [r[4] for r in rows], "P_FKARTE": 1,
        "kernwo": [r[5] for r in rows], "source_H_ID": [r[0] for r in rows], "source_P_ID": [r[1] for r in rows],
        "member_imputed": False, "P_GEW": 1.0,
    })
    return households, persons


def test_student_key_in_the_weekend_pass_pairs_students_counts_paths_and_changes_only_student_households():
    differs = False
    for seed in range(20):
        households, persons = _student_pass_frames()
        off_p, off_trace, off_report = wpm.reassign_weekend_plan_sources(
            households, persons, rng=np.random.RandomState(seed))
        on_p, on_trace, on_report = wpm.reassign_weekend_plan_sources(
            households, persons, rng=np.random.RandomState(seed), student_key=True)
        src = lambda frame, h, p: tuple(frame.loc[(frame["H_ID"] == h) & (frame["P_ID"] == p),
                                                  ["source_H_ID", "source_P_ID"]].iloc[0])
        # household path: the student household takes the household with a student, student paired with student
        assert src(on_p, 3, 1) == (1, 2) and src(on_p, 3, 2) == (1, 1)
        differs |= src(off_p, 3, 1) != (1, 2)
        # person fallback (no weekday household of size 3): the student member still gets the student donor
        assert src(on_p, 4, 1) == (1, 2)
        # weekday households keep their own plan in both arms
        for h, p in ((1, 1), (1, 2), (2, 1), (2, 2)):
            assert src(on_p, h, p) == src(off_p, h, p) == (h, p)
        counts = on_report.student_counts
        assert counts["household_pairs"] == wpm.StudentMatchCounts(1, 1, 0)
        assert counts["person_fallback"] == wpm.StudentMatchCounts(1, 1, 0)
        assert counts["mixed_person_sweep"] == wpm.StudentMatchCounts(0, 0, 0)
        assert on_trace["target_is_student"].sum() == 3     # the three students of the frame, weekday one included
        assert on_trace.loc[on_trace["H_ID"].isin([3, 4]) & on_trace["target_is_student"], "donor_is_student"].tolist() \
            == [True, True]
        assert off_report.student_counts is None
        assert "target_is_student" not in off_trace.columns
    assert differs     # without the key the student household draws the household without a student for some seed


def test_student_key_off_weekend_pass_is_byte_identical_with_and_without_the_arguments():
    households, persons = _student_pass_frames()
    a = wpm.reassign_weekend_plan_sources(households, persons, rng=np.random.RandomState(5))
    b = wpm.reassign_weekend_plan_sources(households, persons, rng=np.random.RandomState(5),
                                          student_key=False, student_fallback_warn_share=0.2)
    pd.testing.assert_frame_equal(a[0], b[0])
    pd.testing.assert_frame_equal(a[1], b[1])
    assert a[2] == b[2]


def test_weekend_student_fallback_is_logged_per_path_and_warns_above_the_bound(caplog):
    households, persons = _student_pass_frames()
    persons.loc[persons["H_ID"].isin([1]), "P_TAET"] = 1       # no student donor left in the weekday pool
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.weekend_plan_match"):
        _p, _t, report = wpm.reassign_weekend_plan_sources(
            households, persons, rng=np.random.RandomState(0), student_key=True)
    assert report.student_counts["household_pairs"].n_non_student_donor == 1
    assert report.student_counts["person_fallback"].n_non_student_donor == 1
    paths = {r.getMessage().split("]")[0].lstrip("[") for r in caplog.records
             if "weekend_plan_match.student_donor." in r.getMessage()}
    assert paths == {f"weekend_plan_match.student_donor.{p}" for p in wpm.STUDENT_PATHS_WEEKEND}
    # no bound set: the two paths on which NO student target got a student donor still warn (the primary
    # method is broken there); the sweep had no student target and stays at INFO
    unbounded = {r.getMessage().split("]")[0].lstrip("[") for r in caplog.records
                 if "student_donor" in r.getMessage() and r.levelno == logging.WARNING}
    assert unbounded == {"weekend_plan_match.student_donor.household_pairs",
                         "weekend_plan_match.student_donor.person_fallback"}

    caplog.clear()
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.weekend_plan_match"):
        wpm.reassign_weekend_plan_sources(households, persons, rng=np.random.RandomState(0), student_key=True,
                                          student_fallback_warn_share=0.5)
    warned = {r.getMessage().split("]")[0].lstrip("[") for r in caplog.records
              if "student_donor" in r.getMessage() and r.levelno == logging.WARNING}
    assert warned == {"weekend_plan_match.student_donor.household_pairs",
                      "weekend_plan_match.student_donor.person_fallback"}


def test_partial_student_fallback_without_a_bound_stays_info_and_a_primary_path_does_not_warn(caplog):
    logger_name = "braunschweig.popsim.weekend_plan_match"
    with caplog.at_level(logging.INFO, logger=logger_name):
        wpm.log_student_match_counts(logging.getLogger(logger_name), "p.partial", wpm.StudentMatchCounts(4, 3, 1))
        wpm.log_student_match_counts(logging.getLogger(logger_name), "p.primary", wpm.StudentMatchCounts(4, 4, 0))
        wpm.log_student_match_counts(logging.getLogger(logger_name), "p.empty", wpm.StudentMatchCounts(0, 0, 0))
    assert [r for r in caplog.records if r.levelno >= logging.WARNING] == []
    assert "warn bound not set" in next(r.getMessage() for r in caplog.records if "p.partial" in r.getMessage())


def test_student_household_levels_are_reported_apart_from_the_other_households_levels():
    """A student household matches on a ladder one key longer, so its level means something else: the
    report counts it in ``student_hh_match_level_counts``, the trace marks its rows, and
    ``hh_match_level_counts`` keeps the meaning of the shorter ladder (as with the key off)."""
    households, persons = _student_pass_frames()
    # a weekend household WITHOUT a student, size 2: it matches on the short ladder
    households = pd.concat([households, households.iloc[[1]].assign(H_ID=6)], ignore_index=True)
    extra = pd.DataFrame({
        "H_ID": [6, 6], "P_ID": [1, 2], "HP_ALTER": [40, 38], "HP_SEX": [1, 2], "P_FSCHEIN": 1, "P_TAET": 1,
        "P_FKARTE": 1, "kernwo": 6, "source_H_ID": 6, "source_P_ID": [1, 2], "member_imputed": False, "P_GEW": 1.0})
    persons = pd.concat([persons, extra], ignore_index=True)
    _p, trace, report = wpm.reassign_weekend_plan_sources(
        households, persons, rng=np.random.RandomState(2), student_key=True)
    assert sum(report.student_hh_match_level_counts.values()) == 1      # HH3, the student household
    assert sum(report.hh_match_level_counts.values()) == 1              # HH6 only
    assert max(report.hh_match_level_counts) <= len(wpm.SOFT_KEYS_BY_PRIORITY)
    on_student_ladder = trace.loc[trace["hh_student_ladder"], "H_ID"].unique().tolist()
    assert on_student_ladder == [3]
    assert not trace.loc[trace["resolution"] != "hh_match", "hh_student_ladder"].any()

    # key off: no separate count, no extra column, one dict as before
    _p, off_trace, off_report = wpm.reassign_weekend_plan_sources(
        households, persons, rng=np.random.RandomState(2))
    assert off_report.student_hh_match_level_counts is None
    assert "hh_student_ladder" not in off_trace.columns
    assert sum(off_report.hh_match_level_counts.values()) == 2
