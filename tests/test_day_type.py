# tests/test_day_type.py
import logging

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import day_type


def test_person_day_type_maps_weekday_and_weekend():
    s = pd.Series([1, 2, 3, 4, 5, 6, 7])
    out = day_type.person_day_type(s)
    assert list(out) == ["weekday"] * 3 + ["weekend"] * 4


def test_person_day_type_raises_on_unknown_kernwo():
    with pytest.raises(ValueError, match="kernwo"):
        day_type.person_day_type(pd.Series([1, 99]))


def test_household_day_type_is_per_household():
    persons = pd.DataFrame({
        "H_ID": [10, 10, 20, 20],
        "kernwo": [2, 3, 6, 6],
    })
    out = day_type.household_day_type(persons)
    assert out.loc[10] == "weekday"
    assert out.loc[20] == "weekend"


def test_household_day_type_resolves_mixed_by_majority(caplog):
    """A mixed household must resolve to the majority day_type without raising.

    Previous behaviour: raised ValueError.
    New behaviour (Option B): emits a logger.warning and resolves by majority
    (ties -> 'weekday' because 'weekday' < 'weekend' alphabetically).
    """
    # Household 10 has 2 weekday + 1 weekend -> majority = weekday
    # Household 20 has 1 weekday + 2 weekend -> majority = weekend
    persons = pd.DataFrame({
        "H_ID":   [10, 10, 10, 20, 20, 20],
        "kernwo": [2,   3,  6,  2,  6,  7],
    })
    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.day_type"):
        out = day_type.household_day_type(persons)

    # Does NOT raise.
    assert out.loc[10] == "weekday"
    assert out.loc[20] == "weekend"

    # A warning must be emitted reporting the count of mixed households.
    assert any("mixed reporting day" in r.getMessage() for r in caplog.records)

    # Tie-breaking: 1 weekday + 1 weekend -> resolves to "weekday".
    persons_tie = pd.DataFrame({"H_ID": [10, 10], "kernwo": [2, 6]})
    out_tie = day_type.household_day_type(persons_tie)
    assert out_tie.loc[10] == "weekday"
