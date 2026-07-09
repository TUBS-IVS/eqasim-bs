"""Tests for the leisure/other subtype chainsolvers wiring (issue #127, Task 4).

TDD: written BEFORE the implementation. Mirrors the existing shop_daily_split
tests in tests/test_secondary_chainsolvers.py (same fixture style, same
"stub distributions + stub decider" pattern for _build_plans_df, same
_build_locations_df / _extract_locations coverage), extended for the leisure
(4-group) and other (errand_short/errand_long/escort/rest) subtype splits.

Scenarios covered:
    (a) leisure legs get group-conditioned distances; a leisure_excursion leg
        draws from the DISTINCT leisure_excursion CDF, not the aggregate one.
    (b) other_rest legs keep the plain "other" placement AND distance (the
        aggregate behaviour, unchanged from the OFF path).
    (c) OFF path (both flags False / deciders None) stays byte-identical.
    (d) internal -> eqasim purpose back-mapping (LEISURE_SUBTYPE_ACTIVITIES /
        OTHER_SUBTYPE_ACTIVITIES / _ACTIVITY_POTENTIAL_COLUMN) is complete and
        consistent.
    (e) decider determinism: two deciders built from the same seed produce the
        identical sequence of per-leg outcomes.
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely import geometry as geo

from braunschweig.synthesis.locations import secondary_chainsolvers as sc

# ---------------------------------------------------------------------------
# Shared fixtures (mirrors tests/test_secondary_chainsolvers.py style).
# ---------------------------------------------------------------------------


def _flat_distribution():
    """Minimal mode-conditional distribution: one bound bucket, a uniform-step
    CDF over a few candidate distance values (metres)."""
    values = np.array([800.0, 1000.0, 1200.0, 1500.0])
    cdf = np.array([0.25, 0.5, 0.75, 1.0])
    return {
        mode: {
            "bounds": np.array([], dtype=float),
            "distributions": [{"values": values.copy(), "cdf": cdf.copy()}],
        }
        for mode in ("car", "car_passenger", "pt", "bicycle", "walk")
    }


def _single_value_distribution(value: float):
    """A degenerate one-value CDF: every draw returns exactly ``value``."""
    return {
        mode: {
            "bounds": np.array([], dtype=float),
            "distributions": [{"values": np.array([value]), "cdf": np.array([1.0])}],
        }
        for mode in ("car", "car_passenger", "pt", "bicycle", "walk")
    }


def _leisure_problem():
    """One bounded problem, single leisure leg between two fixed anchors."""
    return [{
        "person_id": 200, "activity_index": 2, "size": 1,
        "purposes": ["leisure"], "modes": ["car", "car"],
        "travel_times": np.array([600.0, 600.0]),
        "origin": np.array([[0.0, 0.0]]),
        "destination": np.array([[1000.0, 1000.0]]),
    }]


def _other_problem():
    """One bounded problem, single "other" leg between two fixed anchors."""
    return [{
        "person_id": 300, "activity_index": 2, "size": 1,
        "purposes": ["other"], "modes": ["car", "car"],
        "travel_times": np.array([600.0, 600.0]),
        "origin": np.array([[0.0, 0.0]]),
        "destination": np.array([[1000.0, 1000.0]]),
    }]


def _bounded_problems_mixed():
    """A small deterministic set of shop/leisure/other bounded problems, used
    for the OFF-path byte-identical check."""
    rng = np.random.RandomState(11)
    problems = []
    for i, purpose in enumerate(["shop", "leisure", "other", "leisure", "other"]):
        problems.append({
            "person_id": 2000 + i,
            "activity_index": int(rng.randint(0, 5)),
            "size": 1,
            "purposes": [purpose],
            "modes": ["car", "car"],
            "travel_times": rng.uniform(120.0, 1200.0, size=2),
            "origin": rng.uniform(0.0, 2000.0, size=(1, 2)),
            "destination": rng.uniform(0.0, 2000.0, size=(1, 2)),
        })
    return problems


# ---------------------------------------------------------------------------
# (d) internal <-> eqasim purpose back-mapping: structural consistency.
# ---------------------------------------------------------------------------


def test_leisure_subtype_activities_match_purpose_subtype_leisure_groups():
    from braunschweig.popsim.purpose_subtype import LEISURE_GROUPS
    assert set(sc.LEISURE_SUBTYPE_ACTIVITIES) == set(LEISURE_GROUPS)


def test_other_subtype_activities_match_errand_groups_plus_escort():
    from braunschweig.popsim.purpose_subtype import OTHER_ERRAND_GROUPS
    assert set(sc.OTHER_SUBTYPE_ACTIVITIES) == set(OTHER_ERRAND_GROUPS) | {"other_escort"}
    assert "other_rest" not in sc.OTHER_SUBTYPE_ACTIVITIES


def test_activity_potential_column_covers_all_subtype_activities():
    for name in sc.LEISURE_SUBTYPE_ACTIVITIES:
        assert sc._ACTIVITY_POTENTIAL_COLUMN[name] == "pot_leisure"
    for name in sc.OTHER_SUBTYPE_ACTIVITIES:
        assert sc._ACTIVITY_POTENTIAL_COLUMN[name] == "pot_other"
    # other_rest is never a chainsolver activity name -> no potential-column entry.
    assert "other_rest" not in sc._ACTIVITY_POTENTIAL_COLUMN


def test_extract_locations_secondary_acts_includes_all_new_subtypes():
    """_extract_locations must not silently drop a subtype-tagged leg."""
    rdf = pd.DataFrame({
        "unique_person_id": ["9#0"] * 5,
        "unique_leg_id": [f"9#0#{i}" for i in range(5)],
        "to_act_type": [
            "leisure_local", "leisure_excursion",
            "other_errand_short", "other_escort", "other",
        ],
        "to_x": [0.0, 10.0, 20.0, 30.0, 40.0],
        "to_y": [0.0, 10.0, 20.0, 30.0, 40.0],
        "to_act_identifier": ["L1", "L2", "L3", "L4", "L5"],
    })
    meta = [{"problem_idx": 0, "person_id": 9, "activity_index": 5, "n_secondary": 5}]
    secondary = gpd.GeoDataFrame(
        {"location_id": ["L1", "L2", "L3", "L4", "L5"]},
        geometry=[geo.Point(x, x) for x in (0, 10, 20, 30, 40)],
        crs="EPSG:25832",
    )
    df_loc, df_conv = sc._extract_locations(rdf, meta, secondary, crs="EPSG:25832")
    assert list(df_loc["person_id"]) == [9] * 5
    assert "to_act_type" not in df_loc.columns
    assert list(df_conv["valid"]) == [True]


# ---------------------------------------------------------------------------
# _inverse_cdf_choice: pure inverse-CDF selection helper.
# ---------------------------------------------------------------------------


def test_inverse_cdf_choice_selects_the_only_positive_group():
    probs = {"a": 0.0, "b": 1.0, "c": 0.0}
    names = ("a", "b", "c")
    for draw in (0.0, 0.3, 0.999):
        assert sc._inverse_cdf_choice(probs, names, draw) == "b"


def test_inverse_cdf_choice_walks_cumulative_boundaries():
    probs = {"a": 0.25, "b": 0.25, "c": 0.5}
    names = ("a", "b", "c")
    assert sc._inverse_cdf_choice(probs, names, 0.1) == "a"
    assert sc._inverse_cdf_choice(probs, names, 0.3) == "b"
    assert sc._inverse_cdf_choice(probs, names, 0.9) == "c"


def test_inverse_cdf_choice_missing_name_defaults_to_zero_probability():
    # A name absent from `probs` is treated as probability 0 (no KeyError).
    probs = {"only": 1.0}
    assert sc._inverse_cdf_choice(probs, ("only", "absent"), 0.5) == "only"


# ---------------------------------------------------------------------------
# (a) leisure legs: group-conditioned distance, excursion draws its own CDF.
# ---------------------------------------------------------------------------


def test_build_plans_df_leisure_decider_tags_leg_and_uses_subtype_distance():
    layered = {
        "leisure_excursion": _single_value_distribution(99000.0),  # distinct value
        "leisure": _single_value_distribution(1000.0),             # aggregate
        "shop": _flat_distribution(),
        "other": _flat_distribution(),
    }
    df, meta, unbounded, stats = sc._build_plans_df(
        _leisure_problem(), layered, 2.0, np.random.RandomState(1),
        leisure_subtype_decider=lambda mode, tt: "leisure_excursion",
    )
    rows = df[df["to_act_type"] == "leisure_excursion"]
    assert len(rows) == 1
    # Drawn from the DISTINCT leisure_excursion CDF, not the aggregate leisure one.
    assert rows.iloc[0]["distance_meters"] == 99000.0
    assert stats["leisure_excursion"] == 1
    assert stats["leisure_local"] == 0 and stats["leisure_visit"] == 0 and stats["leisure_activity"] == 0
    assert stats["leisure_distance_layer_fallback"] == 0


def test_build_plans_df_leisure_subtype_distance_layer_fallback_counted():
    # The subtype layer is ABSENT -> distance falls back to the aggregate
    # "leisure" layer; the placement activity still carries the subtype.
    layered = {
        "leisure": _single_value_distribution(1234.0),
        "shop": _flat_distribution(),
        "other": _flat_distribution(),
    }
    df, meta, unbounded, stats = sc._build_plans_df(
        _leisure_problem(), layered, 2.0, np.random.RandomState(1),
        leisure_subtype_decider=lambda mode, tt: "leisure_visit",
    )
    row = df[df["to_act_type"] == "leisure_visit"].iloc[0]
    assert row["distance_meters"] == 1234.0
    assert stats["leisure_visit"] == 1
    assert stats["leisure_distance_layer_fallback"] == 1


# ---------------------------------------------------------------------------
# (b) other_rest: keeps the plain "other" placement AND distance.
# ---------------------------------------------------------------------------


def test_build_plans_df_other_rest_keeps_aggregate_placement_and_distance():
    layered = {
        "other": _single_value_distribution(4321.0),
        "shop": _flat_distribution(),
        "leisure": _flat_distribution(),
    }
    df, meta, unbounded, stats = sc._build_plans_df(
        _other_problem(), layered, 2.0, np.random.RandomState(2),
        other_subtype_decider=lambda mode, tt: "other_rest",
    )
    row = df[df["to_act_type"] == "other"].iloc[0]
    assert row["distance_meters"] == 4321.0  # aggregate "other" CDF, unchanged
    assert stats["other_rest"] == 1
    # No subtype activity counters incremented, and no distance-layer fallback
    # (the rest outcome never even attempts the subtype lookup).
    for name in sc.OTHER_SUBTYPE_ACTIVITIES:
        assert stats[name] == 0
    assert stats["other_distance_layer_fallback"] == 0


def test_build_plans_df_other_errand_short_tags_leg_and_uses_subtype_distance():
    layered = {
        "other_errand_short": _single_value_distribution(555.0),
        "other": _single_value_distribution(4321.0),
        "shop": _flat_distribution(),
        "leisure": _flat_distribution(),
    }
    df, meta, unbounded, stats = sc._build_plans_df(
        _other_problem(), layered, 2.0, np.random.RandomState(2),
        other_subtype_decider=lambda mode, tt: "other_errand_short",
    )
    row = df[df["to_act_type"] == "other_errand_short"].iloc[0]
    assert row["distance_meters"] == 555.0
    assert stats["other_errand_short"] == 1
    assert stats["other_distance_layer_fallback"] == 0


def test_build_plans_df_other_subtype_distance_layer_fallback_counted():
    layered = {
        "other": _single_value_distribution(4321.0),
        "shop": _flat_distribution(),
        "leisure": _flat_distribution(),
    }
    df, meta, unbounded, stats = sc._build_plans_df(
        _other_problem(), layered, 2.0, np.random.RandomState(2),
        other_subtype_decider=lambda mode, tt: "other_escort",
    )
    row = df[df["to_act_type"] == "other_escort"].iloc[0]
    assert row["distance_meters"] == 4321.0  # fell back to aggregate "other"
    assert stats["other_escort"] == 1
    assert stats["other_distance_layer_fallback"] == 1


# ---------------------------------------------------------------------------
# (c) OFF path: byte-identical when both deciders are None (default).
# ---------------------------------------------------------------------------


def test_build_plans_df_off_path_byte_identical_leisure_and_other_deciders_none():
    problems = _bounded_problems_mixed()
    distributions = _flat_distribution()

    explicit_off_df, explicit_meta, explicit_unbounded, explicit_stats = sc._build_plans_df(
        problems, distributions, 2.0, np.random.RandomState(5),
        leisure_subtype_decider=None, other_subtype_decider=None,
    )
    default_df, default_meta, default_unbounded, default_stats = sc._build_plans_df(
        problems, distributions, 2.0, np.random.RandomState(5),
    )

    pd.testing.assert_frame_equal(explicit_off_df, default_df)
    assert explicit_meta == default_meta
    assert explicit_unbounded == default_unbounded
    assert explicit_stats == default_stats == {}


def test_build_locations_df_off_path_byte_identical_with_leisure_other_flags_false():
    candidates = gpd.GeoDataFrame(
        {
            "location_id": ["sec_0", "sec_1"],
            "offers_shop": [False, False],
            "offers_leisure": [True, False],
            "offers_other": [False, True],
            "pot_shop": [0.0, 0.0],
            "pot_shop_daily": [0.0, 0.0],
            "pot_shop_non_daily": [0.0, 0.0],
            "pot_leisure": [4.0, 0.0],
            "pot_other": [0.0, 6.0],
        },
        geometry=[geo.Point(0, 0), geo.Point(100, 100)],
        crs="EPSG:25832",
    )
    explicit_off = sc._build_locations_df(
        candidates, with_potentials=True,
        leisure_subtype_split=False, other_subtype_split=False,
    )
    default = sc._build_locations_df(candidates, with_potentials=True)
    pd.testing.assert_frame_equal(explicit_off, default)
    assert explicit_off.loc[0, "activities"] == "leisure"
    assert explicit_off.loc[1, "activities"] == "other"


# ---------------------------------------------------------------------------
# _build_locations_df: leisure/other subtype offer emission (candidate side).
# ---------------------------------------------------------------------------


def _leisure_other_split_candidates():
    return gpd.GeoDataFrame(
        {
            "location_id": ["sec_0", "sec_1"],
            "offers_shop": [False, False],
            "offers_leisure": [True, False],
            "offers_other": [False, True],
            "pot_shop": [0.0, 0.0],
            "pot_shop_daily": [0.0, 0.0],
            "pot_shop_non_daily": [0.0, 0.0],
            "pot_leisure": [4.0, 0.0],
            "pot_other": [0.0, 6.0],
        },
        geometry=[geo.Point(0, 0), geo.Point(50, 50)],
        crs="EPSG:25832",
    )


def test_build_locations_df_leisure_subtype_split_emits_four_activities():
    out = sc._build_locations_df(
        _leisure_other_split_candidates(), with_potentials=True,
        leisure_subtype_split=True,
    )
    assert out.loc[0, "activities"] == "leisure_local; leisure_visit; leisure_activity; leisure_excursion"
    # All four subtypes share the SAME pot_leisure value (no per-subtype
    # potential yet) -- this is also a regression check for the duplicate
    # "pot_leisure" column selection bug (see _build_locations_df docstring).
    assert out.loc[0, "potentials"] == "4.0; 4.0; 4.0; 4.0"
    # sec_1 offers only "other" (unaffected by leisure_subtype_split).
    assert out.loc[1, "activities"] == "other"


def test_build_locations_df_other_subtype_split_emits_subtypes_plus_aggregate():
    out = sc._build_locations_df(
        _leisure_other_split_candidates(), with_potentials=True,
        other_subtype_split=True,
    )
    # sec_0 offers only leisure (unaffected).
    assert out.loc[0, "activities"] == "leisure"
    # sec_1: three subtypes PLUS the aggregate "other" (kept for other_rest).
    assert out.loc[1, "activities"] == "other_errand_short; other_errand_long; other_escort; other"
    assert out.loc[1, "potentials"] == "6.0; 6.0; 6.0; 6.0"


def test_build_locations_df_leisure_and_other_split_together():
    out = sc._build_locations_df(
        _leisure_other_split_candidates(), with_potentials=True,
        leisure_subtype_split=True, other_subtype_split=True,
    )
    assert out.loc[0, "activities"] == "leisure_local; leisure_visit; leisure_activity; leisure_excursion"
    assert out.loc[1, "activities"] == "other_errand_short; other_errand_long; other_escort; other"


def test_build_locations_df_leisure_split_requires_potentials():
    with pytest.raises(ValueError, match="requires with_potentials"):
        sc._build_locations_df(
            _leisure_other_split_candidates(), with_potentials=False,
            leisure_subtype_split=True,
        )


def test_build_locations_df_other_split_requires_potentials():
    with pytest.raises(ValueError, match="requires with_potentials"):
        sc._build_locations_df(
            _leisure_other_split_candidates(), with_potentials=False,
            other_subtype_split=True,
        )


# ---------------------------------------------------------------------------
# carla smoke: the internal leisure/other subtype activities must be
# accepted and placed by the real chainsolvers carla solver (no KeyError on
# an unknown activity name; validates the offer_specs wiring end-to-end).
# ---------------------------------------------------------------------------


def test_carla_accepts_leisure_subtype_activities_smoke():
    cs = pytest.importorskip("chainsolvers")
    candidates = gpd.GeoDataFrame(
        {
            "location_id": ["sec_0", "sec_1"],
            "offers_shop": [False, False],
            "offers_leisure": [True, True],
            "offers_other": [False, False],
            "pot_shop": [0.0, 0.0],
            "pot_shop_daily": [0.0, 0.0],
            "pot_shop_non_daily": [0.0, 0.0],
            "pot_leisure": [4.0, 4.0],
            "pot_other": [0.0, 0.0],
        },
        geometry=[geo.Point(0, 0), geo.Point(100, 100)],
        crs="EPSG:25832",
    )
    locations_df = sc._build_locations_df(
        candidates, with_potentials=True, leisure_subtype_split=True)
    layered = {
        "leisure_excursion": _flat_distribution(),
        "leisure": _flat_distribution(),
        "shop": _flat_distribution(),
        "other": _flat_distribution(),
    }
    plans_df, meta, unbounded, stats = sc._build_plans_df(
        _leisure_problem(), layered, 2.0, np.random.RandomState(3),
        leisure_subtype_decider=lambda mode, tt: "leisure_excursion",
    )
    plans_for_cs = plans_df.drop(columns=["_leg_index", "_problem_idx"])
    ctx = cs.setup(locations_df=locations_df, solver="carla", rng_seed=7)
    res_df, _seg, _v = cs.solve(ctx=ctx, plans_df=plans_for_cs)
    placed = res_df[res_df["to_act_type"] == "leisure_excursion"]
    assert len(placed) == 1


def test_carla_accepts_other_subtype_activities_smoke():
    cs = pytest.importorskip("chainsolvers")
    candidates = gpd.GeoDataFrame(
        {
            "location_id": ["sec_0", "sec_1"],
            "offers_shop": [False, False],
            "offers_leisure": [False, False],
            "offers_other": [True, True],
            "pot_shop": [0.0, 0.0],
            "pot_shop_daily": [0.0, 0.0],
            "pot_shop_non_daily": [0.0, 0.0],
            "pot_leisure": [0.0, 0.0],
            "pot_other": [6.0, 6.0],
        },
        geometry=[geo.Point(0, 0), geo.Point(100, 100)],
        crs="EPSG:25832",
    )
    locations_df = sc._build_locations_df(
        candidates, with_potentials=True, other_subtype_split=True)
    layered = {
        "other_escort": _flat_distribution(),
        "other": _flat_distribution(),
        "shop": _flat_distribution(),
        "leisure": _flat_distribution(),
    }
    plans_df, meta, unbounded, stats = sc._build_plans_df(
        _other_problem(), layered, 2.0, np.random.RandomState(4),
        other_subtype_decider=lambda mode, tt: "other_escort",
    )
    plans_for_cs = plans_df.drop(columns=["_leg_index", "_problem_idx"])
    ctx = cs.setup(locations_df=locations_df, solver="carla", rng_seed=8)
    res_df, _seg, _v = cs.solve(ctx=ctx, plans_df=plans_for_cs)
    placed = res_df[res_df["to_act_type"] == "other_escort"]
    assert len(placed) == 1


# ---------------------------------------------------------------------------
# configure(): new flags declared, mid_dir required only when needed.
# ---------------------------------------------------------------------------


class _FakeContext:
    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.registered = {}
        self.staged = []

    def config(self, key, default=None):
        # Mirror real synpp semantics: a key's value is resolved ONCE (from an
        # override or the first-seen default) and stays fixed for subsequent
        # re-reads of the same key without a default -- exactly how
        # configure() re-reads "secondary_shop_daily_split" after declaring it.
        if key in self.registered:
            return self.registered[key]
        value = self.overrides.get(key, default)
        self.registered[key] = value
        return value

    def stage(self, name, *a, **k):
        self.staged.append(name)
        return None


def test_configure_declares_leisure_and_other_flags_default_false():
    ctx = _FakeContext()
    sc.configure(ctx)
    assert ctx.registered["secondary_leisure_subtype_split"] is False
    assert ctx.registered["secondary_other_subtype_split"] is False


def test_configure_does_not_require_mid_dir_when_all_flags_off():
    ctx = _FakeContext()
    sc.configure(ctx)
    assert "braunschweig.population.popsim.mid_dir" not in ctx.registered


def test_configure_requires_mid_dir_when_leisure_subtype_split_on():
    ctx = _FakeContext({"secondary_leisure_subtype_split": True})
    sc.configure(ctx)
    assert "braunschweig.population.popsim.mid_dir" in ctx.registered


def test_configure_requires_mid_dir_when_other_subtype_split_on():
    ctx = _FakeContext({"secondary_other_subtype_split": True})
    sc.configure(ctx)
    assert "braunschweig.population.popsim.mid_dir" in ctx.registered


# ---------------------------------------------------------------------------
# Decider construction: synthetic (non-MiD-file) Wege frames via a
# monkeypatched braunschweig.popsim.mid.load_mid_wege, mirroring the synthetic
# Wege builder in tests/test_distance_distributions_subtypes.py. Never touches
# real MiD data (local-only, not committed).
# ---------------------------------------------------------------------------


def _add_rows(rows, row_id_start, *, w_zweck, w_zwd, wegkm, n=15):
    row_id = row_id_start
    for _ in range(n):
        rows.append({
            "H_ID": row_id, "P_ID": 0, "W_ID": 0,
            "W_ZWECK": w_zweck, "W_ZWD": w_zwd,
            "hvm_imp": 4,  # car for all rows -> single mode
            "wegkm_imp": wegkm,
            "W_SZS": 8, "W_SZM": 0, "W_AZS": 8, "W_AZM": 10,
            "W_GEW": 1.0,
        })
        row_id += 1
    return row_id


def _decider_context(overrides, monkeypatch, wege_df):
    from braunschweig.popsim import mid as mid_module
    monkeypatch.setattr(mid_module, "load_mid_wege", lambda mid_dir: wege_df)
    base = {
        "secondary_distance_min_obs": 30,
        "braunschweig.population.popsim.mid_dir": "unused_dummy_dir",
    }
    base.update(overrides)
    return _FakeContext(base)


def test_build_leisure_subtype_decider_returns_none_when_off():
    ctx = _FakeContext({"secondary_leisure_subtype_split": False})
    assert sc._build_leisure_subtype_decider(ctx, 1) is None


def test_build_other_subtype_decider_returns_none_when_off():
    ctx = _FakeContext({"secondary_other_subtype_split": False})
    assert sc._build_other_subtype_decider(ctx, 1) is None


def test_build_leisure_subtype_decider_deterministic_single_group(monkeypatch):
    # All leisure (W_ZWECK=7) rows are W_ZWD=708 -> leisure_excursion marginal
    # is 1.0, every other group is 0.0 -> the decider is fully deterministic
    # regardless of the random draw.
    rows = []
    _add_rows(rows, 0, w_zweck=7, w_zwd=708, wegkm=80.0, n=40)
    wege = pd.DataFrame(rows)
    ctx = _decider_context({"secondary_leisure_subtype_split": True}, monkeypatch, wege)

    decide = sc._build_leisure_subtype_decider(ctx, random_seed=1)
    assert decide is not None
    for tt in (100.0, 500.0, 900.0, 2000.0):
        assert decide("car", tt) == "leisure_excursion"


def test_build_leisure_subtype_decider_reproducible_across_builds(monkeypatch, capsys):
    rows = []
    _add_rows(rows, 0, w_zweck=7, w_zwd=706, wegkm=5.0, n=20)   # leisure_local
    _add_rows(rows, 100, w_zweck=7, w_zwd=701, wegkm=19.0, n=20)  # leisure_visit
    _add_rows(rows, 200, w_zweck=7, w_zwd=702, wegkm=15.0, n=20)  # leisure_activity
    _add_rows(rows, 300, w_zweck=7, w_zwd=708, wegkm=80.0, n=20)  # leisure_excursion
    wege = pd.DataFrame(rows)
    ctx1 = _decider_context({"secondary_leisure_subtype_split": True}, monkeypatch, wege)
    ctx2 = _decider_context({"secondary_leisure_subtype_split": True}, monkeypatch, wege)

    decide1 = sc._build_leisure_subtype_decider(ctx1, random_seed=42)
    decide2 = sc._build_leisure_subtype_decider(ctx2, random_seed=42)

    calls = [("car", float(t)) for t in range(100, 2000, 137)]
    seq1 = [decide1(mode, tt) for mode, tt in calls]
    seq2 = [decide2(mode, tt) for mode, tt in calls]
    assert seq1 == seq2
    # Every outcome must be one of the declared leisure groups.
    assert set(seq1) <= set(sc.LEISURE_SUBTYPE_ACTIVITIES)

    out = capsys.readouterr().out
    assert "leisure subtype: marginal shares" in out


def test_build_other_subtype_decider_deterministic_errand_short_only(monkeypatch):
    # All "other" rows are errand (W_ZWECK=5) with W_ZWD=601 (errand_short) ->
    # coarse marginal errand=1.0 (escort=rest=0.0), errand marginal
    # other_errand_short=1.0 -> composed probability other_errand_short=1.0,
    # fully deterministic regardless of the random draw.
    rows = []
    _add_rows(rows, 0, w_zweck=5, w_zwd=601, wegkm=6.0, n=40)
    wege = pd.DataFrame(rows)
    ctx = _decider_context({"secondary_other_subtype_split": True}, monkeypatch, wege)

    decide = sc._build_other_subtype_decider(ctx, random_seed=1)
    assert decide is not None
    for tt in (100.0, 500.0, 900.0, 2000.0):
        assert decide("car", tt) == "other_errand_short"


def test_build_other_subtype_decider_reproducible_across_builds(monkeypatch, capsys):
    rows = []
    _add_rows(rows, 0, w_zweck=5, w_zwd=601, wegkm=6.0, n=15)     # errand_short
    _add_rows(rows, 100, w_zweck=5, w_zwd=603, wegkm=12.0, n=15)  # errand_long
    _add_rows(rows, 200, w_zweck=6, w_zwd=7704, wegkm=3.0, n=15)  # escort (W_ZWD irrelevant)
    _add_rows(rows, 300, w_zweck=10, w_zwd=999, wegkm=2.0, n=15)  # rest
    wege = pd.DataFrame(rows)
    ctx1 = _decider_context({"secondary_other_subtype_split": True}, monkeypatch, wege)
    ctx2 = _decider_context({"secondary_other_subtype_split": True}, monkeypatch, wege)

    decide1 = sc._build_other_subtype_decider(ctx1, random_seed=42)
    decide2 = sc._build_other_subtype_decider(ctx2, random_seed=42)

    calls = [("car", float(t)) for t in range(100, 2000, 137)]
    seq1 = [decide1(mode, tt) for mode, tt in calls]
    seq2 = [decide2(mode, tt) for mode, tt in calls]
    assert seq1 == seq2
    assert set(seq1) <= (set(sc.OTHER_SUBTYPE_ACTIVITIES) | {"other_rest"})

    out = capsys.readouterr().out
    assert "other subtype: coarse marginal shares" in out
    assert "errand marginal shares" in out
