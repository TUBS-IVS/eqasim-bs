"""SrV location-category integration in the chainsolvers leg loop (issue #262,
Task 8 -- design A2: draw the category AFTER the desired distance).

TDD: written BEFORE the implementation. Mirrors the fixture style of
tests/test_secondary_chainsolvers_subtypes.py (stub distributions + stub
deciders for ``_build_plans_df``; small synthetic candidate frames for
``_build_locations_df``).

Scenarios covered:
    (a) with the SrV decider active, a leisure/other leg's PLACEMENT activity is
        the drawn SrV category (or the aggregate purpose for the ``*_misc``
        categories) -- never a MiD distance-subtype name; the DISTANCE is still
        drawn from the MiD subtype layer (placement decoupled from the distance
        label).
    (b) the category is drawn from the ALREADY SAMPLED desired distance (A2
        ordering), so the SrV type<->distance correlation carries over.
    (c) shop and escort legs are untouched by the SrV decider.
    (d) marginal-fallback draws are counted in ``subtype_stats`` (fallback
        transparency), separately from the MiD subtype counters.
    (e) OFF path (``srv_location_decider=None``) stays byte-identical.
    (f) ``_build_locations_df(srv_location_types=True)`` emission matrix:
        aggregate + category activities with the right potential columns, no MiD
        subtype placement activities, shop/escort emission unchanged, fail-fast
        guards.
    (g) determinism: the real seeded decider produces identical categories for
        the same seed.
    (h) the drawn categories map back to eqasim purposes at extraction and the
        stage-level prerequisite guard fails fast.
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely import geometry as geo

from braunschweig.synthesis.locations import secondary_chainsolvers as sc

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_MODES = ("car", "car_passenger", "pt", "bicycle", "walk")


def _flat_distribution():
    values = np.array([800.0, 1000.0, 1200.0, 1500.0])
    cdf = np.array([0.25, 0.5, 0.75, 1.0])
    return {
        mode: {
            "bounds": np.array([], dtype=float),
            "distributions": [{"values": values.copy(), "cdf": cdf.copy()}],
        }
        for mode in _MODES
    }


def _single_value_distribution(value: float):
    """A degenerate one-value CDF: every draw returns exactly ``value``."""
    return {
        mode: {
            "bounds": np.array([], dtype=float),
            "distributions": [{"values": np.array([value]), "cdf": np.array([1.0])}],
        }
        for mode in _MODES
    }


def _problem(person_id: int, purpose: str):
    """One bounded problem with a single leg of ``purpose``."""
    return {
        "person_id": person_id, "activity_index": 2, "size": 1,
        "purposes": [purpose], "modes": ["car", "car"],
        "travel_times": np.array([600.0, 600.0]),
        "origin": np.array([[0.0, 0.0]]),
        "destination": np.array([[1000.0, 1000.0]]),
    }


def _leisure_and_other_problems():
    return [_problem(200, "leisure"), _problem(300, "other")]


def _variable_legs(plans_df):
    """The variable (secondary) legs only: every ``_problem`` above ends on a
    fixed anchor, whose leg is emitted with the placeholder activity ``"home"``
    (see ``_problem_legs`` / the ``__fixed__`` sentinel)."""
    return plans_df[plans_df["to_act_type"] != "home"]


def _constant_srv_decider(category: str, used_marginal: bool = False, log=None):
    """Stub SrV decider returning a fixed ``(category, used_marginal)``; when
    ``log`` is a list, every call's ``(purpose, mode, distance_m)`` is recorded
    so the A2 ordering (distance known BEFORE the draw) can be asserted."""
    def decide(purpose, mode, distance_m):
        if log is not None:
            log.append((purpose, mode, distance_m))
        return category, used_marginal
    return decide


def _by_purpose_srv_decider(category_by_purpose, used_marginal: bool = False,
                            log=None):
    """Stub SrV decider returning a per-purpose category; when ``log`` is a list,
    every call's ``(purpose, mode, distance_m)`` is recorded so the A2 ordering
    (distance known BEFORE the draw) can be asserted."""
    def decide(purpose, mode, distance_m):
        if log is not None:
            log.append((purpose, mode, distance_m))
        return category_by_purpose[purpose], used_marginal
    return decide


_SYNTHETIC_PROBS_CSV = """\
# Synthetic pinned-probability fixture for the leg-loop integration tests (#262).
purpose,mode,band_lower_km,band_upper_km,is_marginal,category,probability,n_legs_unweighted
leisure,all,0.0,inf,1,leisure_culture,0.5,10
leisure,all,0.0,inf,1,leisure_outdoor,0.5,10
other,all,0.0,inf,1,errand_service,0.5,10
other,all,0.0,inf,1,errand_authority_medical,0.5,10
leisure,car,0.0,1.0,0,leisure_culture,0.5,50
leisure,car,0.0,1.0,0,leisure_gastronomy,0.5,50
leisure,car,1.0,2.0,0,leisure_outdoor,1.0,50
other,car,0.0,1.0,0,errand_service,1.0,50
"""


class _Ctx:
    """Minimal synpp ExecuteContext stub (declared-config semantics: one-argument
    ``config``), mirroring tests/test_srv_location_decider.py."""
    def __init__(self, cfg):
        self._cfg = cfg

    def config(self, key):
        if key not in self._cfg:
            raise KeyError(f"_Ctx: no value for config key {key!r}.")
        return self._cfg[key]


def _write_probs(tmp_path):
    path = tmp_path / "srv_location_type_by_distance.csv"
    path.write_text(_SYNTHETIC_PROBS_CSV, encoding="utf-8")
    return str(path)


# ---------------------------------------------------------------------------
# (a)/(b) placement decoupled from the distance label; A2 draw ordering.
# ---------------------------------------------------------------------------


def test_srv_category_replaces_mid_subtype_as_placement_activity():
    """The MiD subtype still selects the DISTANCE layer; the SrV category alone
    decides the placement activity."""
    layered = {
        "leisure_excursion": _single_value_distribution(99000.0),
        "leisure": _single_value_distribution(1000.0),
        "other_errand_short": _single_value_distribution(555.0),
        "other": _single_value_distribution(4321.0),
        "shop": _flat_distribution(),
    }
    calls = []
    df, _meta, _unbounded, stats, _desired_by_category = sc._build_plans_df(
        _leisure_and_other_problems(), layered, 2.0, np.random.RandomState(1),
        leisure_subtype_decider=lambda mode, tt: "leisure_excursion",
        other_subtype_decider=lambda mode, tt: "other_errand_short",
        srv_location_decider=_by_purpose_srv_decider(
            {"leisure": "leisure_outdoor", "other": "errand_service"}, log=calls),
    )

    legs = _variable_legs(df)
    placement = list(legs["to_act_type"])
    assert placement == ["leisure_outdoor", "errand_service"]
    # No MiD subtype name may survive as a placement activity.
    assert not (set(placement)
                & (set(sc.LEISURE_SUBTYPE_ACTIVITIES) | set(sc.OTHER_SUBTYPE_ACTIVITIES)))
    # Distances still come from the MiD SUBTYPE layers, not the aggregates.
    assert list(legs["distance_meters"]) == [99000.0, 555.0]
    # A2 ordering: the decider saw the already-sampled desired distance.
    assert calls == [("leisure", "car", 99000.0), ("other", "car", 555.0)]
    # MiD subtype counters keep counting (unchanged), SrV counters are separate.
    assert stats["leisure_excursion"] == 1
    assert stats["other_errand_short"] == 1
    assert stats[sc.SRV_LOCATION_STAT_PREFIX + "leisure_outdoor"] == 1
    assert stats[sc.SRV_LOCATION_STAT_PREFIX + "errand_service"] == 1
    assert stats[sc.srv_location_marginal_fallback_stat("leisure")] == 0
    assert stats[sc.srv_location_marginal_fallback_stat("other")] == 0


def test_srv_misc_categories_place_on_the_aggregate_purpose():
    layered = {
        "leisure": _single_value_distribution(1000.0),
        "other": _single_value_distribution(2000.0),
        "shop": _flat_distribution(),
    }
    df, _meta, _unbounded, stats, _desired_by_category = sc._build_plans_df(
        _leisure_and_other_problems(), layered, 2.0, np.random.RandomState(1),
        srv_location_decider=_by_purpose_srv_decider(
            {"leisure": "leisure_misc", "other": "other_misc"}),
    )
    assert list(_variable_legs(df)["to_act_type"]) == ["leisure", "other"]
    assert stats[sc.SRV_LOCATION_STAT_PREFIX + "leisure_misc"] == 1
    assert stats[sc.SRV_LOCATION_STAT_PREFIX + "other_misc"] == 1


def test_srv_leisure_visit_category_is_a_placement_activity():
    layered = {"leisure": _single_value_distribution(1000.0),
               "other": _flat_distribution(), "shop": _flat_distribution()}
    df, _meta, _unbounded, stats, _desired_by_category = sc._build_plans_df(
        [_problem(200, "leisure")], layered, 2.0, np.random.RandomState(1),
        srv_location_decider=_constant_srv_decider("leisure_visit"),
    )
    assert list(_variable_legs(df)["to_act_type"]) == ["leisure_visit"]
    # The SrV counter -- NOT the MiD leisure_visit subtype counter, which is not
    # even allocated here (no leisure subtype decider).
    assert stats[sc.SRV_LOCATION_STAT_PREFIX + "leisure_visit"] == 1
    assert "leisure_visit" not in stats


def test_srv_and_mid_leisure_visit_counters_do_not_collide():
    """``leisure_visit`` is BOTH a MiD subtype and an SrV category: the two
    counters must stay separate, or both log lines report inflated counts."""
    layered = {"leisure": _single_value_distribution(1000.0),
               "other": _flat_distribution(), "shop": _flat_distribution()}
    _df, _meta, _unbounded, stats, _desired_by_category = sc._build_plans_df(
        [_problem(200, "leisure"), _problem(201, "leisure")],
        layered, 2.0, np.random.RandomState(1),
        leisure_subtype_decider=lambda mode, tt: "leisure_visit",
        srv_location_decider=_constant_srv_decider("leisure_visit"),
    )
    assert stats["leisure_visit"] == 2                                   # MiD
    assert stats[sc.SRV_LOCATION_STAT_PREFIX + "leisure_visit"] == 2     # SrV


# ---------------------------------------------------------------------------
# (c) shop / escort legs untouched.
# ---------------------------------------------------------------------------


def test_srv_decider_does_not_touch_shop_or_escort_legs():
    layered = {
        "shop_daily": _single_value_distribution(300.0),
        "shop": _flat_distribution(),
        "leisure": _flat_distribution(),
        "escort": _single_value_distribution(700.0),
        "other": _flat_distribution(),
    }
    problems = [_problem(400, "shop"), _problem(500, "escort")]
    df, _meta, _unbounded, stats, _desired_by_category = sc._build_plans_df(
        problems, layered, 2.0, np.random.RandomState(3),
        shop_subtype_decider=lambda mode, tt: "shop_daily",
        escort_location_decider=lambda: "escort_edu_school",
        srv_location_decider=_constant_srv_decider("leisure_outdoor"),
    )
    legs = _variable_legs(df)
    assert list(legs["to_act_type"]) == ["shop_daily", "escort_edu_school"]
    assert list(legs["distance_meters"]) == [300.0, 700.0]
    # Every SrV category counter stays at zero: no leisure/other leg in this set.
    for name in sc.SRV_LEISURE_CATEGORIES + sc.SRV_OTHER_CATEGORIES:
        assert stats[sc.SRV_LOCATION_STAT_PREFIX + name] == 0


# ---------------------------------------------------------------------------
# (d) marginal-fallback transparency.
# ---------------------------------------------------------------------------


def test_srv_marginal_fallback_counted_in_subtype_stats():
    layered = {"leisure": _single_value_distribution(1000.0),
               "other": _single_value_distribution(2000.0),
               "shop": _flat_distribution()}
    _df, _meta, _unbounded, stats, _desired_by_category = sc._build_plans_df(
        _leisure_and_other_problems(), layered, 2.0, np.random.RandomState(1),
        srv_location_decider=_by_purpose_srv_decider(
            {"leisure": "leisure_culture", "other": "errand_service"},
            used_marginal=True),
    )
    # Counted PER PURPOSE, never pooled (review finding: a pooled counter lets a
    # badly covered purpose hide behind a well covered one).
    assert stats[sc.srv_location_marginal_fallback_stat("leisure")] == 1
    assert stats[sc.srv_location_marginal_fallback_stat("other")] == 1
    assert stats[sc.SRV_LOCATION_STAT_PREFIX + "leisure_culture"] == 1
    assert stats[sc.SRV_LOCATION_STAT_PREFIX + "errand_service"] == 1


def test_srv_marginal_fallback_counters_are_attributed_to_their_own_purpose():
    """A purpose whose (mode, band) cells are missing must not push its fallback
    count onto the other purpose's books."""
    layered = {"leisure": _single_value_distribution(1000.0),
               "other": _single_value_distribution(2000.0),
               "shop": _flat_distribution()}

    def decide(purpose, mode, distance_m):
        # "other" always falls back to its marginal; "leisure" never does.
        return (("errand_service", True) if purpose == "other"
                else ("leisure_culture", False))

    _df, _meta, _unbounded, stats, _desired_by_category = sc._build_plans_df(
        [_problem(200, "leisure"), _problem(201, "leisure"), _problem(300, "other")],
        layered, 2.0, np.random.RandomState(1), srv_location_decider=decide,
    )
    assert stats[sc.srv_location_marginal_fallback_stat("leisure")] == 0
    assert stats[sc.srv_location_marginal_fallback_stat("other")] == 1


def test_srv_counters_absent_when_decider_off():
    layered = {"leisure": _flat_distribution(), "other": _flat_distribution(),
               "shop": _flat_distribution()}
    _df, _meta, _unbounded, stats, _desired_by_category = sc._build_plans_df(
        _leisure_and_other_problems(), layered, 2.0, np.random.RandomState(1),
    )
    assert stats == {}


# ---------------------------------------------------------------------------
# (e) OFF path: byte-identical.
# ---------------------------------------------------------------------------


def test_off_path_byte_identical_srv_decider_none():
    problems = _leisure_and_other_problems()
    layered = {
        "leisure_excursion": _single_value_distribution(99000.0),
        "leisure": _single_value_distribution(1000.0),
        "other": _single_value_distribution(4321.0),
        "shop": _flat_distribution(),
    }
    explicit_df, explicit_meta, explicit_unbounded, explicit_stats, _desired_by_category = sc._build_plans_df(
        problems, layered, 2.0, np.random.RandomState(7),
        leisure_subtype_decider=lambda mode, tt: "leisure_excursion",
        srv_location_decider=None,
    )
    default_df, default_meta, default_unbounded, default_stats, _desired_by_category = sc._build_plans_df(
        problems, layered, 2.0, np.random.RandomState(7),
        leisure_subtype_decider=lambda mode, tt: "leisure_excursion",
    )
    pd.testing.assert_frame_equal(explicit_df, default_df)
    assert explicit_meta == default_meta
    assert explicit_unbounded == default_unbounded
    assert explicit_stats == default_stats
    # Unchanged MiD behaviour: the subtype IS the placement activity when the
    # SrV decider is off.
    assert list(_variable_legs(default_df)["to_act_type"]) == ["leisure_excursion", "other"]


# ---------------------------------------------------------------------------
# (f) _build_locations_df emission matrix.
# ---------------------------------------------------------------------------


_EXTERNAL_EWZ = 7000.0


def _srv_candidates():
    """Six candidate rows covering every emission branch:

    ``sec_b_0``  leisure building mapped to leisure_culture (positive potential)
    ``sec_b_1``  errand building mapped to errand_service (positive potential)
    ``sec_lu_0`` ATKIS landuse grid point, leisure_outdoor only
    ``sec_res_0`` residential visit candidate (offers_visit / pot_visit)
    ``sec_2``    legacy shop-only catalog row
    ``03151000`` external Gemeinde centroid after
                 ``append_external_category_escapes``: all base purposes AND all
                 category escapes at the same ewz potential
    """
    n = 6
    frame = {
        "location_id": ["sec_b_0", "sec_b_1", "sec_lu_0", "sec_res_0", "sec_2", "03151000"],
        "commune_id": ["1", "1", "1", "1", "1", "03151000"],
        "offers_shop": [False, False, False, False, True, True],
        "offers_leisure": [True, False, False, False, False, True],
        "offers_other": [False, True, False, False, False, True],
        "pot_shop": [0.0, 0.0, 0.0, 0.0, 5.0, _EXTERNAL_EWZ],
        "pot_shop_daily": [0.0] * n,
        "pot_shop_non_daily": [0.0] * n,
        "pot_leisure": [4.0, 0.0, 0.0, 0.0, 0.0, _EXTERNAL_EWZ],
        "pot_other": [0.0, 6.0, 0.0, 0.0, 0.0, _EXTERNAL_EWZ],
        "offers_leisure_culture": [True, False, False, False, False, True],
        "pot_leisure_culture": [4.0, 0.0, 0.0, 0.0, 0.0, _EXTERNAL_EWZ],
        "offers_leisure_gastronomy": [False, False, False, False, False, True],
        "pot_leisure_gastronomy": [0.0, 0.0, 0.0, 0.0, 0.0, _EXTERNAL_EWZ],
        "offers_leisure_sports": [False, False, False, False, False, True],
        "pot_leisure_sports": [0.0, 0.0, 0.0, 0.0, 0.0, _EXTERNAL_EWZ],
        "offers_leisure_outdoor": [False, False, True, False, False, True],
        "pot_leisure_outdoor": [0.0, 0.0, 100.0, 0.0, 0.0, _EXTERNAL_EWZ],
        "offers_errand_authority_medical": [False, False, False, False, False, True],
        "pot_errand_authority_medical": [0.0, 0.0, 0.0, 0.0, 0.0, _EXTERNAL_EWZ],
        "offers_errand_service": [False, True, False, False, False, True],
        "pot_errand_service": [0.0, 6.0, 0.0, 0.0, 0.0, _EXTERNAL_EWZ],
        sc.VISIT_OFFER_COLUMN: [False, False, False, True, False, False],
        sc.VISIT_POTENTIAL_COLUMN: [0.0, 0.0, 0.0, 9.0, 0.0, 0.0],
    }
    return gpd.GeoDataFrame(
        frame,
        geometry=[geo.Point(10 * i, 10 * i) for i in range(n)],
        crs="EPSG:25832",
    )


def test_build_locations_df_srv_emits_aggregate_plus_category_activities():
    out = sc._build_locations_df(
        _srv_candidates(), with_potentials=True, srv_location_types=True)
    acts = [row.split("; ") if row else [] for row in out["activities"]]
    pots = [row.split("; ") if row else [] for row in out["potentials"]]

    # sec_b_0: aggregate "leisure" (for leisure_misc legs) + its own category.
    assert acts[0] == ["leisure", "leisure_culture"]
    assert pots[0] == ["4.0", "4.0"]
    # sec_b_1: aggregate "other" (for other_misc legs) + errand_service.
    assert acts[1] == ["other", "errand_service"]
    assert pots[1] == ["6.0", "6.0"]
    # sec_lu_0: landuse category only (it offers no base purpose).
    assert acts[2] == ["leisure_outdoor"]
    assert pots[2] == ["100.0"]
    # sec_res_0: the SrV leisure_visit category routes onto offers_visit/pot_visit.
    assert acts[3] == ["leisure_visit"]
    assert pots[3] == ["9.0"]
    # sec_2: shop emission unchanged.
    assert acts[4] == ["shop"]
    assert pots[4] == ["5.0"]
    # External Gemeinde centroid: a candidate for EVERY category leg (the
    # long-distance escape), at its aggregate ewz potential.
    assert acts[5] == [
        "shop", "leisure", "leisure_culture", "leisure_gastronomy",
        "leisure_outdoor", "leisure_sports", "other",
        "errand_authority_medical", "errand_service",
    ]
    assert set(pots[5]) == {str(_EXTERNAL_EWZ)}


def test_build_locations_df_srv_external_centroid_serves_every_category_leg():
    """Long-distance reach parity (review finding): every SrV category leg must
    find the external centroid among its candidates, exactly as the plain
    leisure/other legs did on the OFF path."""
    out = sc._build_locations_df(
        _srv_candidates(), with_potentials=True, srv_location_types=True)
    external_acts = set(out.loc[5, "activities"].split("; "))
    for category in sc.EXTERNAL_CATEGORY_ESCAPE_CATEGORIES:
        assert category in external_acts, category
    # leisure_visit stays residential-only (it is not an external escape).
    assert "leisure_visit" not in external_acts
    assert out.loc[3, "activities"] == "leisure_visit"


def test_build_locations_df_srv_drops_mid_subtype_placement_activities():
    out = sc._build_locations_df(
        _srv_candidates(), with_potentials=True, srv_location_types=True,
        leisure_subtype_split=True, other_subtype_split=True,
        leisure_visit_building_potential=True,
    )
    emitted = {act for row in out["activities"] for act in row.split("; ") if act}
    # leisure_visit is an SrV category too, so only the other three MiD leisure
    # subtypes plus the two MiD errand subtypes must disappear.
    forbidden = (set(sc.LEISURE_SUBTYPE_ACTIVITIES) - {"leisure_visit"}) \
        | {"other_errand_short", "other_errand_long", "other_escort"}
    assert not (emitted & forbidden)
    assert "leisure" in emitted and "other" in emitted
    assert {"leisure_culture", "leisure_outdoor", "errand_service", "leisure_visit"} <= emitted


def test_build_locations_df_srv_zero_potential_category_is_skipped():
    candidates = _srv_candidates()
    # A building that CLAIMS the category but carries no potential for it is not
    # a candidate for that category (zero-skip, like the shop subtypes).
    candidates.loc[0, "offers_leisure_gastronomy"] = True
    candidates.loc[0, "pot_leisure_gastronomy"] = 0.0
    out = sc._build_locations_df(
        candidates, with_potentials=True, srv_location_types=True)
    assert out.loc[0, "activities"] == "leisure; leisure_culture"


def test_build_locations_df_srv_keeps_escort_emission():
    candidates = _srv_candidates()
    for column in sc.ESCORT_EDU_OFFER_BY_TYPE.values():
        candidates[column] = False
    candidates[sc.ESCORT_RESIDENTIAL_OFFER_COLUMN] = [
        False, False, False, True, False, False]
    candidates["pot_escort_edu"] = 0.0
    out = sc._build_locations_df(
        candidates, with_potentials=True, srv_location_types=True,
        escort_purpose=True)
    assert out.loc[0, "activities"] == "leisure; leisure_culture; escort_leisure"
    assert out.loc[1, "activities"] == "other; errand_service; escort_other"
    assert out.loc[3, "activities"] == "leisure_visit; escort_residential"


def test_build_locations_df_srv_requires_potentials():
    with pytest.raises(ValueError, match="srv_location_types requires with_potentials"):
        sc._build_locations_df(
            _srv_candidates(), with_potentials=False, srv_location_types=True)


def test_build_locations_df_srv_missing_category_column_raises():
    candidates = _srv_candidates().drop(columns=["pot_leisure_outdoor"])
    with pytest.raises(ValueError, match="pot_leisure_outdoor"):
        sc._build_locations_df(
            candidates, with_potentials=True, srv_location_types=True)


def test_build_locations_df_srv_off_is_byte_identical():
    """The category columns may be present on the candidate frame while the flag
    is OFF (a candidate set built for an A/B run): the emission must then be
    identical to the flag's absence."""
    candidates = _srv_candidates()
    explicit_off = sc._build_locations_df(
        candidates, with_potentials=True, srv_location_types=False)
    default = sc._build_locations_df(candidates, with_potentials=True)
    pd.testing.assert_frame_equal(explicit_off, default)
    assert explicit_off.loc[0, "activities"] == "leisure"
    assert explicit_off.loc[2, "activities"] == ""


# ---------------------------------------------------------------------------
# carla smoke: the SrV category activity names must be accepted and placed by
# the real chainsolvers carla solver (no KeyError on an unknown activity name;
# validates the offer_specs wiring end-to-end), mirroring the leisure/other
# subtype smokes in tests/test_secondary_chainsolvers_subtypes.py.
# ---------------------------------------------------------------------------


def test_carla_accepts_srv_category_activities_smoke():
    cs = pytest.importorskip("chainsolvers")
    candidates = _srv_candidates()
    # A second leisure_outdoor candidate so carla has a real choice for the leg.
    candidates.loc[4, "offers_leisure_outdoor"] = True
    candidates.loc[4, "pot_leisure_outdoor"] = 50.0
    locations_df = sc._build_locations_df(
        candidates, with_potentials=True, srv_location_types=True)
    layered = {"leisure": _flat_distribution(), "other": _flat_distribution(),
               "shop": _flat_distribution()}
    plans_df, _meta, _unbounded, _stats, _desired_by_category = sc._build_plans_df(
        [_problem(200, "leisure")], layered, 2.0, np.random.RandomState(3),
        srv_location_decider=_constant_srv_decider("leisure_outdoor"),
    )
    plans_for_cs = plans_df.drop(columns=["_leg_index", "_problem_idx"])
    ctx = cs.setup(locations_df=locations_df, solver="carla", rng_seed=7)
    result_df, _segments, _valid = cs.solve(ctx=ctx, plans_df=plans_for_cs)
    assert len(result_df[result_df["to_act_type"] == "leisure_outdoor"]) == 1


# ---------------------------------------------------------------------------
# (g) determinism with the REAL seeded decider (end-to-end through the loop).
# ---------------------------------------------------------------------------


def test_real_decider_categories_are_deterministic_for_one_seed(tmp_path):
    path = _write_probs(tmp_path)
    cfg = {"secondary_srv_location_types": True,
           "srv_location_type_probs_path": path}
    problems = [_problem(600 + i, "leisure") for i in range(12)]
    layered = {"leisure": _flat_distribution(), "other": _flat_distribution(),
               "shop": _flat_distribution()}

    def run():
        decider = sc._build_srv_location_decider(_Ctx(cfg), random_seed=99)
        df, _m, _u, stats, _desired_by_category = sc._build_plans_df(
            problems, layered, 2.0, np.random.RandomState(4),
            srv_location_decider=decider)
        return list(_variable_legs(df)["to_act_type"]), stats

    acts_a, stats_a = run()
    acts_b, stats_b = run()
    assert acts_a == acts_b
    assert stats_a == stats_b
    # Every placement activity is an SrV category or the aggregate purpose.
    assert set(acts_a) <= set(sc.SRV_LEISURE_CATEGORIES) | {"leisure"}
    # The fixture's car cells cover both distance bands, so no marginal fallback.
    assert stats_a[sc.srv_location_marginal_fallback_stat("leisure")] == 0


# ---------------------------------------------------------------------------
# (h) extraction map-back + stage prerequisite guard.
# ---------------------------------------------------------------------------


def test_extract_locations_keeps_srv_category_legs():
    categories = list(sc.SRV_LEISURE_CATEGORIES) + list(sc.SRV_OTHER_CATEGORIES)
    n = len(categories)
    result_df = pd.DataFrame({
        "unique_person_id": ["7#0"] * n,
        "unique_leg_id": [f"7#0#{i}" for i in range(n)],
        "to_act_type": categories,
        "to_x": [float(10 * i) for i in range(n)],
        "to_y": [float(10 * i) for i in range(n)],
        "to_act_identifier": [f"L{i}" for i in range(n)],
    })
    meta = [{"problem_idx": 0, "person_id": 7, "activity_index": 3, "n_secondary": n}]
    secondary = gpd.GeoDataFrame(
        {"location_id": [f"L{i}" for i in range(n)]},
        geometry=[geo.Point(10 * i, 10 * i) for i in range(n)],
        crs="EPSG:25832",
    )
    df_loc, _df_conv = sc._extract_locations(
        result_df, meta, secondary, crs="EPSG:25832")
    # No SrV-category leg may be silently dropped at extraction.
    assert len(df_loc) == n
    assert list(df_loc["location_id"]) == [f"L{i}" for i in range(n)]


_ALL_PREREQUISITES_ON = dict(
    srv_location_types=True,
    secondary_building_potentials=True,
    leisure_subtype_split=True,
    other_subtype_split=True,
    leisure_visit_building_potential=True,
)


def test_srv_prerequisites_pass_when_all_flags_on():
    sc._validate_srv_location_type_prerequisites(**_ALL_PREREQUISITES_ON)


@pytest.mark.parametrize("missing", [
    "secondary_building_potentials",
    "leisure_subtype_split",
    "other_subtype_split",
    "leisure_visit_building_potential",
])
def test_srv_prerequisites_fail_fast_per_missing_flag(missing):
    kwargs = dict(_ALL_PREREQUISITES_ON)
    kwargs[missing] = False
    with pytest.raises(RuntimeError, match="secondary_srv_location_types requires"):
        sc._validate_srv_location_type_prerequisites(**kwargs)


def test_srv_prerequisites_inert_when_flag_off():
    sc._validate_srv_location_type_prerequisites(
        srv_location_types=False,
        secondary_building_potentials=False,
        leisure_subtype_split=False,
        other_subtype_split=False,
        leisure_visit_building_potential=False,
    )


# ---------------------------------------------------------------------------
# configure(): both keys declared UNCONDITIONALLY (the #201 short-circuit trap).
# ---------------------------------------------------------------------------


class _ConfigureCtx:
    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.registered = {}
        self.staged = []

    def config(self, key, default=None):
        if key in self.registered:
            return self.registered[key]
        value = self.overrides.get(key, default)
        self.registered[key] = value
        return value

    def stage(self, name, *a, **k):
        self.staged.append(name)
        return None


def test_configure_declares_srv_location_keys_with_documented_defaults():
    ctx = _ConfigureCtx()
    sc.configure(ctx)
    assert ctx.registered["secondary_srv_location_types"] is False
    assert ctx.registered["srv_location_type_probs_path"] == \
        sc.DEFAULT_SRV_LOCATION_TYPE_PROBS_PATH
    assert sc.DEFAULT_SRV_LOCATION_TYPE_PROBS_PATH == \
        "eqasim-data/data/braunschweig/srv/srv2023_location_type_by_distance.csv"


def test_configure_declares_srv_keys_even_with_every_other_flag_on():
    """Regression guard for the documented short-circuit trap (issue #201): the
    keys must be declared whatever the other flags are."""
    ctx = _ConfigureCtx({
        "secondary_building_potentials": False,
        "leisure_visit_building_potential": True,
        "escort_purpose": True,
        "secondary_srv_location_types": True,
    })
    sc.configure(ctx)
    assert ctx.registered["secondary_srv_location_types"] is True
    assert "srv_location_type_probs_path" in ctx.registered


# ---------------------------------------------------------------------------
# Draw-rate logging (fallback transparency).
# ---------------------------------------------------------------------------


def _draw_stats(*, leisure_counts=None, other_counts=None,
                marginal_leisure=0, marginal_other=0):
    stats = {sc.SRV_LOCATION_STAT_PREFIX + name: 0
             for name in sc.SRV_LEISURE_CATEGORIES + sc.SRV_OTHER_CATEGORIES}
    for name, count in (leisure_counts or {}).items():
        stats[sc.SRV_LOCATION_STAT_PREFIX + name] = count
    for name, count in (other_counts or {}).items():
        stats[sc.SRV_LOCATION_STAT_PREFIX + name] = count
    stats[sc.srv_location_marginal_fallback_stat("leisure")] = marginal_leisure
    stats[sc.srv_location_marginal_fallback_stat("other")] = marginal_other
    return stats


def test_srv_draw_summary_lines_report_shares_and_per_purpose_marginal_rate():
    stats = _draw_stats(
        leisure_counts={"leisure_outdoor": 3, "leisure_visit": 1},
        other_counts={"errand_service": 4},
        marginal_leisure=1, marginal_other=1,
    )
    leisure_line, other_line, total_line = sc._srv_location_draw_summary_lines(stats)

    assert "srv location draw (leisure)" in leisure_line
    assert "leisure_outdoor 3 (75.0%)" in leisure_line
    assert "1/4 (25.0%)" in leisure_line            # leisure's OWN rate
    assert "srv location draw (other)" in other_line
    assert "errand_service 4 (100.0%)" in other_line
    assert "1/4 (25.0%)" in other_line              # other's OWN rate
    assert "marginal fallback total 2/8 (25.0%)" in total_line


def test_srv_draw_summary_warns_per_purpose_and_cannot_be_masked_by_the_other():
    """Review finding: with a POOLED rate a 50% "other" failure hid behind a
    large, well-covered leisure side. The warning must fire on the purpose that
    actually failed -- and only on it."""
    stats = _draw_stats(
        leisure_counts={"leisure_outdoor": 100},
        other_counts={"errand_service": 4},
        marginal_leisure=0, marginal_other=2,
    )
    leisure_line, other_line, total_line = sc._srv_location_draw_summary_lines(stats)

    assert "WARNING" not in leisure_line               # 0/100 -> no warning
    assert other_line.count("WARNING") == 1            # 2/4 = 50% -> warning
    # Pooled rate is only 2/104 (1.9%) -- exactly the masking the split prevents.
    assert "marginal fallback total 2/104 (1.9%)" in total_line
    assert "WARNING" not in total_line


def test_srv_draw_summary_handles_a_purpose_without_any_legs():
    stats = _draw_stats(leisure_counts={"leisure_outdoor": 5})
    leisure_line, other_line, _total = sc._srv_location_draw_summary_lines(stats)
    assert "0 bounded other legs" in other_line
    assert "0/0 (0.0%)" in other_line                  # no ZeroDivisionError
    assert "WARNING" not in other_line
    assert "WARNING" not in leisure_line
