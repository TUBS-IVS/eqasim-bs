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


# Positive-path fixture value for the escort_residential test below: chosen to
# be distinct from every other pot_* value already used by _mini_candidates()
# (10.0, 5.0, 5.0, 0.0, 3.0) so a copy-paste of the wrong column could not pass
# by coincidence. Defined once and reused (never duplicated) in both the
# fixture and the assertion, so the expected value stays traceable.
_RESIDENTIAL_VISIT_POTENTIAL = 2.5


def _mini_candidates_with_visit():
    """``_mini_candidates()`` plus the ``offers_visit`` / ``pot_visit`` columns
    that ``append_residential_visit_candidates`` (Task 5, issue #127) adds in
    the real pipeline, including one residential visit candidate row.

    Needed for the ``escort_residential`` positive-path test below: none of
    the other fixtures in this file carry ``offers_visit`` / ``pot_visit``, so
    ``ESCORT_RESIDENTIAL_OFFER_COLUMN`` (derived from ``VISIT_OFFER_COLUMN``
    by ``append_escort_candidates``) was always False and
    ``escort_residential`` could never be emitted by ``_build_locations_df``
    (Finding 2, Task 6 review).
    """
    base = _mini_candidates()
    base[sc.VISIT_OFFER_COLUMN] = False
    base[sc.VISIT_POTENTIAL_COLUMN] = 0.0
    visit_row = gpd.GeoDataFrame({
        "location_id": ["sec_res_77"],
        "commune_id": ["1"],
        "iris_id": ["1"],
        "offers_shop": [False],
        "offers_leisure": [False],
        "offers_other": [False],
        "offers_escort": [True],
        "pot_shop": [0.0],
        "pot_shop_daily": [0.0],
        "pot_shop_non_daily": [0.0],
        "pot_leisure": [0.0],
        "pot_other": [0.0],
        sc.VISIT_OFFER_COLUMN: [True],
        sc.VISIT_POTENTIAL_COLUMN: [_RESIDENTIAL_VISIT_POTENTIAL],
        "geometry": [Point(5, 5)],
    }, crs="EPSG:25832")
    return gpd.GeoDataFrame(
        pd.concat([base, visit_row], ignore_index=True), crs="EPSG:25832")


def test_build_locations_df_emits_escort_residential_with_expected_potential():
    """Finding 2 (Task 6 review): a positive path for escort_residential.

    escort_residential is (a) filtered by the potential-column-existence
    check in _build_locations_df, (b) subject to a dedicated zero-potential
    skip, and (c) dependent on state set in a DIFFERENT function
    (append_escort_candidates derives ESCORT_RESIDENTIAL_OFFER_COLUMN from
    VISIT_OFFER_COLUMN, which append_residential_visit_candidates sets
    upstream in the real pipeline). None of the other tests in this file
    exercise that combination, so this covers it end to end.
    """
    cands = sc.append_escort_candidates(_mini_candidates_with_visit(), _mini_education())
    visit_row = cands[cands["location_id"] == "sec_res_77"].iloc[0]
    assert bool(visit_row[sc.ESCORT_RESIDENTIAL_OFFER_COLUMN]) is True

    locations = sc._build_locations_df(cands, with_potentials=True, escort_purpose=True)
    loc_row = locations[locations["id"] == "sec_res_77"].iloc[0]
    activities = loc_row["activities"].split("; ")
    potentials = [float(p) for p in loc_row["potentials"].split("; ")]

    assert "escort_residential" in activities
    assert potentials[activities.index("escort_residential")] == _RESIDENTIAL_VISIT_POTENTIAL


# ---------------------------------------------------------------------------
# Task 7: leg-loop draw, extraction, stats, other-subtype interplay.
# ---------------------------------------------------------------------------

def _escort_problem():
    return {
        "person_id": 1,
        "trip_index": 0,
        "purposes": ["escort"],
        "modes": ["car", "car"],
        "travel_times": [600.0, 600.0],
        "size": 1,
        "origin": np.array([[0.0, 0.0]]),
        "destination": np.array([[100.0, 100.0]]),
        "activity_index": 1,
    }


def _mode_distributions():
    return {
        "car": {
            "bounds": np.array([np.inf]),
            "distributions": [
                {"cdf": np.array([1.0]), "values": np.array([500.0]),
                 "weights": np.array([1.0])}
            ],
        }
    }


def _escort_distributions():
    return {
        "escort": _mode_distributions(),
        "other": _mode_distributions(),
    }


def test_plans_df_escort_leg_gets_drawn_activity_and_escort_layer():
    decider_calls = []
    def decider():
        decider_calls.append(1)
        return "escort_edu_kindergarten"
    plans, meta, unbounded, stats = sc._build_plans_df(
        [_escort_problem()], _escort_distributions(), 1.0,
        np.random.RandomState(0), escort_location_decider=decider,
    )
    assert list(plans["to_act_type"])[:1] == ["escort_edu_kindergarten"]
    assert stats["escort_edu_kindergarten"] == 1
    assert stats["escort_distance_layer_fallback"] == 0
    assert len(decider_calls) == 1


def test_plans_df_escort_distance_fallback_counted():
    plans, meta, unbounded, stats = sc._build_plans_df(
        [_escort_problem()], {"other": _mode_distributions()}, 1.0,
        np.random.RandomState(0),
        escort_location_decider=lambda: "escort_leisure",
    )
    assert stats["escort_distance_layer_fallback"] == 1


def test_plans_df_no_decider_leaves_escort_untouched():
    # decider None (flag OFF upstream): escort leg keeps plain purpose; no stats keys.
    plans, meta, unbounded, stats = sc._build_plans_df(
        [_escort_problem()], _escort_distributions(), 1.0, np.random.RandomState(0),
    )
    assert list(plans["to_act_type"])[:1] == ["escort"]
    assert "escort_distance_layer_fallback" not in stats


def test_other_subtype_decider_drops_escort_group_when_escort_purpose_on(monkeypatch):
    # Estimation must run on W_ZWECK {5,10} only with groups {errand, rest}.
    import braunschweig.popsim.mid as mid_module
    mini = pd.DataFrame({
        "W_ZWECK": [5, 5, 10, 6, 6],
        "W_ZWD": [601, 603, 999, 7704, 7704],
        "hvm_imp": [4, 4, 4, 4, 4],
        "W_SZS": [8]*5, "W_SZM": [0]*5, "W_AZS": [8]*5, "W_AZM": [10]*5,
        "W_GEW": [1.0]*5,
    })
    monkeypatch.setattr(mid_module, "load_mid_wege", lambda _dir: mini.copy())
    ctx = _Ctx({
        "secondary_other_subtype_split": True,
        "escort_purpose": True,
        "secondary_distance_min_obs": 1,
        "braunschweig.population.popsim.mid_dir": "unused",
    })
    decide = sc._build_other_subtype_decider(ctx, random_seed=3)
    outcomes = {decide("car", 600.0) for _ in range(200)}
    assert "other_escort" not in outcomes
    assert outcomes <= {"other_errand_short", "other_errand_long", "other_rest"}


# ---------------------------------------------------------------------------
# configure(): the three escort keys are declared with the documented
# defaults (the "Produces" contract for Task 7). Not part of the plan's
# Step-1 test list, added during self-review because none of the tests above
# exercise configure() itself and a typo in a key name here would otherwise
# go undetected -- see tests/test_secondary_chainsolvers_subtypes.py's
# _FakeContext for the sibling pattern this mirrors (a separate minimal stub
# is used here because this file owns only the escort-specific tests).
# ---------------------------------------------------------------------------

class _ConfigureCtx:
    """Minimal two-argument config() stub for exercising configure() itself.

    Mirrors synpp's ``ConfigurationContext.config(name, default)``: a key's
    value is resolved once (the first-seen default) and stays fixed for
    subsequent re-reads without a default -- configure() re-reads several
    flags this way right after declaring them."""
    def __init__(self):
        self.registered = {}

    def config(self, key, default=None):
        if key not in self.registered:
            self.registered[key] = default
        return self.registered[key]

    def stage(self, *args, **kwargs):
        return None


def test_configure_declares_escort_keys_with_documented_defaults():
    ctx = _ConfigureCtx()
    sc.configure(ctx)
    assert ctx.registered["escort_purpose"] is False
    assert ctx.registered["escort_locations_activities"] == sc.DEFAULT_ESCORT_LOCATIONS_ACTIVITIES
    assert ctx.registered["escort_locations_weights"] == sc.DEFAULT_ESCORT_LOCATIONS_WEIGHTS
    assert ctx.registered["escort_distance_by_type"] is False
    assert ctx.registered["escort_distance_factor_activities"] == sc.DEFAULT_ESCORT_LOCATIONS_ACTIVITIES
    assert ctx.registered["escort_distance_factors"] == sc.DEFAULT_ESCORT_DISTANCE_FACTORS


# --- escort distance-by-type (A3): factor map builder -------------------------
def test_distance_factor_defaults_match_pinned_csv():
    import csv, pathlib
    csv_path = pathlib.Path(__file__).resolve().parents[1] / "eqasim-data" / "data" \
        / "braunschweig" / "srv" / "srv2023_escort_distance_factors.csv"
    with open(csv_path, encoding="utf-8") as handle:
        rows = [r for r in csv.DictReader(
            line for line in handle if not line.startswith("#"))]
    pinned = {r["category"]: float(r["factor_applied"]) for r in rows}
    assert list(pinned) and set(pinned) == set(sc.DEFAULT_ESCORT_LOCATIONS_ACTIVITIES)
    for category, factor in zip(sc.DEFAULT_ESCORT_LOCATIONS_ACTIVITIES,
                                sc.DEFAULT_ESCORT_DISTANCE_FACTORS):
        assert factor == pytest.approx(pinned[category], abs=1e-9)


def test_factor_map_off_returns_none():
    ctx = _Ctx({"escort_distance_by_type": False})
    assert sc._build_escort_distance_factor_map(ctx) is None


def test_factor_map_requires_escort_purpose():
    with pytest.raises(RuntimeError, match="requires escort_purpose"):
        sc._build_escort_distance_factor_map(_Ctx({
            "escort_distance_by_type": True,
            "escort_purpose": False,
        }))


def test_factor_map_happy_path_keys_are_activity_names():
    ctx = _Ctx({
        "escort_distance_by_type": True,
        "escort_purpose": True,
        "escort_locations_activities": ["edu_kindergarten", "residential"],
        "escort_distance_factor_activities": ["edu_kindergarten", "residential"],
        "escort_distance_factors": [0.5, 1.5],
    })
    factor_map = sc._build_escort_distance_factor_map(ctx)
    assert factor_map == {"escort_edu_kindergarten": 0.5, "escort_residential": 1.5}


def test_factor_map_warns_when_draw_category_has_no_factor(capsys):
    # Final-review finding: escort_locations_activities (the draw vocabulary)
    # and escort_distance_factor_activities (the factor vocabulary) are
    # configured independently -- "shop" is drawable but has no factor entry
    # here, so its legs would silently fall back without this loud signal.
    ctx = _Ctx({
        "escort_distance_by_type": True,
        "escort_purpose": True,
        "escort_locations_activities": ["edu_kindergarten", "residential", "shop"],
        "escort_distance_factor_activities": ["edu_kindergarten", "residential"],
        "escort_distance_factors": [0.5, 1.5],
    })
    sc._build_escort_distance_factor_map(ctx)
    out = capsys.readouterr()[0]
    assert "WARNING: escort_distance_by_type" in out
    assert "shop" in out


def test_factor_map_validation():
    base = {"escort_distance_by_type": True, "escort_purpose": True}
    with pytest.raises(ValueError, match="same length"):
        sc._build_escort_distance_factor_map(_Ctx({**base,
            "escort_distance_factor_activities": ["edu_kindergarten"],
            "escort_distance_factors": [1.0, 2.0]}))
    with pytest.raises(ValueError, match="unknown escort location categor"):
        sc._build_escort_distance_factor_map(_Ctx({**base,
            "escort_distance_factor_activities": ["kita"],
            "escort_distance_factors": [1.0]}))
    with pytest.raises(ValueError, match="positive"):
        sc._build_escort_distance_factor_map(_Ctx({**base,
            "escort_distance_factor_activities": ["edu_kindergarten"],
            "escort_distance_factors": [0.0]}))


# --- escort distance-by-type (A3): layer synthesis ----------------------------
def test_synthesize_scales_values_and_keeps_base_unmutated():
    dists = _escort_distributions()
    base_values_before = dists["escort"]["car"]["distributions"][0]["values"].copy()
    out = sc._synthesize_escort_type_layers(
        dists, {"escort_edu_kindergarten": 0.5, "escort_residential": 1.0})
    assert out is dists  # in-place on the private copy, returned for chaining
    kita = out["escort_edu_kindergarten"]["car"]["distributions"][0]["values"]
    assert kita == pytest.approx(base_values_before * 0.5)
    resid = out["escort_residential"]["car"]["distributions"][0]["values"]
    assert resid == pytest.approx(base_values_before * 1.0)  # neutral layer EXISTS
    assert out["escort"]["car"]["distributions"][0]["values"] == pytest.approx(base_values_before)


def test_synthesize_no_op_on_legacy_or_missing_escort(capsys):
    legacy = _mode_distributions()  # top-level mode keys, no purpose layer
    out = sc._synthesize_escort_type_layers(legacy, {"escort_edu_kindergarten": 0.5})
    assert out is legacy and "escort_edu_kindergarten" not in out
    assert "WARNING" in capsys.readouterr()[0]

    no_escort = {"other": _mode_distributions()}
    out2 = sc._synthesize_escort_type_layers(no_escort, {"escort_edu_kindergarten": 0.5})
    assert "escort_edu_kindergarten" not in out2


# --- escort distance-by-type (A3): leg-loop layer selection --------------------
def _typed_escort_distributions():
    dists = _escort_distributions()
    return sc._synthesize_escort_type_layers(dists, {"escort_edu_kindergarten": 0.5})


def test_leg_uses_drawn_type_layer_when_enabled():
    plans, meta, unbounded, stats = sc._build_plans_df(
        [_escort_problem()], _typed_escort_distributions(), 1.0,
        np.random.RandomState(0),
        escort_location_decider=lambda: "escort_edu_kindergarten",
        escort_distance_by_type=True,
    )
    assert stats["escort_type_distance_layer_fallback"] == 0
    assert stats["escort_distance_layer_fallback"] == 0
    assert list(plans["to_act_type"])[:1] == ["escort_edu_kindergarten"]
    # PRIMARY-PATH PROOF (CLAUDE.md fallback rule 3): the sampled distance must
    # come from the 0.5-scaled layer -- 250.0, not the base layer's 500.0.
    assert plans["distance_meters"].iloc[0] == pytest.approx(250.0)


def test_leg_falls_back_to_escort_layer_counted():
    # type layer for the drawn name is MISSING -> counted fallback to "escort"
    plans, meta, unbounded, stats = sc._build_plans_df(
        [_escort_problem()], _escort_distributions(), 1.0,
        np.random.RandomState(0),
        escort_location_decider=lambda: "escort_leisure",
        escort_distance_by_type=True,
    )
    assert stats["escort_type_distance_layer_fallback"] == 1
    assert stats["escort_distance_layer_fallback"] == 0


def test_leg_falls_back_to_other_counted_when_no_escort_layer():
    plans, meta, unbounded, stats = sc._build_plans_df(
        [_escort_problem()], {"other": _mode_distributions()}, 1.0,
        np.random.RandomState(0),
        escort_location_decider=lambda: "escort_leisure",
        escort_distance_by_type=True,
    )
    assert stats["escort_type_distance_layer_fallback"] == 0
    assert stats["escort_distance_layer_fallback"] == 1


def test_flag_off_path_has_no_type_counter_and_uses_escort_layer():
    plans, meta, unbounded, stats = sc._build_plans_df(
        [_escort_problem()], _escort_distributions(), 1.0,
        np.random.RandomState(0),
        escort_location_decider=lambda: "escort_edu_kindergarten",
    )
    assert "escort_type_distance_layer_fallback" not in stats
    assert stats["escort_distance_layer_fallback"] == 0


def test_base_bs_config_enables_distance_by_type():
    import pathlib, yaml
    base = pathlib.Path(__file__).resolve().parents[1] / "configs" / "base_bs.yml"
    with open(base, encoding="utf-8") as handle:
        doc = yaml.safe_load(handle)
    assert doc["config"]["escort_distance_by_type"] is True
