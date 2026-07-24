"""Escort purpose in the secondary chainsolvers (issue #201, Phase 1)."""
import numpy as np
import pandas as pd
import pytest

from braunschweig.synthesis.locations import secondary_chainsolvers as sc


class _Ctx:
    """Minimal synpp-context stub (mirrors the stubs in
    tests/test_secondary_chainsolvers_subtypes.py -- reuse that file's stub if it
    is importable instead of redefining)."""
    def __init__(self, cfg):
        self._cfg = cfg
    def config(self, key, default=None):
        return self._cfg.get(key, default)


def test_constants_vocabulary():
    assert sc.SECONDARY_PURPOSES == {"shop", "leisure", "other", "escort"}
    assert sc.ESCORT_LOCATION_SEED_OFFSET == 90214
    assert set(sc.ESCORT_CATEGORY_TO_ACTIVITY.values()) == set(sc.ESCORT_LOCATION_ACTIVITIES)
    assert list(sc.ESCORT_CATEGORY_TO_ACTIVITY) == sc.DEFAULT_ESCORT_LOCATIONS_ACTIVITIES or \
        set(sc.ESCORT_CATEGORY_TO_ACTIVITY) == set(sc.DEFAULT_ESCORT_LOCATIONS_ACTIVITIES)
    assert sum(sc.DEFAULT_ESCORT_LOCATIONS_WEIGHTS) == pytest.approx(1.0, abs=1e-9)


def test_decider_off_returns_none():
    ctx = _Ctx({"escort_purpose": False})
    assert sc._build_escort_location_decider(ctx, random_seed=7) is None


def test_decider_draw_is_deterministic_and_weighted():
    cfg = {
        "escort_purpose": True,
        "escort_locations_activities": ["edu_kindergarten", "leisure"],
        "escort_locations_weights": [0.75, 0.25],
    }
    decide_a = sc._build_escort_location_decider(_Ctx(cfg), random_seed=7)
    decide_b = sc._build_escort_location_decider(_Ctx(cfg), random_seed=7)
    draws_a = [decide_a() for _ in range(2000)]
    draws_b = [decide_b() for _ in range(2000)]
    assert draws_a == draws_b  # same seed -> identical stream
    share_kita = draws_a.count("escort_edu_kindergarten") / 2000.0
    assert 0.70 < share_kita < 0.80
    assert set(draws_a) <= {"escort_edu_kindergarten", "escort_leisure"}


def test_decider_config_validation():
    with pytest.raises(ValueError, match="same length"):
        sc._build_escort_location_decider(_Ctx({
            "escort_purpose": True,
            "escort_locations_activities": ["edu_kindergarten", "leisure"],
            "escort_locations_weights": [1.0],
        }), random_seed=1)
    with pytest.raises(ValueError, match="unknown escort location categor"):
        sc._build_escort_location_decider(_Ctx({
            "escort_purpose": True,
            "escort_locations_activities": ["kita"],
            "escort_locations_weights": [1.0],
        }), random_seed=1)
    with pytest.raises(ValueError, match="positive"):
        sc._build_escort_location_decider(_Ctx({
            "escort_purpose": True,
            "escort_locations_activities": ["edu_kindergarten"],
            "escort_locations_weights": [0.0],
        }), random_seed=1)
