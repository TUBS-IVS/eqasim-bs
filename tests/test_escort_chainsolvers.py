"""Escort purpose in the secondary chainsolvers (issue #201, Phase 1)."""
import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from braunschweig.synthesis.locations import secondary_chainsolvers as sc


class _Ctx:
    """Minimal synpp ExecuteContext stub. ``config(self, key)`` takes the key
    alone, mirroring ``synpp.pipeline.ExecuteContext.config`` (declared
    options only, no default parameter): a two-argument call from code under
    test fails here exactly as it would crash in production -- see
    tests/test_execute_context_config_contract.py. Tests must therefore
    supply every config key the decider under test actually reads."""
    def __init__(self, cfg):
        self._cfg = cfg
    def config(self, key):
        if key not in self._cfg:
            raise KeyError(
                f"_Ctx: no value for config key {key!r} -- declared-config "
                "semantics require the test to supply it explicitly."
            )
        return self._cfg[key]


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


# ---------------------------------------------------------------------------
# Task 6: escort candidate universe (education rows, residential reuse,
# offer emission).
# ---------------------------------------------------------------------------

def _mini_candidates():
    return gpd.GeoDataFrame({
        "location_id": ["sec_b_1", "sec_0"],
        "commune_id": ["1", "1"],
        "iris_id": ["1", "1"],
        "offers_shop": [True, False],
        "offers_leisure": [False, False],
        "offers_other": [False, True],
        "offers_escort": [True, True],
        "pot_shop": [10.0, 0.0],
        "pot_shop_daily": [5.0, 0.0],
        "pot_shop_non_daily": [5.0, 0.0],
        "pot_leisure": [0.0, 0.0],
        "pot_other": [0.0, 3.0],
        "geometry": [Point(0, 0), Point(1, 1)],
    }, crs="EPSG:25832")


def _mini_education():
    return gpd.GeoDataFrame({
        "fake": [False, False, True],
        "education_type": pd.Categorical(["kindergarten", "school", "unknown"]),
        "weight": [100.0, 400.0, 1.0],
        "location_id": ["edu_0", "edu_1", "edu_2"],
        "commune_id": ["1", "1", "2"],
        "iris_id": ["1", "1", "2"],
        "geometry": [Point(2, 2), Point(3, 3), Point(4, 4)],
    }, crs="EPSG:25832")


def test_append_escort_candidates_adds_edu_rows_and_columns():
    out = sc.append_escort_candidates(_mini_candidates(), _mini_education())
    # fake education rows are excluded
    assert len(out) == 2 + 2
    edu = out[out["location_id"].str.startswith("sec_edu_")]
    assert set(edu["location_id"]) == {"sec_edu_0", "sec_edu_1"}
    kita = out[out["location_id"] == "sec_edu_0"].iloc[0]
    assert kita["offers_escort_edu_kindergarten"] and not kita["offers_escort_edu_school"]
    assert kita["pot_escort_edu"] == 100.0
    school = out[out["location_id"] == "sec_edu_1"].iloc[0]
    assert school["offers_escort_edu_school"] and not school["offers_escort_edu_kindergarten"]
    # pre-existing rows keep escort-edu offers False and pot 0.0
    base = out[out["location_id"] == "sec_b_1"].iloc[0]
    assert not base["offers_escort_edu_kindergarten"] and base["pot_escort_edu"] == 0.0
    # edu rows offer escort (facilities aggregate) but none of the standard purposes
    assert bool(kita["offers_escort"]) and not kita["offers_shop"] and not kita["offers_other"]


def test_build_locations_df_emits_escort_activities():
    cands = sc.append_escort_candidates(_mini_candidates(), _mini_education())
    locations = sc._build_locations_df(cands, with_potentials=True, escort_purpose=True)
    kita_row = locations[locations["id"] == "sec_edu_0"].iloc[0]
    assert "escort_edu_kindergarten" in kita_row["activities"].split("; ")
    shop_row = locations[locations["id"] == "sec_b_1"].iloc[0]
    assert "escort_shop" in shop_row["activities"].split("; ")
    other_row = locations[locations["id"] == "sec_0"].iloc[0]
    assert "escort_other" in other_row["activities"].split("; ")


def test_build_locations_df_escort_off_is_unchanged():
    cands = sc.append_escort_candidates(_mini_candidates(), _mini_education())
    on_cols = sc._build_locations_df(cands, with_potentials=True, escort_purpose=False)
    assert not on_cols["activities"].str.contains("escort").any()


def test_build_locations_df_escort_requires_potentials():
    with pytest.raises(ValueError, match="escort_purpose requires"):
        sc._build_locations_df(_mini_candidates(), with_potentials=False, escort_purpose=True)
