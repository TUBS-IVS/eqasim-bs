import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim.placement_income import resolve_income_path, apply_own_income
from braunschweig.popsim.income import HIGH_INCOME_THRESHOLD_EUR


def test_resolve_income_path_off_reproduces_legacy_booleans():
    for kc in (True, False):
        for tilt in (True, False):
            path = resolve_income_path(False, kc, tilt)
            assert path == {"placement": False, "redraw": kc, "tilt": tilt,
                            "skip_inkar_scale": kc}


def test_resolve_income_path_on_overrides_redraw_and_tilt():
    path = resolve_income_path(True, True, True)
    assert path == {"placement": True, "redraw": False, "tilt": False,
                    "skip_inkar_scale": True}


def test_apply_own_income_draws_within_label_and_sets_high_income():
    persons = pd.DataFrame({
        "household_id": ["a", "a", "b", "c"],
        "household_income": ["900_1500", "900_1500", "over_7000", np.nan],
        "household_income_eur": [1.0, 1.0, 1.0, 1.0],
        "high_income": [False, False, False, False],
    })
    out, diag = apply_own_income(persons, random_seed=42)
    a = out[out.household_id == "a"]["household_income_eur"]
    assert a.nunique() == 1 and 900 <= a.iloc[0] < 1500        # one draw per household
    b = out[out.household_id == "b"].iloc[0]
    assert b["household_income_eur"] >= 7000 and bool(b["high_income"]) == (
        b["household_income_eur"] >= HIGH_INCOME_THRESHOLD_EUR)
    assert np.isnan(out[out.household_id == "c"]["household_income_eur"]).all()
    assert not out[out.household_id == "c"]["high_income"].iloc[0]
    assert diag["nan_label_rate"] == pytest.approx(1 / 3)
    # label column untouched
    pd.testing.assert_series_equal(out["household_income"], persons["household_income"])


def test_apply_own_income_deterministic_per_seed():
    persons = pd.DataFrame({
        "household_id": ["a", "b"], "household_income": ["2000_2600", "3000_3600"],
        "household_income_eur": [0.0, 0.0], "high_income": [False, False]})
    o1, _ = apply_own_income(persons, random_seed=7)
    o2, _ = apply_own_income(persons, random_seed=7)
    pd.testing.assert_frame_equal(o1, o2)
