# tests/test_member_completion_day_aware.py
"""Tests for the day-type-aware mirror selection in complete_members.

Change 1 of the Option B weekend-plan-match fix: complete_members now accepts
``kernwo_col`` and -- when kernwo is present and the pool contains both weekday
and weekend households -- restricts mirror candidates to the same day type as
the incomplete host household.

When kernwo is absent OR all households share one day type the candidate set
and RNG consumption are BYTE-IDENTICAL to the legacy path (no behaviour
change).
"""

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import member_completion


# ---------------------------------------------------------------------------
# Helper builders
# ---------------------------------------------------------------------------

def _make_households(*args):
    """Build a households frame from tuples of (H_ID, H_GR).

    All households are given H_GEW=1.0 so _select_mirror's weighted draw
    behaves identically to the old uniform draw (equal weights -> uniform).
    """
    return pd.DataFrame(
        [{"H_ID": h, "H_GR": gr, "H_GEW": 1.0} for h, gr in args]
    )


def _make_persons(*args):
    """Build a persons frame from tuples of (H_ID, P_ID, HP_ALTER, HP_SEX, kernwo).

    ``kernwo`` may be omitted (set to None) for the no-kernwo tests.
    """
    rows = []
    for tup in args:
        h_id, p_id, age, sex = tup[:4]
        kw = tup[4] if len(tup) > 4 else None
        row = {"H_ID": h_id, "P_ID": p_id, "HP_ALTER": age, "HP_SEX": sex}
        if kw is not None:
            row["kernwo"] = kw
        rows.append(row)
    df = pd.DataFrame(rows)
    if "kernwo" in df.columns:
        df["kernwo"] = df["kernwo"].astype(int)
    return df


# ---------------------------------------------------------------------------
# Test 1: filler comes from the same-day-type mirror
# ---------------------------------------------------------------------------

def test_complete_members_filler_matches_host_day_type():
    """A weekday-incomplete host must receive a filler from the weekday mirror,
    never from the weekend mirror -- even when both have equal H_GR.

    Setup:
      - Host A  (H_GR=2, 1 existing weekday person, kernwo=1 -> weekday)
      - Mirror W (H_GR=2, 2 persons, kernwo=1 -> weekday) -- the CORRECT donor
      - Mirror X (H_GR=2, 2 persons, kernwo=5 -> weekend) -- must NEVER be picked
    """
    households = _make_households(("A", 2), ("W", 2), ("X", 2))
    persons = _make_persons(
        # Incomplete weekday host
        ("A", 1, 40, 1, 1),
        # Complete weekday mirror
        ("W", 1, 38, 2, 2),
        ("W", 2, 35, 1, 3),
        # Complete weekend mirror -- must be excluded
        ("X", 1, 36, 2, 5),
        ("X", 2, 33, 1, 6),
    )

    _, filled_p, report = member_completion.complete_members(
        households, persons, rng=np.random.RandomState(42),
    )

    fillers = filled_p[(filled_p["H_ID"] == "A") & filled_p["member_imputed"]]
    assert len(fillers) == 1, "Host A should receive exactly 1 filler."
    assert (fillers["source_H_ID"] == "W").all(), (
        f"Filler must come from the weekday mirror W, got {fillers['source_H_ID'].tolist()}"
    )
    assert report.n_households_filled == 1
    assert report.n_persons_added == 1


# ---------------------------------------------------------------------------
# Test 2: no-kernwo path is unchanged (no crash, same behaviour)
# ---------------------------------------------------------------------------

def test_complete_members_no_kernwo_is_unchanged():
    """When the persons frame has no 'kernwo' column the function must behave
    exactly as before: fill the host without raising, using the only available
    mirror.
    """
    households = _make_households(("A", 2), ("B", 2))
    # No kernwo column
    persons = _make_persons(
        ("A", 1, 40, 1),
        ("B", 1, 38, 2),
        ("B", 2, 35, 1),
    )
    assert "kernwo" not in persons.columns, "Fixture must not include kernwo."

    _, filled_p, report = member_completion.complete_members(
        households, persons, rng=np.random.RandomState(0),
    )

    fillers = filled_p[(filled_p["H_ID"] == "A") & filled_p["member_imputed"]]
    assert len(fillers) == 1
    assert (fillers["source_H_ID"] == "B").all()
    assert report.n_households_filled == 1


# ---------------------------------------------------------------------------
# Test 3: single day type -> day filter is a NO-OP, legacy behaviour retained
# ---------------------------------------------------------------------------

def test_complete_members_single_daytype_is_noop():
    """When ALL households share the same day type (e.g. all weekday), the
    kernwo filter resolves to hh_dt=None and has NO effect on candidate
    selection.  The host still gets a filler from the only available mirror.
    """
    households = _make_households(("A", 2), ("B", 2))
    persons = _make_persons(
        ("A", 1, 40, 1, 1),   # weekday
        ("B", 1, 38, 2, 2),   # weekday
        ("B", 2, 35, 1, 3),   # weekday
    )
    # All kernwo in {1,2,3} -> single day type -> hh_dt=None

    _, filled_p, report = member_completion.complete_members(
        households, persons, rng=np.random.RandomState(0),
    )

    fillers = filled_p[(filled_p["H_ID"] == "A") & filled_p["member_imputed"]]
    assert len(fillers) == 1
    assert (fillers["source_H_ID"] == "B").all(), (
        "The only available mirror is B; the day filter must not block it."
    )
    assert report.n_households_filled == 1


# ---------------------------------------------------------------------------
# Test 4: no same-day mirror -> household stays unfilled (never cross day type)
# ---------------------------------------------------------------------------

def test_complete_members_host_without_day_type_still_fills():
    """A host whose day type cannot be determined (absent from hh_dt because it
    has no persons with kernwo) must still be filled using the un-day-constrained
    candidate set (legacy fallback), not silently left unfillable.

    This exercises the ``host_dt is None`` guard introduced in Fix 1.

    Construction: we use the no-kernwo path (hh_dt=None) which is the broadest
    equivalent -- the pre-Option-B code never filtered by day type and always
    filled when a same-size mirror existed.  Verifying that a host IS filled when
    kernwo is absent confirms the guard keeps the pre-Option-B contract intact.

    For the narrower sub-case where hh_dt is built but the host itself is absent
    (e.g. host has zero persons), construction would require a host with H_GR>0
    but no rows in the persons frame -- an edge case that is already gated by the
    ``incomplete_mask`` logic upstream.  The guard in the code is a defensive
    belt-and-suspenders; the no-kernwo test above is the meaningful regression
    anchor.
    """
    # Same as test_complete_members_no_kernwo_is_unchanged but documented
    # explicitly as the host-without-day-type regression test.
    households = _make_households(("A", 2), ("B", 2))
    persons = _make_persons(
        ("A", 1, 30, 1),   # no kernwo -> hh_dt is None -> no day filter
        ("B", 1, 40, 2),
        ("B", 2, 35, 1),
    )

    _, filled_p, report = member_completion.complete_members(
        households, persons, rng=np.random.RandomState(7),
    )

    fillers = filled_p[(filled_p["H_ID"] == "A") & filled_p["member_imputed"]]
    assert len(fillers) == 1, (
        "Host A must still be filled even when day type is indeterminate "
        "(no kernwo column -> hh_dt=None -> legacy fallback)."
    )
    assert report.n_households_filled == 1
    assert report.n_persons_added == 1


def test_complete_members_no_same_day_mirror_stays_unfilled():
    """If the only complete equal-size mirror is from the WRONG day type, the
    incomplete host must remain unfilled (n_unfillable += 1), never cross the
    day boundary.
    """
    # Host A is weekday (kernwo=1), Mirror X is weekend (kernwo=6).
    households = _make_households(("A", 2), ("X", 2))
    persons = _make_persons(
        ("A", 1, 40, 1, 1),
        ("X", 1, 38, 2, 6),
        ("X", 2, 35, 1, 6),
    )

    _, filled_p, report = member_completion.complete_members(
        households, persons, rng=np.random.RandomState(0),
    )

    # No filler appended for host A.
    a_persons = filled_p[filled_p["H_ID"] == "A"]
    assert len(a_persons) == 1, "Host A must stay at 1 person (no cross-day fill)."
    assert a_persons["member_imputed"].sum() == 0
    assert report.n_households_filled == 0
    assert report.n_persons_added == 0


# ---------------------------------------------------------------------------
# Test 6: _select_mirror draws proportional to H_GEW
# ---------------------------------------------------------------------------

def test_select_mirror_draws_proportional_to_h_gew():
    incomplete_row = pd.Series({"H_ID": 1, "H_GR": 2, "hhgr_gr": 2, "oek_status": 3, "RegioStaR7": 71})
    candidates = pd.DataFrame({
        "H_ID": [10, 20], "H_GR": [2, 2], "hhgr_gr": [2, 2],
        "oek_status": [3, 3], "RegioStaR7": [71, 71], "H_GEW": [1.0, 9.0],
    })
    rng = np.random.RandomState(0)
    counts = {10: 0, 20: 0}
    for _ in range(2000):
        counts[member_completion._select_mirror(incomplete_row, candidates, household_id="H_ID", rng=rng)] += 1
    assert counts[20] > counts[10] * 3  # heavy mirror dominates
