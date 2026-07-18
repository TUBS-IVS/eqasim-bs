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
    assert diag["n_households"] == 3
    # label column untouched
    pd.testing.assert_series_equal(out["household_income"], persons["household_income"])


def test_apply_own_income_deterministic_per_seed():
    persons = pd.DataFrame({
        "household_id": ["a", "b"], "household_income": ["2000_2600", "3000_3600"],
        "household_income_eur": [0.0, 0.0], "high_income": [False, False]})
    o1, _ = apply_own_income(persons, random_seed=7)
    o2, _ = apply_own_income(persons, random_seed=7)
    pd.testing.assert_frame_equal(o1, o2)


def test_resolve_income_path_logs_each_override(caplog):
    import logging
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.placement_income"):
        resolve_income_path(True, True, False)
    messages = [r.message for r in caplog.records]
    assert any("income_kreis_control=ON" in m for m in messages)
    assert not any("income_spatial_tilt=ON" in m for m in messages)
    caplog.clear()
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.placement_income"):
        resolve_income_path(True, False, True)
    messages = [r.message for r in caplog.records]
    assert any("income_spatial_tilt=ON" in m for m in messages)
    assert not any("income_kreis_control=ON" in m for m in messages)


from braunschweig.popsim import stage as stage_mod


def test_placement_income_key_registered_and_default_on():
    assert stage_mod.KEY_PLACEMENT_INCOME == "braunschweig.population.popsim.placement_income"
    # configure() must register the key with default True: emulate synpp's config recorder.
    class _Ctx:
        def __init__(self):
            self.defaults = {}

        def config(self, key, default=None):
            self.defaults[key] = default
            return default

        def stage(self, *a, **k):
            pass

    ctx = _Ctx()
    try:
        stage_mod.configure(ctx)
    except Exception:
        pass  # unrelated stage deps may raise on the dummy ctx; the key must be registered first
    assert ctx.defaults.get(stage_mod.KEY_PLACEMENT_INCOME) is True


def test_check_controls_source_compatible():
    from braunschweig.popsim.placement_income import check_controls_source_compatible
    check_controls_source_compatible(False, "csv")        # OFF: any source fine
    check_controls_source_compatible(True, "catalog")     # ON + catalog: fine
    check_controls_source_compatible(True, " Catalog ")   # normalization
    with pytest.raises(ValueError):
        check_controls_source_compatible(True, "csv")
