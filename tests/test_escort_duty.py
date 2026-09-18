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
