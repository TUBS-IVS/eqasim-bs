# tests/test_day_type.py
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


def test_household_day_type_raises_on_mixed_household():
    persons = pd.DataFrame({"H_ID": [10, 10], "kernwo": [2, 6]})
    with pytest.raises(ValueError, match="mixed reporting day"):
        day_type.household_day_type(persons)
