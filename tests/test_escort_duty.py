"""Tests for ``braunschweig.synthesis.escort_duty`` (issue #425).

The module answers ONE question -- which persons carry an escort leg on their pre-assignment
reporting day -- for every consumer that needs it, so the escort definition lives in one place
rather than being re-derived per stage.
"""
import pandas as pd
import pytest

from braunschweig.synthesis import escort_duty as E


def _trips(rows):
    return pd.DataFrame(rows, columns=["person_id", "following_purpose", "preceding_purpose"])


def test_escort_person_ids_returns_persons_with_an_escort_leg_on_either_trip_end():
    """The outbound leg ARRIVES at the escort activity (following_purpose), the return leg DEPARTS
    from it (preceding_purpose); either end is evidence of the duty (ADR-0104 Assumption 4)."""
    trips = _trips([
        (1, "escort", "home"),      # outbound escort leg
        (2, "home", "escort"),      # return leg from the escort activity
        (3, "work", "home"),        # no escort involvement
        (3, "home", "work"),
        (4, "shop", "leisure"),
    ])
    assert E.escort_person_ids(trips) == {1, 2}


def test_escort_person_ids_is_empty_when_no_leg_carries_the_escort_purpose():
    trips = _trips([(1, "work", "home"), (2, "home", "work")])
    assert E.escort_person_ids(trips) == set()


def test_escort_person_ids_logs_the_share_by_default_and_stays_quiet_on_request(caplog):
    """The share is a fallback-transparency rate for the stages that OWN the decision. A consumer
    that only needs the set for a diagnostic (``plan_replacement``'s stranded-children metric)
    passes ``log=False``, so the same rate is not repeated for every caller in one run."""
    trips = _trips([(1, "escort", "home"), (2, "work", "home")])
    with caplog.at_level("INFO", logger=E.logger.name):
        E.escort_person_ids(trips)
    assert any("escort duty" in message for message in caplog.messages)
    caplog.clear()
    with caplog.at_level("INFO", logger=E.logger.name):
        E.escort_person_ids(trips, log=False)
    assert not caplog.messages


def test_escort_person_ids_raises_a_named_error_on_a_missing_column():
    trips = pd.DataFrame({"person_id": [1], "following_purpose": ["escort"]})
    with pytest.raises(ValueError, match="preceding_purpose"):
        E.escort_person_ids(trips)


def test_escort_purpose_constant_agrees_with_the_two_existing_copies():
    """``state_stage`` and ``plan_replacement`` each keep a local ``ESCORT_PURPOSE`` literal
    (deliberately, to avoid cross-module imports that would couple stage hashes). This pin keeps
    the shared module's constant equal to both, so a future rename cannot silently diverge."""
    from braunschweig.synthesis.commute_day import plan_replacement, state_stage
    assert E.ESCORT_PURPOSE == "escort"
    assert E.ESCORT_PURPOSE == state_stage.ESCORT_PURPOSE == plan_replacement.ESCORT_PURPOSE


def test_the_three_escort_call_sites_select_the_same_persons_on_a_shared_fixture():
    """Since issue #425 all three call sites route through ``escort_person_ids``, so this pins the
    consolidation rather than a coincidence: if anyone re-inlines the rule in one place and it
    diverges -- a purpose alias, a third trip end, an inverted condition -- this fails.

    It is load-bearing, not decorative: ADR-0110 Amendment 2's "stranded children = 0 by
    construction" holds only while the day-absence gate (this module) and the stranded-children
    metric (``plan_replacement``) select the SAME persons.

    ``state_stage._escort_person_ids`` takes a donors frame for its own guard; an empty one with
    the column it inspects exercises the selection without tripping that warning."""
    import pandas as pd

    from braunschweig.synthesis.commute_day import plan_replacement, state_stage

    trips = _trips([
        (1, "escort", "home"),       # escort on following_purpose
        (2, "home", "escort"),       # escort on preceding_purpose
        (3, "escort", "escort"),     # both ends
        (4, "work", "home"),         # neither
        (4, "home", "work"),
        (5, "leisure", "shop"),
    ])
    expected = {1, 2, 3}

    # 1. this module
    assert E.escort_person_ids(trips) == expected

    # 2. the commute-day state stage
    donors = pd.DataFrame({"has_active_escort": pd.Series(dtype=bool)})
    assert state_stage._escort_person_ids(trips, donors) == expected

    # 3. plan_replacement's inline mask, applied exactly as build_day_trips applies it
    mask = ((trips["preceding_purpose"] == plan_replacement.ESCORT_PURPOSE)
            | (trips["following_purpose"] == plan_replacement.ESCORT_PURPOSE))
    assert set(trips.loc[mask, "person_id"]) == expected
