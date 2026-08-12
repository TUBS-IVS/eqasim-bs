"""
Chainsolvers-based secondary location assignment (TASK-CS01).

Drop-in replacement for ``synthesis.population.spatial.secondary.locations``
that delegates the spatial point-placement step to the
`chainsolvers <https://github.com/TUBS-IVS/chainsolvers>`_ package
(carla solver, default distance-based scoring) instead of the eqasim
RDA-style ``GravityChainSolver``.

The stage produces the same output schema as the legacy stage:
``(df_locations, df_convergence)`` with

    df_locations  : GeoDataFrame [person_id, activity_index, location_id, geometry]
    df_convergence: DataFrame    [valid, size]

so it can be wired in via a synpp ``aliases`` entry without touching
any downstream consumer:

    aliases:
      synthesis.population.spatial.secondary.locations: braunschweig.synthesis.locations.secondary_chainsolvers

Per-leg desired distances are sampled once from the existing
``synthesis.population.spatial.secondary.distance_distributions``
mode-conditional CDFs (the same source the legacy ``CustomDistanceSampler``
uses); chainsolvers' carla solver then performs its own search over
the candidate facility set to minimise the (default) distance-error
score. The leisure-correction factor and CDF-resampling tweaks of the
legacy stage are preserved 1:1 so the comparison stays apples-to-apples
on the input distribution side.
"""

from __future__ import annotations

import copy
import multiprocessing as mp
import time
from typing import Any, Dict, List, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd
import shapely.geometry as geo

from braunschweig import parallelism
from braunschweig.calibration.secondary_measurement import boundary_clip_share
from synthesis.population.spatial.secondary.problems import (
    find_assignment_problems,
)


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without a synpp context)
# ---------------------------------------------------------------------------

def external_candidates_cordon_warning(external_on, cordon_on):
    """Return a warning string when external secondary candidates are enabled but
    the cordon cutter is off (the resulting boundary-crossing trips would not be
    converted into 'outside' activities and would be unroutable in MATSim), else None."""
    if external_on and not cordon_on:
        return ("[braunschweig.secondary_chainsolvers] WARNING: "
                "secondary_external_candidates is ON but cordon_enabled is OFF -- "
                "long-distance secondary activities at external Gemeinde centroids "
                "will not be converted to 'outside' activities and may be unroutable "
                "in MATSim. Enable cordon_enabled or disable secondary_external_candidates.")
    return None


# ---------------------------------------------------------------------------
# synpp configure
# ---------------------------------------------------------------------------

def configure(context):
    context.stage("synthesis.population.trips")
    context.stage("synthesis.population.sampled")
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("synthesis.population.spatial.primary.locations")
    context.stage("synthesis.population.spatial.secondary.distance_distributions")
    context.stage("synthesis.locations.secondary")

    context.config("random_seed")
    context.config("processes")

    DEFAULT_LEISURE_CORRECTION_FACTOR = 2.0
    context.config("leisure_correction_factor", DEFAULT_LEISURE_CORRECTION_FACTOR)

    # chainsolvers tuning (defaults follow the package quickstart).
    context.config("braunschweig.chainsolvers.solver", "carla")
    # Fallback strategy for problems carla cannot solve (unbounded
    # chains, sparse candidate regions, etc.):
    #   "rda"    — eqasim's GravityChainSolver / AngularTailSolver /
    #              CustomFreeChainSolver pipeline (default; preserves
    #              the legacy distance-error objective on failures).
    #   "random" — random candidate of the matching purpose (legacy
    #              stop-gap; lower quality, used pre-2026-04-26).
    context.config("braunschweig.chainsolvers.fallback", "rda")

    # Parallel chain solving (flag-gated; default OFF -> byte-identical serial
    # path). Person chains are independent, so they are sharded across worker
    # processes, each with its own chainsolvers context seeded deterministically
    # from random_seed and the shard index. The parallel result is fully
    # reproducible but is a DIFFERENT (equally valid) Monte-Carlo realisation
    # than the single-RNG serial path, and depends on the worker count -- so
    # reproducing a parallel run requires the same chainsolvers.processes.
    context.config("braunschweig.chainsolvers.parallel", False)
    # Worker count for parallel solving. None -> fall back to the global
    # "processes" config. Decoupled from "processes" so the embarrassingly
    # parallel chain solve can use more cores than the (memory-bound) MATSim
    # mobsim without changing the MATSim thread count.
    context.config("braunschweig.chainsolvers.processes", None)

    # Building-potential scorer (flag-gated; default ON). When enabled, each
    # candidate's per-activity potential (retail / leisure / generic) is attached
    # from the building footprints and forwarded to the chainsolvers combined
    # Scorer. When disabled the stage is byte-identical to the pre-C3 behaviour
    # (distance-only scoring, no potentials column in locations_df).
    sec_enabled = context.config("secondary_building_potentials", True)
    context.config("secondary_scorer_mode", "combined")
    context.config("secondary_scorer_pot_weight", 1.0)
    context.config("secondary_scorer_dist_dev_weight", 1.0)
    # Attractiveness transform applied to building potentials before scoring:
    # "linear" (default, byte-identical to before), "log1p" (log(1+P),
    # the calibrated-MNL form), or "log". Forwarded to chainsolvers Scorer.
    context.config("secondary_scorer_attr_transform", "linear")
    # Carla candidate-selection strategy: "top_n" (default, byte-identical),
    # "top_n_spatial_downsample" (carla's complex-case native default — pass
    # None/omit to leave carla at its built-in defaults), or "mnl" (MNL
    # sampling, see Task 8 eval). When "mnl", BOTH strategies are set to "mnl"
    # in cs.setup(parameters=...). For any other value no parameters are passed
    # so carla uses its native defaults (byte-identical for the default "top_n").
    context.config("secondary_scorer_selection", "top_n")
    # MNL temperature: reserved for Task 8 evaluation; CarlaConfig has no
    # temperature field, so this key is registered but NOT wired into cs.setup.
    context.config("secondary_scorer_mnl_temperature", 1.0)
    if sec_enabled:
        # The assembled candidate set (gpkg sec_b_* + legacy other + external
        # centroids + residential visit rows) is built by a dedicated stage that
        # the facilities writer ALSO consumes, so a location the chainsolvers can
        # realise always exists as a MATSim facility (2026-07-11 LinkAssignment
        # fix: sec_b_* ids were realised but never written to facilities.xml).
        context.stage("braunschweig.synthesis.locations.secondary_candidates")

    # Smart `other` potential, external candidates and the cordon warning are
    # properties of the CANDIDATE SET and are declared/consumed by the
    # braunschweig.synthesis.locations.secondary_candidates stage above.

    # Daily / non-daily shopping subtype (Tier 2). When ON, each shop leg is
    # tagged with a daily/non-daily subtype that drives BOTH its desired
    # distance (the shop_daily / shop_non_daily distribution layer built by
    # braunschweig.popsim.distance_distributions when secondary_shop_daily_split
    # is set there) AND the building it is placed at (potential_retail_daily vs
    # potential_retail_non_daily instead of the summed pot_shop). The eqasim
    # output activity purpose stays "shop"; the subtype is internal to the
    # chainsolver. OFF (default false / flag absent) is byte-identical to the
    # pre-feature behaviour (single "shop" activity, summed pot_shop).
    context.config("secondary_shop_daily_split", False)
    # Optional pinned daily share (None -> derive the conditional
    # P(daily | mode, travel-time band) from the MiD Wege survey). A float in
    # [0, 1] forces a flat marginal daily probability instead of the
    # MiD-estimated conditional table (used only if one wants to pin the share).
    context.config("secondary_shop_daily_share", None)
    # Minimum observation count for a (mode, travel-time band) cell to receive
    # its own MiD-estimated daily probability; thinner cells fall back to the
    # MiD marginal share (logged, no silent fallback).
    context.config("secondary_distance_min_obs", 30)

    # Leisure / other errand+escort subtype splits (Task 4, issue #127). Mirror
    # secondary_shop_daily_split's structure: each ON flag tags legs of that
    # purpose with an internal MiD-estimated subtype that drives BOTH the
    # distance-distribution layer (braunschweig.popsim.distance_distributions'
    # secondary_leisure_subtype_split / secondary_other_subtype_split there) AND
    # the building placement (pot_leisure / pot_other candidates -- shared across
    # all subtypes of the same purpose for now, see _ACTIVITY_POTENTIAL_COLUMN).
    # The eqasim output purpose stays "leisure" / "other"; the subtype is
    # internal to the chainsolver. OFF (default) is byte-identical.
    context.config("secondary_leisure_subtype_split", False)
    context.config("secondary_other_subtype_split", False)

    # Escort as dedicated activity purpose (issue #201). The decider draws one
    # location TYPE per escort leg from the SrV-derived weights; defaults are
    # the committed derivation output (srv2023_escort_destination_types.csv).
    context.config("escort_purpose", False)
    context.config("escort_locations_activities", DEFAULT_ESCORT_LOCATIONS_ACTIVITIES)
    context.config("escort_locations_weights", DEFAULT_ESCORT_LOCATIONS_WEIGHTS)
    # Household escort link (issue #201 Phase 2): anchor a linked escorter's
    # escort activities at the youngest linkable child's education location
    # instead of drawing a location type. Requires escort_purpose ON.
    context.config("escort_household_link", False)
    context.config("escort_household_link_max_child_age_years", 17)

    # Escort distance-by-type (A3): scale the MiD escort distance layer per
    # drawn destination type with SrV-derived structure factors.
    context.config("escort_distance_by_type", False)
    context.config("escort_distance_factor_activities", DEFAULT_ESCORT_LOCATIONS_ACTIVITIES)
    context.config("escort_distance_factors", DEFAULT_ESCORT_DISTANCE_FACTORS)

    # MiD Wege directory: only consumed (and only declared) when at least one
    # subtype split is ON, so non-real configs that leave all three flags off
    # never require the local-only MiD delivery.
    shop_daily_split = context.config("secondary_shop_daily_split")
    leisure_subtype_split = context.config("secondary_leisure_subtype_split")
    other_subtype_split = context.config("secondary_other_subtype_split")
    if shop_daily_split or leisure_subtype_split or other_subtype_split:
        context.config("braunschweig.population.popsim.mid_dir")

    # Residential ``pot_visit`` placement for leisure_visit legs (Task 5,
    # issue #127). When ON, ``leisure_visit`` candidates are the ALKIS
    # residential building stock (``braunschweig.data.buildings`` -- the SAME
    # GFK-filtered, area-weighted frame ``synthesis/locations/home_cell.py``
    # consumes for home placement) instead of the generic pot_leisure
    # buildings; the other three leisure groups are unaffected. Requires
    # secondary_leisure_subtype_split AND secondary_building_potentials
    # (checked, fail-fast, in execute()). OFF (default) is byte-identical to
    # the Task-4 behaviour: leisure_visit maps to pot_leisure like the other
    # three leisure groups.
    # Flag still read in execute() (fail-fast guards + locations_df schema); the
    # residential candidate rows themselves are appended by the
    # secondary_candidates stage, which owns the braunschweig.data.buildings dep.
    context.config("leisure_visit_building_potential", False)


# ---------------------------------------------------------------------------
# Helpers (mirrors of the legacy stage)
# ---------------------------------------------------------------------------

def _prepare_primary(context):
    df_home = context.stage("synthesis.population.spatial.home.locations")
    df_work, df_education = context.stage(
        "synthesis.population.spatial.primary.locations"
    )
    crs = df_home.crs

    df_home = df_home.rename(columns={"geometry": "home"})
    df_work = df_work.rename(columns={"geometry": "work"})
    df_education = df_education.rename(columns={"geometry": "education"})

    df_locations = context.stage("synthesis.population.sampled")[
        ["person_id", "household_id"]
    ]
    df_locations = pd.merge(
        df_locations, df_home[["household_id", "home"]],
        how="left", on="household_id",
    )
    df_locations = pd.merge(
        df_locations, df_work[["person_id", "work"]],
        how="left", on="person_id",
    )
    df_locations = pd.merge(
        df_locations, df_education[["person_id", "education"]],
        how="left", on="person_id",
    )

    return (
        df_locations[["person_id", "home", "work", "education"]]
        .sort_values(by="person_id"),
        crs,
    )


def _resample_cdf(cdf, factor):
    if factor >= 0.0:
        cdf = cdf * (1.0 + factor * np.arange(1, len(cdf) + 1) / len(cdf))
    else:
        cdf = cdf * (
            1.0 + abs(factor) - abs(factor) * np.arange(1, len(cdf) + 1) / len(cdf)
        )
    cdf /= cdf[-1]
    return cdf


def _resample_distributions(distributions, factors):
    """Return a resampled deep copy of ``distributions``; never mutate the input.

    The ``distance_distributions`` stage object is synpp-cached and shared with
    the legacy locations stage. Resampling in place would compound the resample
    factors if the same cached object were resampled twice (double-resample
    contamination across consumers). We therefore deep-copy the nested dict and
    mutate only the copy; the returned object carries the resampled CDFs while
    the original cached object stays untouched. The deep copy is cheap (a
    handful of small distribution dicts per mode).

    Handles BOTH the legacy per-mode structure ``{mode: {bounds, distributions}}``
    and the purpose-layered structure ``{purpose: {mode: {bounds, distributions}}}``
    (Tier 1, built when ``secondary_shop_daily_split`` adds ``shop_daily`` /
    ``shop_non_daily`` distribution layers). The per-mode resample ``factors`` are
    applied within each mode regardless of the layer structure. Detection mirrors
    ``_sample_leg_distance``: a mode-level dict carries a ``"distributions"`` key;
    a purpose-level dict maps purpose -> mode-dict (no ``"distributions"`` key
    at the top level).
    """
    distributions = copy.deepcopy(distributions)
    # Guard against empty dict: next(iter(...)) raises StopIteration on empty.
    if not distributions:
        return distributions
    # Detect whether the top level is a purpose layer or a mode layer. A mode-level
    # dict always carries a "distributions" key; a purpose-level dict does not
    # (its values are mode dicts, each of which carries "distributions" one level
    # deeper). Modes and purposes are disjoint vocabularies, so an ambiguous top-
    # level key cannot occur.
    sample_value = next(iter(distributions.values()))
    is_purpose_layered = "distributions" not in sample_value
    if is_purpose_layered:
        for purpose, mode_dict in distributions.items():
            for mode, mode_distributions in mode_dict.items():
                for distribution in mode_distributions["distributions"]:
                    distribution["cdf"] = _resample_cdf(distribution["cdf"], factors[mode])
    else:
        for mode, mode_distributions in distributions.items():
            for distribution in mode_distributions["distributions"]:
                distribution["cdf"] = _resample_cdf(distribution["cdf"], factors[mode])
    return distributions


def _sample_leg_distance(distributions, mode, travel_time, purpose,
                         leisure_correction_factor, random):
    """Replicates ``CustomDistanceSampler.sample_distances`` for one leg.

    Auto-detects whether ``distributions`` is the legacy per-mode structure
    ``{mode: ...}`` or a purpose-layered one ``{purpose: {mode: ...}}``.
    Purposes (shop/leisure/other/work/education) and modes (car/walk/pt/
    bicycle/car_passenger) are disjoint vocabularies, so a top-level key equal
    to ``mode`` means the legacy per-mode structure; otherwise a purpose layer
    is expected and ``distributions[purpose]`` is selected. If ``purpose`` is
    absent from the purpose-layered dict the resulting KeyError surfaces
    immediately (no silent fallback -- a wiring bug should not be hidden).
    """
    # Auto-detect structure by checking whether the mode key is present at the
    # top level. Since purposes and modes are disjoint vocabularies, this is
    # unambiguous: a top-level "car"/"walk"/... key means legacy; a top-level
    # "shop"/"leisure"/... key means purpose-layered.
    legacy_mode_keyed = mode in distributions
    if legacy_mode_keyed:
        mode_distributions = distributions
    else:
        mode_distributions = distributions[purpose]
    mode_distribution = mode_distributions[mode]
    bound_index = int(np.count_nonzero(travel_time > mode_distribution["bounds"]))
    mode_distribution = mode_distribution["distributions"][bound_index]
    distance = mode_distribution["values"][
        int(np.count_nonzero(random.random_sample() > mode_distribution["cdf"]))
    ]
    # The leisure-correction factor is a LEGACY mode-only heuristic: on the
    # per-mode distribution leisure trips are diluted by the shorter shop/other
    # legs sharing the same mode, so leisure distances were scaled up to
    # compensate. With the Tier-1 purpose-resolved distributions
    # (secondary_distance_by_purpose: true) the leisure distance is sourced
    # DIRECTLY from the per-purpose MiD CDF, so applying the factor on top
    # double-counts and inflates the leisure far-tail (~2x). Apply it ONLY on the
    # legacy per-mode structure; on the purpose-layered structure it is a no-op.
    if purpose == "leisure" and legacy_mode_keyed:
        distance *= leisure_correction_factor
    return float(distance)


def _rda_sample_distances(distributions, problem, leisure_correction_factor, random):
    """Per-leg desired distances for the rda fallback's distance sampler.

    The rda fallback (``_rda_fallback_place``) receives the same distribution
    object as the carla path. With the Tier-1 purpose-resolved feature ON that
    object is ``{purpose: {mode: ...}}``, but eqasim's stock
    ``CustomDistanceSampler.sample_distances`` indexes it by ``mode`` and raises
    ``KeyError: '<mode>'`` -- which is why the fallback placed nothing for the
    long-distance / unbounded chains it is meant to catch. Reuse the
    purpose-aware ``_sample_leg_distance`` (which auto-detects the layout) so the
    fallback samples distances exactly like the carla path. The legacy
    ``{mode: ...}`` layout stays byte-identical (auto-detected).

    Mirrors ``CustomDistanceSampler.sample_distances`` EXACTLY: a
    length-``len(modes)`` array is zero-initialised and filled by ``zip`` over
    (modes, travel_times, purposes). When the chain has more legs than secondary
    purposes (the trailing leg returns to a primary anchor), ``zip`` truncates to
    the purposes length and those trailing legs keep distance 0 -- the relaxation
    solver requires one distance per leg, so the returned length MUST equal
    ``len(modes)``.
    """
    distances = np.zeros((len(problem["modes"]),))
    for index, (mode, travel_time, purpose) in enumerate(zip(
            problem["modes"], problem["travel_times"], problem["purposes"])):
        distances[index] = _sample_leg_distance(
            distributions, mode, travel_time, purpose,
            leisure_correction_factor, random,
        )
    return distances


def _purpose_in_distributions(distributions: Dict[str, Any], purpose: str) -> bool:
    """True iff ``distributions`` is purpose-layered AND carries ``purpose``.

    A purpose-layered structure is ``{purpose: {mode: ...}}``; the legacy
    per-mode structure is ``{mode: ...}``. Modes and purposes are disjoint
    vocabularies, so a top-level key equal to a known mode (e.g. ``"car"``)
    means the legacy structure, in which no purpose sub-keying exists (returns
    False). Used by the Tier-2 shop subtype routing to decide whether a
    ``shop_daily`` / ``shop_non_daily`` distance layer exists or the aggregate
    ``"shop"`` layer must be used as a logged fallback.
    """
    _MODE_KEYS = {"car", "car_passenger", "pt", "bicycle", "walk"}
    if not distributions:
        return False
    # Legacy per-mode structure: a top-level mode key is present.
    if any(k in distributions for k in _MODE_KEYS):
        return False
    return purpose in distributions


def _synthesize_escort_type_layers(distributions, factor_by_activity):
    """Add per-destination-type escort distance layers (A3, issue #201 follow-up).

    For every entry of ``factor_by_activity`` (activity name -> SrV structure
    factor) a deep copy of the aggregate ``escort`` layer is added under the
    activity name with every distance ``values`` array multiplied by the factor
    (exact multiplicative semantics: P(D_type <= x) = P(D <= x/factor)). Neutral
    factors (1.0) get an identical copy ON PURPOSE: the per-type fallback counter
    in the leg loop must stay a true failure signal, so a factor-neutral category
    must not read as a missing layer. The caller passes the PRIVATE deep copy
    returned by ``_resample_distributions``; this function mutates and returns it.
    Legacy mode-keyed structures (or a missing ``escort`` layer) are returned
    unchanged with a WARNING -- the leg loop's counted fallback then surfaces the
    rate (no silent fallback).
    """
    if not _purpose_in_distributions(distributions, "escort"):
        print(
            "[braunschweig.secondary_chainsolvers] WARNING: escort_distance_by_type "
            "is ON but the distributions carry no 'escort' purpose layer (legacy "
            "mode-keyed structure?); per-type layers NOT synthesized -- the leg "
            "loop will count every escort leg as distance-layer fallback."
        )
        return distributions
    base = distributions["escort"]
    for activity, factor in factor_by_activity.items():
        layer = copy.deepcopy(base)
        for mode_distribution in layer.values():
            for distribution in mode_distribution["distributions"]:
                distribution["values"] = distribution["values"] * float(factor)
        distributions[activity] = layer
    return distributions


# Internal shop subtype activities (chainsolver-only). They never leak into the
# eqasim output: _extract_locations maps them back to the "shop" purpose.
SHOP_SUBTYPE_ACTIVITIES = ("shop_daily", "shop_non_daily")

# Internal leisure subtype activities (chainsolver-only; Task 4, issue #127).
# Mirror the four purpose_subtype.LEISURE_GROUPS keys exactly (kept as literal
# strings here, not imported, matching how SHOP_SUBTYPE_ACTIVITIES mirrors
# shop_subtype's daily/non-daily vocabulary without importing it). They never
# leak into the eqasim output: _extract_locations maps them back to "leisure".
LEISURE_SUBTYPE_ACTIVITIES = (
    "leisure_local", "leisure_visit", "leisure_activity", "leisure_excursion",
)

# Internal "other" errand/escort subtype activities (chainsolver-only; Task 4,
# issue #127). Mirror the two purpose_subtype.OTHER_ERRAND_GROUPS keys plus the
# always-labelled escort outcome. "other_rest" is deliberately NOT included: it
# keeps the plain "other" activity rather than becoming its own chainsolver
# activity name (see _build_other_subtype_decider). They never leak into the
# eqasim output: _extract_locations maps them back to "other".
OTHER_SUBTYPE_ACTIVITIES = ("other_errand_short", "other_errand_long", "other_escort")

# Internal escort location-type activities (chainsolver-only; issue #201). One
# per drawable location category; the draw happens per escort leg in
# _build_plans_df via _build_escort_location_decider. They never leak into the
# eqasim output: _extract_locations maps them back to the "escort" purpose.
ESCORT_LOCATION_ACTIVITIES = (
    "escort_edu_kindergarten", "escort_edu_school", "escort_edu_university",
    "escort_leisure", "escort_other", "escort_residential", "escort_shop",
)

# Config category vocabulary -> internal activity name. Config uses the short
# category names (edu_kindergarten, ..., shop); the SrV-derived default weights
# below are the output of scripts/derive_escort_location_weights.py
# (srv2023_escort_destination_types.csv) -- regenerate there, never edit here.
ESCORT_CATEGORY_TO_ACTIVITY = {
    "edu_kindergarten": "escort_edu_kindergarten",
    "edu_school": "escort_edu_school",
    "edu_university": "escort_edu_university",
    "leisure": "escort_leisure",
    "other": "escort_other",
    "residential": "escort_residential",
    "shop": "escort_shop",
}
DEFAULT_ESCORT_LOCATIONS_ACTIVITIES = [
    "edu_kindergarten", "edu_school", "edu_university",
    "other", "leisure", "residential", "shop",
]
DEFAULT_ESCORT_LOCATIONS_WEIGHTS = [0.433, 0.199, 0.004, 0.141, 0.113, 0.105, 0.005]
# SrV-derived escort distance factors per destination type (A3; issue #201
# follow-up). Values are the factor_applied column of
# srv2023_escort_distance_factors.csv (weighted-median ratio to the overall
# escort median; thin categories neutralized to 1.0) -- regenerate via
# scripts/derive_escort_location_weights.py, never edit here.
DEFAULT_ESCORT_DISTANCE_FACTORS = [0.618, 0.8339, 1.0, 1.7361, 1.3607, 1.8035, 1.0]


def rewrite_linked_escort_trips(df_trips: pd.DataFrame,
                                df_anchors: pd.DataFrame) -> pd.DataFrame:
    """Return a COPY of the trips frame where ANCHORED escort activities'
    plan-level ``escort`` purposes become the fixed ``escort_linked`` purpose
    (issue #201 Phase 2; per-activity since the multi-child fix).

    A trip's ``preceding_purpose`` reflects activity ``trip_index`` and its
    ``following_purpose`` activity ``trip_index + 1``; both sides of an
    anchored activity are rewritten so the problem splitter sees a consistent
    fixed boundary. Escort activities WITHOUT an anchor row (overflow beyond
    the household's linkable children) keep the plain ``escort`` purpose and
    stay on the SrV-weighted draw path. Only the chainsolver-local problem
    construction sees this frame; the persisted activities/plans keep the
    plain ``escort`` purpose. The MultiIndex/``isin`` masks are built only over
    the rows whose ``preceding_purpose`` / ``following_purpose`` is already
    ``escort`` (typically 5-8% of all trips), not the full trips frame, since
    building and probing a MultiIndex over every row is the dominant cost at
    scale for a candidate set this small."""
    out = df_trips.copy()
    anchored = pd.MultiIndex.from_frame(df_anchors[["person_id", "activity_index"]])

    candidate_preceding = (out["preceding_purpose"] == "escort").to_numpy()
    candidate_following = (out["following_purpose"] == "escort").to_numpy()

    mask_preceding = np.zeros(len(out), dtype=bool)
    if candidate_preceding.any():
        preceding_activity = pd.MultiIndex.from_arrays([
            out.loc[candidate_preceding, "person_id"],
            out.loc[candidate_preceding, "trip_index"],
        ])
        mask_preceding[candidate_preceding] = preceding_activity.isin(anchored)

    mask_following = np.zeros(len(out), dtype=bool)
    if candidate_following.any():
        following_activity = pd.MultiIndex.from_arrays([
            out.loc[candidate_following, "person_id"],
            out.loc[candidate_following, "trip_index"] + 1,
        ])
        mask_following[candidate_following] = following_activity.isin(anchored)

    out.loc[mask_preceding, "preceding_purpose"] = "escort_linked"
    out.loc[mask_following, "following_purpose"] = "escort_linked"
    return out

# Maps each secondary chainsolver activity to its attached candidate-potential
# column. The two shop subtypes (Tier 2: secondary_shop_daily_split) map to
# genuinely distinct split retail potentials; the aggregate "shop" maps to the
# summed pot_shop and is the only shop key on the OFF path. The leisure/other
# subtypes (Task 4) map to the SAME aggregate potential as their parent purpose
# -- there is no per-subtype building potential yet (a dedicated "pot_visit"
# column is deferred to a later task).
_ACTIVITY_POTENTIAL_COLUMN = {
    "shop": "pot_shop",
    "shop_daily": "pot_shop_daily",
    "shop_non_daily": "pot_shop_non_daily",
    "leisure": "pot_leisure",
    "other": "pot_other",
}
_ACTIVITY_POTENTIAL_COLUMN.update({name: "pot_leisure" for name in LEISURE_SUBTYPE_ACTIVITIES})
_ACTIVITY_POTENTIAL_COLUMN.update({name: "pot_other" for name in OTHER_SUBTYPE_ACTIVITIES})
# Escort location-type activities (issue #201). "escort_residential" reuses the
# literal "pot_visit" column name here (NOT the VISIT_POTENTIAL_COLUMN constant,
# which is defined further below in this module) so this default/OFF-path
# mapping does not depend on a forward reference.
_ACTIVITY_POTENTIAL_COLUMN.update({
    "escort_edu_kindergarten": "pot_escort_edu",
    "escort_edu_school": "pot_escort_edu",
    "escort_edu_university": "pot_escort_edu",
    "escort_leisure": "pot_leisure",
    "escort_other": "pot_other",
    "escort_residential": "pot_visit",
    "escort_shop": "pot_shop",
})

# Offer / potential columns used for the "leisure_visit" activity ONLY when
# ``leisure_visit_building_potential`` is ON (Task 5, issue #127). Residential
# candidates appended by ``append_residential_visit_candidates`` carry
# ``offers_visit`` / ``pot_visit`` instead of the generic ``offers_leisure`` /
# ``pot_leisure`` shared by the other three leisure groups, so a residential
# building is a candidate for "leisure_visit" and NOTHING else. This dict
# stays fixed (does not update ``_ACTIVITY_POTENTIAL_COLUMN``, which is the
# OFF-path / default mapping tested by
# ``test_activity_potential_column_covers_all_subtype_activities``); the
# override is applied locally inside ``_build_locations_df``.
VISIT_OFFER_COLUMN = "offers_visit"
VISIT_POTENTIAL_COLUMN = "pot_visit"

# Warn if appending residential visit candidates multiplies the locations-
# frame row count by more than this factor (CLAUDE.md "Fallback
# transparency" / growth-guard requirement: a large, unforeseen growth of the
# carla candidate universe is a runtime-cost and correctness risk that must
# be surfaced, not hidden).
VISIT_CANDIDATE_WARN_FACTOR = 3.0


def append_residential_visit_candidates(candidates: gpd.GeoDataFrame,
                                        df_residential_buildings: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Append one residential-building candidate row per building for the
    "leisure_visit" placement (Task 5, issue #127).

    "leisure_visit" legs (MiD W_ZWD 701, "visiting someone") are destined for a
    private household, not a leisure-activity building, so their candidate set
    is the residential building stock reused verbatim from the home-assignment
    path -- ``braunschweig.data.buildings`` (the SAME ALKIS/GFK-filtered,
    area-weighted frame ``synthesis/locations/home_cell.py`` consumes for home
    placement; no new data source). Each residential building becomes one
    candidate row carrying ``offers_visit=True`` / ``pot_visit=weight`` (the
    footprint-area dwelling-capacity proxy already used by the legacy
    area-weighted home sampler) and ``False`` / ``0.0`` for every other
    purpose's offer/potential column, so it is NEVER a candidate for
    shop / leisure (non-visit) / other.

    Parameters
    ----------
    candidates:
        The existing secondary-candidate GeoDataFrame (the return value of
        :func:`build_secondary_candidates`), missing the ``offers_visit`` /
        ``pot_visit`` columns (added here, defaulting to ``False`` / ``0.0``
        on the pre-existing rows).
    df_residential_buildings:
        ``braunschweig.data.buildings`` output: ``building_id``, ``weight``,
        ``commune_id`` (and, if present, ``iris_id``), ``geometry`` (point,
        same CRS as ``candidates`` or reprojectable to it).

    Returns
    -------
    geopandas.GeoDataFrame
        ``candidates`` with ``offers_visit`` / ``pot_visit`` columns added,
        concatenated with one residential-candidate row per building.

    Raises
    ------
    ValueError
        If ``df_residential_buildings`` is missing a required column
        (fail-fast; no silent fallback to an empty/degenerate residential
        candidate set -- see the ``leisure_visit_building_potential`` caller
        in ``execute()``).
    """
    required = ["building_id", "weight", "commune_id", "geometry"]
    missing = [c for c in required if c not in df_residential_buildings.columns]
    if missing:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] leisure_visit_building_potential "
            "residential candidate source (braunschweig.data.buildings) is missing "
            "column(s) %s; available: %s." % (missing, list(df_residential_buildings.columns))
        )

    n_before = len(candidates)

    base = candidates.copy()
    base[VISIT_OFFER_COLUMN] = False
    base[VISIT_POTENTIAL_COLUMN] = 0.0

    res = df_residential_buildings
    if res.crs is not None and candidates.crs is not None and res.crs != candidates.crs:
        res = res.to_crs(candidates.crs)
    iris_col = "iris_id" if "iris_id" in res.columns else "commune_id"
    residential_rows = gpd.GeoDataFrame({
        "location_id": ("sec_res_" + res["building_id"].astype(str)).values,
        "commune_id": res["commune_id"].astype(str).values,
        "iris_id": res[iris_col].astype(str).values,
        "offers_shop": False,
        "offers_leisure": False,
        "offers_other": False,
        VISIT_OFFER_COLUMN: True,
        "pot_shop": 0.0,
        "pot_shop_daily": 0.0,
        "pot_shop_non_daily": 0.0,
        "pot_leisure": 0.0,
        "pot_other": 0.0,
        VISIT_POTENTIAL_COLUMN: res["weight"].astype(float).values,
        "geometry": res.geometry.values,
    }, crs=candidates.crs)

    out = gpd.GeoDataFrame(
        pd.concat([base, residential_rows], ignore_index=True), crs=candidates.crs)
    n_after = len(out)
    n_residential = len(residential_rows)
    growth_factor = (n_after / n_before) if n_before else float("inf")
    print(
        "[braunschweig.secondary_chainsolvers] leisure_visit_building_potential: "
        "locations frame %d -> %d rows after appending %d residential visit "
        "candidates (growth x%.2f)"
        % (n_before, n_after, n_residential, growth_factor)
    )
    if growth_factor > VISIT_CANDIDATE_WARN_FACTOR:
        print(
            "WARNING: [braunschweig.secondary_chainsolvers] residential visit-candidate "
            "growth factor x%.2f exceeds VISIT_CANDIDATE_WARN_FACTOR=%.1f -- this "
            "materially increases the carla candidate universe and solve cost; "
            "verify braunschweig.data.buildings is scoped to the expected region."
            % (growth_factor, VISIT_CANDIDATE_WARN_FACTOR)
        )
    return out


# Offer / potential columns for the escort education candidates (issue #201).
ESCORT_EDU_OFFER_BY_TYPE = {
    "kindergarten": "offers_escort_edu_kindergarten",
    "school": "offers_escort_edu_school",
    "university": "offers_escort_edu_university",
}
ESCORT_EDU_POTENTIAL_COLUMN = "pot_escort_edu"
ESCORT_RESIDENTIAL_OFFER_COLUMN = "offers_escort_residential"


def append_escort_candidates(candidates: gpd.GeoDataFrame,
                             df_education: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Append education escort candidates + escort offer columns (issue #201).

    Adds, on EVERY row: the three education offer columns
    (ESCORT_EDU_OFFER_BY_TYPE, default False), ESCORT_EDU_POTENTIAL_COLUMN
    (default 0.0) and ESCORT_RESIDENTIAL_OFFER_COLUMN (True where the row is a
    residential visit candidate, i.e. its VISIT_OFFER_COLUMN is True; False
    elsewhere / when the visit machinery is off). Then appends one
    ``sec_edu_<n>`` candidate row per NON-fake education facility from
    ``synthesis.locations.education`` (fake rows are municipality-centroid
    placeholders, not real facilities), carrying ONLY its per-type escort offer
    and ``pot_escort_edu = weight`` (the OSM area*floors capacity proxy the
    education gravity assignment uses -- ASSUMPTION documented in the spec).
    """
    required = ["fake", "education_type", "weight", "location_id", "commune_id", "geometry"]
    missing = [c for c in required if c not in df_education.columns]
    if missing:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] escort education candidate source "
            "(synthesis.locations.education) is missing column(s) %s; available: %s."
            % (missing, list(df_education.columns))
        )

    base = candidates.copy()
    for column in ESCORT_EDU_OFFER_BY_TYPE.values():
        base[column] = False
    base[ESCORT_EDU_POTENTIAL_COLUMN] = 0.0
    if VISIT_OFFER_COLUMN in base.columns:
        base[ESCORT_RESIDENTIAL_OFFER_COLUMN] = base[VISIT_OFFER_COLUMN].astype(bool)
    else:
        base[ESCORT_RESIDENTIAL_OFFER_COLUMN] = False

    edu = df_education[~df_education["fake"].astype(bool)].copy()
    n_excluded_fake = int(df_education["fake"].astype(bool).sum())
    n_unknown = int((edu["education_type"].astype(str) == "unknown").sum())
    edu = edu[edu["education_type"].astype(str).isin(ESCORT_EDU_OFFER_BY_TYPE)]
    if edu.crs is not None and candidates.crs is not None and edu.crs != candidates.crs:
        edu = edu.to_crs(candidates.crs)

    iris_col = "iris_id" if "iris_id" in edu.columns else "commune_id"
    data = {
        "location_id": ("sec_" + edu["location_id"].astype(str)).values,
        "commune_id": edu["commune_id"].astype(str).values,
        "iris_id": edu[iris_col].astype(str).values,
        "offers_shop": False,
        "offers_leisure": False,
        "offers_other": False,
        "offers_escort": True,
        "pot_shop": 0.0,
        "pot_shop_daily": 0.0,
        "pot_shop_non_daily": 0.0,
        "pot_leisure": 0.0,
        "pot_other": 0.0,
        ESCORT_EDU_POTENTIAL_COLUMN: edu["weight"].astype(float).values,
        ESCORT_RESIDENTIAL_OFFER_COLUMN: False,
        "geometry": edu.geometry.values,
    }
    # Visit columns exist on base whenever the residential machinery ran; keep
    # the frames column-aligned.
    if VISIT_OFFER_COLUMN in base.columns:
        data[VISIT_OFFER_COLUMN] = False
        data[VISIT_POTENTIAL_COLUMN] = 0.0
    education_types = edu["education_type"].astype(str).values
    for education_type, column in ESCORT_EDU_OFFER_BY_TYPE.items():
        data[column] = (education_types == education_type)

    edu_rows = gpd.GeoDataFrame(data, crs=candidates.crs)
    out = gpd.GeoDataFrame(
        pd.concat([base, edu_rows], ignore_index=True), crs=candidates.crs)
    print(
        "[braunschweig.secondary_chainsolvers] escort candidates: appended "
        f"{len(edu_rows)} education rows "
        f"(kindergarten={int((education_types == 'kindergarten').sum())}, "
        f"school={int((education_types == 'school').sum())}, "
        f"university={int((education_types == 'university').sum())}); "
        f"excluded {n_excluded_fake} fake centroid rows and {n_unknown} "
        "unknown-type rows"
    )
    return out


# ---------------------------------------------------------------------------
# SrV-grounded location-category candidates (issue #262): per-category
# building offer/potential columns + ATKIS landuse grid-point candidates.
# ---------------------------------------------------------------------------

# Offer/potential columns for the SrV location categories (issue #262).
#
# PLAN AMENDMENT (issue #262, post-Task-4 review): the leisure_* categories
# genuinely MASK the pot_leisure aggregate a sec_b_* row already carries (see
# ``build_secondary_candidates`` for how pot_leisure is derived) -- that part
# is unchanged. The errand_* categories do NOT mask pot_other: every sec_b_*
# row carries pot_other=0.0 by construction (build_secondary_candidates keeps
# only buildings with retail>0 | leisure>0), and errand-class buildings
# (hospitals, authorities, service businesses, ...) are therefore excluded
# from the candidate set entirely. Masking pot_other would be a structural
# zero-supply bug, not a thin-data limitation. ``append_location_category_columns``
# now derives the two errand categories' potential directly from
# ``df_potentials`` (the ``derive_other_potential`` cap-and-floor formula,
# applied per category -- see that function for the shared numerics) and
# appends a NEW ``sec_b_<building_id>`` candidate row for every errand-class
# building missing from ``candidates``. The dict below is kept as the
# leisure/errand grouping key other callers (e.g.
# ``secondary_candidates.execute``) use to select the leisure subset; for the
# errand entries it no longer means "mask this column literally".
SRV_BUILDING_CATEGORY_BASE_POTENTIAL = {
    "leisure_culture": "pot_leisure",
    "leisure_gastronomy": "pot_leisure",
    "leisure_sports": "pot_leisure",
    "errand_authority_medical": "pot_other",
    "errand_service": "pot_other",
}


def append_location_category_columns(candidates: gpd.GeoDataFrame,
                                      df_potentials: gpd.GeoDataFrame,
                                      mapping: pd.DataFrame,
                                      *, min_volume_m3: float = 50.0,
                                      cap_percentile: float = 0.99) -> gpd.GeoDataFrame:
    """Add per-category offer/potential columns to the candidates frame (issue #262).

    For each of the five ``SRV_BUILDING_CATEGORY_BASE_POTENTIAL`` categories,
    adds ``offers_<category>`` (bool) and ``pot_<category>`` (float) to
    EVERY row of ``candidates``. The three ``leisure_*`` categories MASK the
    existing ``pot_leisure`` aggregate on ``sec_b_<building_id>`` rows already
    present in ``candidates``: for a row whose Bosserhof class maps to
    ``<category>`` in ``mapping``, ``pot_<category> = pot_leisure`` (a mask,
    not a new formula) and ``offers_<category> = pot_<category> > 0``.

    The two ``errand_*`` categories (``errand_authority_medical``,
    ``errand_service``) are DIFFERENT: masking ``pot_other`` would be
    structurally zero everywhere, because ``build_secondary_candidates`` sets
    ``pot_other=0.0`` on every ``sec_b_*`` row and excludes errand-class
    buildings (hospitals, authorities, services, ...) from the candidate set
    entirely (its keep-filter is ``retail > 0 | leisure > 0``). Their
    potential is instead computed directly from ``df_potentials`` with the
    same cap-and-floor formula as ``secondary_other_potential.derive_other_potential``,
    applied per category:

        cap_<category>  = nanquantile(potential_generic over buildings whose
                           class maps to <category>, cap_percentile)
        pot_<category>  = min(potential_generic, cap_<category>) where the
                           building's class maps to <category>, else 0.0
        pot_<category>  = 0.0 where volume_m3 < min_volume_m3

    A building with a positive computed potential is guaranteed a
    ``sec_b_<building_id>`` candidate row: if one already exists (e.g. the
    building also has retail/leisure potential) its errand columns are
    updated in place; otherwise a NEW row is appended, carrying ONLY that
    errand category's offer/potential (every other offer/potential column --
    ``offers_shop``, ``offers_leisure``, ``offers_other``, ``offers_escort``,
    the other four SrV categories, etc. -- is ``False`` / ``0.0``).

    Every row that is neither a matching leisure building nor a matching
    errand building -- non-building candidates (external centroids,
    ``sec_res_*``, ``sec_edu_*``, legacy ``sec_*`` catalog rows) and building
    rows whose class is unmapped in ``mapping`` -- gets ``False`` / ``0.0``
    for all five columns. An unmapped class is a VALID outcome (not every
    Bosserhof class maps to one of the five categories), not an error.

    Parameters
    ----------
    candidates:
        The existing secondary-candidate GeoDataFrame; must already carry
        ``pot_leisure`` (added by ``build_secondary_candidates``).
    df_potentials:
        ``braunschweig.data.building_potentials`` frame: ``building_id``,
        ``bosserhof_class_clean``, ``potential_generic``, ``volume_m3``,
        ``commune_id``, ``geometry`` (footprint polygon or point).
    mapping:
        ``braunschweig.data.bosserhof_location_category`` frame:
        ``bosserhof_class``, ``location_category`` (one of
        ``bosserhof_location_category.BUILDING_CATEGORIES``).
    min_volume_m3:
        Errand potential is zeroed for buildings with ``volume_m3`` below
        this threshold (mirrors ``secondary_other_min_volume_m3``; the
        ``secondary_candidates`` stage passes the configured value).
    cap_percentile:
        Quantile of ``potential_generic`` (over each errand category's own
        class-member buildings) used as that category's potential cap
        (mirrors ``secondary_other_cap_percentile``).

    Returns
    -------
    geopandas.GeoDataFrame
        ``candidates`` with the ten new columns (five offers_/pot_ pairs),
        plus any newly appended errand-only ``sec_b_<building_id>`` rows.

    Raises
    ------
    ValueError
        If ``candidates`` is missing ``pot_leisure``, or
        ``df_potentials``/``mapping`` is missing a required column
        (fail-fast; no silent fallback to an all-zero category set).
    """
    if "pot_leisure" not in candidates.columns:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] append_location_category_columns "
            "requires candidates to already carry column 'pot_leisure' (produced by "
            "build_secondary_candidates); available: %s." % list(candidates.columns)
        )
    missing_potentials = [c for c in ["building_id", "bosserhof_class_clean", "potential_generic",
                                      "volume_m3", "commune_id", "geometry"]
                          if c not in df_potentials.columns]
    if missing_potentials:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] append_location_category_columns "
            "building_potentials source is missing column(s) %s; available: %s."
            % (missing_potentials, list(df_potentials.columns))
        )
    missing_mapping = [c for c in ["bosserhof_class", "location_category"]
                       if c not in mapping.columns]
    if missing_mapping:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] append_location_category_columns "
            "category mapping is missing column(s) %s; available: %s."
            % (missing_mapping, list(mapping.columns))
        )

    out = candidates.copy()
    categories = list(SRV_BUILDING_CATEGORY_BASE_POTENTIAL)
    for category in categories:
        out["offers_" + category] = False
        out["pot_" + category] = 0.0

    category_by_class = dict(zip(
        mapping["bosserhof_class"].astype(str), mapping["location_category"].astype(str),
    ))
    leisure_categories = [c for c, base in SRV_BUILDING_CATEGORY_BASE_POTENTIAL.items()
                          if base == "pot_leisure"]
    errand_categories = [c for c, base in SRV_BUILDING_CATEGORY_BASE_POTENTIAL.items()
                         if base == "pot_other"]

    # --- leisure categories: UNCHANGED -- mask the existing pot_leisure
    # aggregate on sec_b_* rows already present in candidates. ---
    building_mask = out["location_id"].astype(str).str.startswith("sec_b_")
    n_building_rows = int(building_mask.sum())

    class_by_building = dict(zip(
        df_potentials["building_id"].astype(str),
        df_potentials["bosserhof_class_clean"].astype(str),
    ))
    building_ids = out.loc[building_mask, "location_id"].astype(str).str.slice(len("sec_b_"))
    classes = building_ids.map(class_by_building)
    row_categories = classes.map(category_by_class)

    n_class_matched = int(classes.notna().sum())
    n_category_mapped = int(row_categories.notna().sum())
    per_category_counts = {
        category: int((row_categories == category).sum()) for category in categories
    }

    for category in leisure_categories:
        matched_index = row_categories.index[row_categories == category]
        if len(matched_index):
            out.loc[matched_index, "pot_" + category] = out.loc[matched_index, "pot_leisure"].astype(float)
            out.loc[matched_index, "offers_" + category] = out.loc[matched_index, "pot_" + category] > 0.0

    print(
        "[braunschweig.secondary_chainsolvers] leisure location category columns: %d "
        "sec_b_* building candidates; class matched %d/%d (%.1f%%), mapped to a "
        "leisure_* category %d/%d (%.1f%%); per-category counts: %s"
        % (n_building_rows, n_class_matched, n_building_rows,
           100.0 * n_class_matched / n_building_rows if n_building_rows else 0.0,
           n_category_mapped, n_class_matched,
           100.0 * n_category_mapped / n_class_matched if n_class_matched else 0.0,
           per_category_counts)
    )
    n_unmatched_building = n_building_rows - n_class_matched
    if n_unmatched_building:
        print(
            "WARNING: [braunschweig.secondary_chainsolvers] %d/%d sec_b_* candidates "
            "have no matching building_id in the building_potentials source "
            "(df_potentials); they carry False/0.0 for the leisure_* SrV location "
            "categories -- verify braunschweig.data.building_potentials and the "
            "building candidate set share the same building_id space."
            % (n_unmatched_building, n_building_rows)
        )

    # --- errand categories: derived independently from df_potentials, using
    # the derive_other_potential cap-and-floor formula per category (plan
    # amendment, issue #262). ---
    building_out_index = dict(zip(building_ids.values, building_ids.index))

    generic = pd.to_numeric(df_potentials["potential_generic"], errors="coerce").astype(float).to_numpy()
    volume = pd.to_numeric(df_potentials["volume_m3"], errors="coerce").astype(float).to_numpy()
    potential_building_ids = df_potentials["building_id"].astype(str).to_numpy()
    potential_classes = df_potentials["bosserhof_class_clean"].astype(str).to_numpy()
    potential_commune = df_potentials["commune_id"].astype(str).to_numpy()
    potential_geometry = df_potentials.geometry
    is_point_geometry = (potential_geometry.geom_type == "Point").to_numpy()
    potential_points = np.where(
        is_point_geometry, potential_geometry.values, potential_geometry.centroid.values)

    category_of_building = pd.Series(potential_classes).map(category_by_class)
    present_out_index = pd.Series(potential_building_ids).map(building_out_index)
    present_mask = present_out_index.notna().to_numpy()

    all_offer_columns = [c for c in out.columns if c.startswith("offers_")]
    all_potential_columns = [c for c in out.columns if c.startswith("pot_")]

    append_frames = []
    n_appended_total = 0
    per_category_supply = {}

    for category in errand_categories:
        member_mask = (category_of_building == category).to_numpy()
        n_members = int(member_mask.sum())
        if n_members:
            cap = float(np.nanquantile(generic[member_mask], cap_percentile))
        else:
            cap = float(np.nanquantile(generic, cap_percentile))
            print(
                "WARNING: [braunschweig.secondary_chainsolvers] no buildings map to "
                "location category '%s' in the Bosserhof mapping; potential cap "
                "derived from the all-building potential_generic quantile instead."
                % category
            )
        pot = np.where(member_mask, np.minimum(generic, cap), 0.0)
        pot = np.where(volume < float(min_volume_m3), 0.0, pot)
        offers = pot > 0.0
        per_category_supply[category] = int(offers.sum())

        update_mask = member_mask & present_mask
        if update_mask.any():
            target_index = present_out_index[update_mask].to_numpy()
            out.loc[target_index, "pot_" + category] = pot[update_mask]
            out.loc[target_index, "offers_" + category] = offers[update_mask]

        append_mask = member_mask & ~present_mask
        n_appended = int(append_mask.sum())
        n_appended_total += n_appended
        if n_appended:
            data = {
                "location_id": ["sec_b_" + b for b in potential_building_ids[append_mask]],
                "commune_id": potential_commune[append_mask],
                "iris_id": potential_commune[append_mask],
                "geometry": potential_points[append_mask],
            }
            for column in all_offer_columns:
                data[column] = np.zeros(n_appended, dtype=bool)
            for column in all_potential_columns:
                data[column] = np.zeros(n_appended, dtype=float)
            data["offers_" + category] = offers[append_mask]
            data["pot_" + category] = pot[append_mask]
            append_frames.append(gpd.GeoDataFrame(data, crs=out.crs))

    if append_frames:
        out = gpd.GeoDataFrame(
            pd.concat([out] + append_frames, ignore_index=True), crs=out.crs)

    print(
        "[braunschweig.secondary_chainsolvers] errand location category columns: "
        "%d new sec_b_* candidates appended for errand-class buildings absent from "
        "the candidate set; positive-potential rows per category: %s (min_volume_m3=%s, "
        "cap_percentile=%s)"
        % (n_appended_total, per_category_supply, min_volume_m3, cap_percentile)
    )
    return out


def check_category_supply(candidates: gpd.GeoDataFrame, categories) -> None:
    """Raise if any category in ``categories`` has zero positive-potential rows.

    A region-wide zero supply for a location category means the candidate
    universe carries no ``pot_<category> > 0`` row anywhere, so the carla
    solver could never select that category regardless of demand -- this is
    a wiring failure (a broken mapping, a grid-seeding gap, a potential-join
    miss), not merely thin data, and must be surfaced loudly (CLAUDE.md
    "Fallback transparency").

    Parameters
    ----------
    candidates:
        The assembled candidate GeoDataFrame; expected to carry
        ``pot_<category>`` for every entry in ``categories``.
    categories:
        Iterable of category names to check.

    Raises
    ------
    RuntimeError
        Naming every category with zero positive-potential rows (a missing
        ``pot_<category>`` column counts as zero supply).
    """
    empty = []
    for category in categories:
        column = "pot_" + category
        if column not in candidates.columns or not (candidates[column].astype(float) > 0.0).any():
            empty.append(category)
    if empty:
        raise RuntimeError(
            "[braunschweig.secondary_chainsolvers] zero candidate supply for location "
            "categor%s %s -- every pot_<category> column has no positive-potential "
            "rows; this indicates broken wiring (mapping / grid seeding / potential "
            "join), not thin data."
            % ("y" if len(empty) == 1 else "ies", empty)
        )


def append_landuse_candidates(candidates: gpd.GeoDataFrame,
                              df_landuse_points: gpd.GeoDataFrame,
                              layer_to_category: Dict[str, str],
                              df_municipalities: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Append one landuse grid-point candidate row per seeded point (issue #262).

    ``df_landuse_points`` is the output of
    ``braunschweig.synthesis.locations.landuse_candidates.grid_seed_polygons``
    (columns ``layer``, ``represented_area_m2``, ``geometry`` (Point)): each
    point becomes a ``sec_lu_<n>`` candidate row (``n`` = its positional
    index in ``df_landuse_points``, stable because grid seeding is
    deterministic) carrying ``offers_<category>=True`` /
    ``pot_<category>=represented_area_m2`` for its layer's category
    (``layer_to_category[layer]``) and ``False`` / ``0.0`` for every other
    offer/potential column already on ``candidates``, mirroring the
    column-fill pattern of :func:`append_residential_visit_candidates`. Any
    category column named by ``layer_to_category`` that does not yet exist
    on ``candidates`` (e.g. ``leisure_outdoor``, which has no building
    counterpart) is added here, defaulting to ``False`` / ``0.0`` on the
    pre-existing rows.

    ``commune_id`` / ``iris_id`` are attached by a point-in-polygon spatial
    join against ``df_municipalities`` (predicate ``"within"``). Points that
    fall outside every municipality polygon are outside the study area and
    are DROPPED (counted and logged -- no silent fallback to an unset zone
    id). ``iris_id`` is set equal to ``commune_id`` because
    ``data.spatial.municipalities`` does not carry a separate IRIS code
    (mirroring the ``iris_col`` fallback already used by
    ``append_residential_visit_candidates`` / ``append_escort_candidates``
    when the finer-grained id is unavailable).

    Parameters
    ----------
    candidates:
        The existing secondary-candidate GeoDataFrame. Should already carry
        the five SrV building-category columns (i.e. called AFTER
        :func:`append_location_category_columns`) so those columns are
        correctly zero-filled for the new landuse rows rather than added
        fresh here.
    df_landuse_points:
        ``grid_seed_polygons`` output: ``layer``, ``represented_area_m2``,
        ``geometry`` (Point).
    layer_to_category:
        ATKIS layer name -> SrV location category, e.g.
        ``landuse_candidates.LANDUSE_LAYER_TO_CATEGORY``.
    df_municipalities:
        ``data.spatial.municipalities`` frame: ``commune_id``, ``geometry``
        (polygon), same CRS as ``candidates`` or reprojectable to it.

    Returns
    -------
    geopandas.GeoDataFrame
        ``candidates`` concatenated with one landuse-candidate row per point
        that falls inside a municipality.

    Raises
    ------
    ValueError
        If ``df_landuse_points`` is missing a required column, if
        ``df_municipalities`` is missing ``commune_id``, or if
        ``df_landuse_points`` carries a ``layer`` value with no entry in
        ``layer_to_category`` (fail-fast; no silent drop of an unrecognised
        layer).
    """
    required_points = ["layer", "represented_area_m2", "geometry"]
    missing_points = [c for c in required_points if c not in df_landuse_points.columns]
    if missing_points:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] append_landuse_candidates landuse "
            "point source is missing column(s) %s; available: %s."
            % (missing_points, list(df_landuse_points.columns))
        )
    if "commune_id" not in df_municipalities.columns:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] append_landuse_candidates "
            "municipalities source is missing the 'commune_id' column; available: %s."
            % list(df_municipalities.columns)
        )
    unknown_layers = sorted(set(df_landuse_points["layer"].astype(str)) - set(layer_to_category))
    if unknown_layers:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] append_landuse_candidates: "
            "df_landuse_points has layer(s) %s with no entry in layer_to_category "
            "(known: %s)." % (unknown_layers, sorted(layer_to_category))
        )

    n_before = len(candidates)
    base = candidates.copy()

    categories = sorted(set(layer_to_category.values()))
    for category in categories:
        if ("offers_" + category) not in base.columns:
            base["offers_" + category] = False
        if ("pot_" + category) not in base.columns:
            base["pot_" + category] = 0.0

    pts = df_landuse_points.copy()
    if pts.crs is not None and candidates.crs is not None and pts.crs != candidates.crs:
        pts = pts.to_crs(candidates.crs)
    municipalities = df_municipalities
    if (municipalities.crs is not None and candidates.crs is not None
            and municipalities.crs != candidates.crs):
        municipalities = municipalities.to_crs(candidates.crs)

    n_total = len(pts)
    pts_indexed = gpd.GeoDataFrame(
        {"_row": np.arange(n_total)}, geometry=pts.geometry.values, crs=candidates.crs)
    joined = gpd.sjoin(
        pts_indexed, municipalities[["commune_id", "geometry"]],
        how="left", predicate="within",
    ).drop(columns=["index_right"])
    # A point exactly on a shared municipality border can match more than one
    # polygon; keep the first match (deterministic row order) so every input
    # point contributes at most one output row.
    joined = joined.drop_duplicates(subset="_row", keep="first").set_index("_row")
    commune_by_row = joined["commune_id"].reindex(range(n_total))

    kept_mask = commune_by_row.notna().to_numpy()
    n_kept = int(kept_mask.sum())
    n_dropped = n_total - n_kept

    kept_n = np.arange(n_total)[kept_mask]
    layer_kept = df_landuse_points["layer"].to_numpy()[kept_mask]
    area_kept = df_landuse_points["represented_area_m2"].astype(float).to_numpy()[kept_mask]
    geom_kept = pts.geometry.to_numpy()[kept_mask]
    commune_kept = commune_by_row.to_numpy()[kept_mask].astype(str)
    category_kept = np.array([layer_to_category[layer] for layer in layer_kept])

    offer_columns_all = [c for c in base.columns if c.startswith("offers_")]
    potential_columns_all = [c for c in base.columns if c.startswith("pot_")]

    data = {
        "location_id": ["sec_lu_%d" % n for n in kept_n],
        "commune_id": commune_kept,
        "iris_id": commune_kept,
        "geometry": geom_kept,
    }
    for column in offer_columns_all:
        data[column] = np.zeros(n_kept, dtype=bool)
    for column in potential_columns_all:
        data[column] = np.zeros(n_kept, dtype=float)
    for category in categories:
        mask = category_kept == category
        data["offers_" + category][mask] = True
        data["pot_" + category][mask] = area_kept[mask]

    landuse_rows = gpd.GeoDataFrame(data, crs=candidates.crs)
    out = gpd.GeoDataFrame(
        pd.concat([base, landuse_rows], ignore_index=True), crs=candidates.crs)

    n_after = len(out)
    growth_factor = (n_after / n_before) if n_before else float("inf")
    print(
        "[braunschweig.secondary_chainsolvers] landuse candidates: %d/%d grid points "
        "inside a municipality kept, %d dropped (outside the study area boundary); "
        "locations frame %d -> %d rows after appending %d landuse candidates "
        "(growth x%.2f)"
        % (n_kept, n_total, n_dropped, n_before, n_after, n_kept, growth_factor)
    )
    if growth_factor > VISIT_CANDIDATE_WARN_FACTOR:
        print(
            "WARNING: [braunschweig.secondary_chainsolvers] landuse candidate growth "
            "factor x%.2f exceeds VISIT_CANDIDATE_WARN_FACTOR=%.1f -- this materially "
            "increases the carla candidate universe and solve cost; verify "
            "secondary_landuse_grid_spacing_meters is not set too fine for the "
            "region's landuse extent."
            % (growth_factor, VISIT_CANDIDATE_WARN_FACTOR)
        )
    return out


def build_scorer(enabled: bool, mode: str, pot_weight: float, dist_dev_weight: float,
                 attr_transform: str = "linear"):
    """Construct the chainsolvers combined Scorer, or None when disabled (the
    legacy distance-only path). Import-lazy so the module loads without the dep.
    Raises if enabled but the Scorer is unavailable (no silent fallback).

    ``attr_transform`` controls how building potentials are scaled before scoring:
    ``"linear"`` (default, byte-identical to before), ``"log1p"`` (log(1+P),
    the calibrated-MNL form), or ``"log"``. Forwarded directly to
    ``chainsolvers.Scorer(attr_transform=...)``.
    """
    if not enabled:
        return None
    try:
        import chainsolvers as cs
        Scorer = getattr(cs, "Scorer", None)
        if Scorer is None:
            from chainsolvers.scoring_selection import Scorer
        return Scorer(mode=mode, pot_weight=pot_weight, dist_dev_weight=dist_dev_weight,
                      attr_transform=attr_transform)
    except Exception as exc:
        raise RuntimeError(
            "secondary_building_potentials is ON but the chainsolvers combined "
            "Scorer is unavailable (%s); pin the git commit in environment.yml" % exc
        )


def build_secondary_candidates(df_secondary_legacy: gpd.GeoDataFrame,
                               df_buildings: gpd.GeoDataFrame,
                               df_external: gpd.GeoDataFrame = None,
                               *, mapping=None,
                               other_potential_params=None) -> gpd.GeoDataFrame:
    """REPLACE secondary candidates when building potentials are ON.

    shop/leisure candidates = gpkg activity buildings (native potentials, no
    fallback — the candidate set IS the buildings that carry a non-zero
    retail or leisure potential); 'other' candidates = the legacy broad catalog
    with the generic potential attached by footprint join (fallback 0.0, logged
    by attach_potential so the rate stays observable).

    Parameters
    ----------
    df_secondary_legacy:
        The legacy secondary candidate GeoDataFrame from
        ``synthesis.locations.secondary`` (columns: location_id, commune_id,
        iris_id, geometry(Point), offers_shop, offers_leisure, offers_other).
    df_buildings:
        Building-footprint GeoDataFrame from
        ``braunschweig.data.building_potentials`` (columns: building_id,
        potential_retail_daily, potential_retail_non_daily, potential_leisure,
        potential_generic, commune_id, geometry(POLYGON), EPSG:25832).
        When ``mapping`` is provided, the buildings frame must additionally
        carry ``volume_m3`` and the Bosserhof class column (default
        ``bosserhof_class_clean``).
    df_external:
        Optional GeoDataFrame of external Gemeinde centroids (long-distance
        secondary candidates).
    mapping:
        Optional DataFrame ``[bosserhof_class, eqasim_purpose, other_destination]``
        from ``braunschweig.data.bosserhof_purpose``.  When provided (ON path)
        the ``other`` potential is derived via
        ``derive_other_potential`` (capped, whitelist-boosted) and the median of
        the positive values is used as the spatial-join fallback (logged).
        When ``None`` (default / OFF path) the raw ``potential_generic`` is used
        with a zero fallback — byte-identical to the pre-feature behaviour.
    other_potential_params:
        Optional dict with keyword arguments forwarded to
        ``derive_other_potential`` (``broad_share``, ``errand_share``,
        ``min_volume_m3``, ``cap_percentile``). Ignored when ``mapping`` is None.

    Returns
    -------
    GeoDataFrame with columns:
        location_id, commune_id, iris_id, geometry(Point),
        offers_shop, offers_leisure, offers_other, offers_escort,
        pot_shop, pot_shop_daily, pot_shop_non_daily, pot_leisure, pot_other
    concat of gpkg shop/leisure rows and legacy other rows, reset index.

    ``pot_shop`` stays the SUM of the daily + non-daily retail potential (used
    on the OFF / non-split path, byte-identical to before); ``pot_shop_daily``
    and ``pot_shop_non_daily`` carry the two gpkg components separately so the
    Tier-2 daily/non-daily split (secondary_shop_daily_split) can route a leg's
    placement to the matching retail subtype. The legacy 'other' rows carry 0.0
    for all three shop potentials.

    ``offers_escort`` (issue #201): True on every candidate row, regardless of
    the ``escort_purpose`` flag -- it is cheap to mark every candidate eligible
    here; whether facilities WRITE the escort option is gated by the
    ``escort_purpose`` flag in the facilities writer (Task 8), not by this
    column.
    """
    from braunschweig.data.building_potential_attach import attach_potential

    # --- gpkg shop/leisure candidates ---
    # One row per building that carries a non-zero retail or leisure potential.
    # Potentials are native (read directly from the building table); no spatial
    # join needed, so there is no fallback path for this half of the candidates.
    b = df_buildings.copy()
    retail_daily = b["potential_retail_daily"].astype(float)
    retail_non_daily = b["potential_retail_non_daily"].astype(float)
    retail = retail_daily + retail_non_daily
    leisure = b["potential_leisure"].astype(float)
    keep = (retail > 0) | (leisure > 0)
    b = b[keep]
    retail = retail[keep]
    retail_daily = retail_daily[keep]
    retail_non_daily = retail_non_daily[keep]
    leisure = leisure[keep]
    gpkg = gpd.GeoDataFrame({
        "location_id": ("sec_b_" + b["building_id"].astype(str)).values,
        "commune_id": b["commune_id"].astype(str).values,
        "iris_id": b["commune_id"].astype(str).values,
        "offers_shop": (retail > 0).values,
        "offers_leisure": (leisure > 0).values,
        "offers_other": False,
        "offers_escort": True,
        "pot_shop": retail.values,
        "pot_shop_daily": retail_daily.values,
        "pot_shop_non_daily": retail_non_daily.values,
        "pot_leisure": leisure.values,
        "pot_other": 0.0,
        "geometry": b.geometry.centroid.values,
    }, crs=df_buildings.crs)

    # --- legacy 'other' candidates (broad catalog) ---
    # All legacy candidates become 'other'-only rows so the broad OSM/ALKIS/
    # landuse catalog is preserved for the 'other' purpose.
    legacy = df_secondary_legacy.copy()
    if mapping is not None:
        # ON path: derive a capped, whitelist-boosted potential_other via the
        # Bosserhof function-class mapping. The footprint-join fallback is the
        # median of the positive potential_other values (so candidates without a
        # containing building still receive a reasonable non-zero potential rather
        # than the 0.0 that the generic fallback would give). The rate is logged
        # by attach_potential (no silent fallback).
        from braunschweig.synthesis.locations.secondary_other_potential import (
            derive_other_potential,
        )
        params = other_potential_params or {}
        bld = df_buildings.copy()
        pot_series, st = derive_other_potential(bld, mapping, **params)
        bld["potential_other"] = pot_series.values
        positive = pot_series[pot_series > 0.0]
        median_prior = float(positive.median()) if len(positive) else 0.0
        print("[braunschweig.secondary_chainsolvers] smart other potential: "
              "cap=%.0f whitelist=%d non-whitelist=%d unknown_class=%d tiny=%d "
              "median_prior=%.1f" % (st["cap_value"], st["n_whitelist"],
              st["n_nonwhitelist"], st["n_unknown_class"], st["n_tiny"], median_prior))
        pot_other, _p, _f = attach_potential(
            legacy, bld, "potential_other",
            fallback=np.full(len(legacy), median_prior, dtype=float), label="sec_other")
    else:
        # OFF path: byte-identical to the pre-feature behaviour (raw
        # potential_generic, zero fallback). No new imports, no new logic.
        pot_other, _p, _f = attach_potential(
            legacy, df_buildings, "potential_generic",
            fallback=np.zeros(len(legacy), dtype=float), label="sec_other")
    legacy_other = gpd.GeoDataFrame({
        "location_id": legacy["location_id"].astype(str).values,
        "commune_id": legacy["commune_id"].astype(str).values,
        "iris_id": legacy["iris_id"].astype(str).values,
        "offers_shop": False,
        "offers_leisure": False,
        "offers_other": True,
        "offers_escort": True,
        "pot_shop": 0.0,
        "pot_shop_daily": 0.0,
        "pot_shop_non_daily": 0.0,
        "pot_leisure": 0.0,
        "pot_other": pot_other,
        "geometry": legacy.geometry.values,
    }, crs=legacy.crs)

    # External Gemeinde centroids (outside ZGB): long-distance secondary candidates
    # so carla can match desired distances beyond the study area instead of
    # truncating to the area edge. offers all three purposes; potential =
    # population (ewz) -- a population proxy; external selection is distance-driven
    # (carla snaps the relaxed point to the nearest external centroid), so the exact
    # potential only ranks among near-equal-distance centroids.
    frames = [gpkg, legacy_other]
    if df_external is not None and len(df_external) > 0:
        ext = (df_external.to_crs(df_buildings.crs)
               if df_external.crs != df_buildings.crs else df_external)
        ewz = ext["ewz"].astype(float).values
        cid = ext["commune_id"].astype(str).values
        ext_rows = gpd.GeoDataFrame({
            "location_id": cid,
            "commune_id": cid,
            "iris_id": cid,
            "offers_shop": True,
            "offers_leisure": True,
            "offers_other": True,
            "offers_escort": True,
            "pot_shop": ewz,
            "pot_shop_daily": ewz,
            "pot_shop_non_daily": ewz,
            "pot_leisure": ewz,
            "pot_other": ewz,
            "geometry": ext.geometry.values,
        }, crs=df_buildings.crs)
        frames.append(ext_rows)
        print("[braunschweig.secondary_chainsolvers] external candidates: "
              "%d Gemeinde centroids appended for long-distance secondary trips"
              % len(ext_rows))

    out = gpd.GeoDataFrame(
        pd.concat(frames, ignore_index=True), crs=df_buildings.crs)
    print("[braunschweig.secondary_chainsolvers] REPLACE candidates: "
          "%d gpkg shop/leisure buildings + %d legacy 'other' candidates"
          % (len(gpkg), len(legacy_other)))
    return out


def _build_locations_df(df_secondary, with_potentials: bool = False,
                        shop_daily_split: bool = False,
                        leisure_subtype_split: bool = False,
                        other_subtype_split: bool = False,
                        leisure_visit_building_potential: bool = False,
                        escort_purpose: bool = False):
    """Convert eqasim secondary candidates -> chainsolvers ``locations_df``.

    When ``with_potentials`` is True a ``potentials`` column is added: a
    semicolon-joined string aligned 1:1 with ``activities`` (the chainsolvers df
    parser reads per-activity potentials parallel to the activities list).

    When ``shop_daily_split`` is True (Tier 2: secondary_shop_daily_split) a
    building that offers shopping is emitted under the two internal subtype
    activities ``shop_daily`` / ``shop_non_daily`` (each carrying its own retail
    potential, ``pot_shop_daily`` / ``pot_shop_non_daily``) instead of a single
    ``shop`` activity, so the carla solver can place a daily shop leg at a
    daily-retail building and a non-daily leg at a non-daily-retail building. A
    subtype is only offered when its potential column is strictly positive, so a
    daily-only building is not a candidate for a non-daily leg and vice versa.
    ``shop_daily_split`` requires ``with_potentials`` (the split is meaningless
    without the per-subtype potentials). OFF (default) is byte-identical to the
    pre-feature behaviour (a single ``shop`` activity at ``pot_shop``).

    When ``leisure_subtype_split`` is True (Task 4, issue #127) a building that
    offers leisure is emitted under the four internal subtype activities
    (``LEISURE_SUBTYPE_ACTIVITIES``: leisure_local/visit/activity/excursion)
    INSTEAD OF the aggregate ``leisure`` activity. Unless
    ``leisure_visit_building_potential`` is also ON, there is no per-subtype
    building potential -- all four share the SAME ``pot_leisure`` value, so no
    offer is ever dropped for a non-positive potential here (that zero-skip
    only applies to the genuinely distinct ``SHOP_SUBTYPE_ACTIVITIES``
    potentials). ``leisure_subtype_split`` requires ``with_potentials``. OFF
    (default) is byte-identical.

    When ``leisure_visit_building_potential`` is also True (Task 5, issue #127)
    the ``leisure_visit`` subtype is REROUTED onto the dedicated residential
    candidate set: its offer column becomes ``VISIT_OFFER_COLUMN``
    ("offers_visit") instead of "offers_leisure", and its potential column
    becomes ``VISIT_POTENTIAL_COLUMN`` ("pot_visit") instead of "pot_leisure",
    so it only targets residential buildings appended by
    ``append_residential_visit_candidates`` (a "leisure_visit" offer with a
    non-positive ``pot_visit`` is dropped, mirroring the shop-subtype
    zero-skip). The other three leisure groups are unaffected (still
    "offers_leisure" / "pot_leisure"). Requires ``leisure_subtype_split`` and
    ``with_potentials``; fails fast if ``pot_visit`` is absent from
    ``df_secondary`` (no silent fallback to ``pot_leisure``).

    When ``other_subtype_split`` is True (Task 4, issue #127) a building that
    offers "other" is emitted under the three internal errand/escort subtype
    activities (``OTHER_SUBTYPE_ACTIVITIES``: other_errand_short/long,
    other_escort) IN ADDITION TO the aggregate ``other`` activity -- kept so
    ``other_rest`` legs (which the decider deliberately never subtypes, see
    ``_build_other_subtype_decider``) still find a candidate. All three subtypes
    share the SAME ``pot_other`` value. ``other_subtype_split`` requires
    ``with_potentials``. OFF (default) is byte-identical.

    When ``escort_purpose`` is True (issue #201) the seven internal
    ``ESCORT_LOCATION_ACTIVITIES`` are emitted IN ADDITION TO the aggregate/
    subtype activities above, so the same building can be a candidate for both
    its normal purpose and the matching escort drop-off/pick-up. Three
    (``escort_edu_kindergarten/school/university``) target the dedicated
    education candidates from :func:`append_escort_candidates`
    (``pot_escort_edu``); ``escort_leisure`` / ``escort_other`` / ``escort_shop``
    reuse the plain aggregate offer/potential of their base purpose;
    ``escort_residential`` reuses the residential visit candidates
    (``ESCORT_RESIDENTIAL_OFFER_COLUMN`` / ``pot_visit``) and is dropped for a
    non-positive potential, mirroring the shop-subtype zero-skip.
    ``escort_purpose`` requires ``with_potentials`` (the escort placement needs
    the education/visit/aggregate potential columns).
    """
    if shop_daily_split and not with_potentials:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] shop_daily_split requires "
            "with_potentials (the daily/non-daily split needs the per-subtype "
            "retail potential columns)."
        )
    if leisure_subtype_split and not with_potentials:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] leisure_subtype_split requires "
            "with_potentials (the leisure subtype placement needs the pot_leisure "
            "potential column)."
        )
    if other_subtype_split and not with_potentials:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] other_subtype_split requires "
            "with_potentials (the other subtype placement needs the pot_other "
            "potential column)."
        )
    if leisure_visit_building_potential and not leisure_subtype_split:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] leisure_visit_building_potential "
            "requires leisure_subtype_split (there is no 'leisure_visit' activity "
            "without the leisure subtype split)."
        )
    if leisure_visit_building_potential and VISIT_POTENTIAL_COLUMN not in df_secondary.columns:
        # Fail-fast (CLAUDE.md "Fallback transparency"): the flag promises a
        # dedicated residential potential; silently falling back to pot_leisure
        # here would hide a broken wiring (e.g. append_residential_visit_candidates
        # not called upstream) behind an apparently-working run.
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] leisure_visit_building_potential is ON "
            "but the locations frame has no '%s' column (residential visit candidates "
            "were not appended -- call append_residential_visit_candidates() on "
            "df_secondary before _build_locations_df, or disable "
            "leisure_visit_building_potential)." % VISIT_POTENTIAL_COLUMN
        )
    if escort_purpose and not with_potentials:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] escort_purpose requires "
            "with_potentials (the escort placement needs the education/visit/"
            "aggregate potential columns)."
        )
    activities = []
    potentials = []
    # Activity emission order. With a split ON, the aggregate offer is either
    # REPLACED (shop, leisure -- every leg of that purpose gets a subtype) or
    # EXTENDED (other -- other_rest legs still need the plain "other" offer);
    # a purpose whose split is OFF keeps its single aggregate offer.
    shop_offer_specs = (
        (("shop_daily", "offers_shop"), ("shop_non_daily", "offers_shop"))
        if shop_daily_split else (("shop", "offers_shop"),)
    )
    if leisure_subtype_split:
        leisure_offer_specs = tuple(
            (name, VISIT_OFFER_COLUMN if (leisure_visit_building_potential and name == "leisure_visit")
             else "offers_leisure")
            for name in LEISURE_SUBTYPE_ACTIVITIES
        )
    else:
        leisure_offer_specs = (("leisure", "offers_leisure"),)
    other_offer_specs = (
        tuple((name, "offers_other") for name in OTHER_SUBTYPE_ACTIVITIES) + (("other", "offers_other"),)
        if other_subtype_split else (("other", "offers_other"),)
    )
    escort_offer_specs = (
        (
            ("escort_edu_kindergarten", "offers_escort_edu_kindergarten"),
            ("escort_edu_school", "offers_escort_edu_school"),
            ("escort_edu_university", "offers_escort_edu_university"),
            ("escort_leisure", "offers_leisure"),
            ("escort_other", "offers_other"),
            ("escort_residential", ESCORT_RESIDENTIAL_OFFER_COLUMN),
            ("escort_shop", "offers_shop"),
        )
        if escort_purpose else ()
    )
    offer_specs = shop_offer_specs + leisure_offer_specs + other_offer_specs + escort_offer_specs
    # Per-activity potential column, overriding "leisure_visit" -> pot_visit
    # ONLY when leisure_visit_building_potential is ON (the OFF-path/default
    # mapping in _ACTIVITY_POTENTIAL_COLUMN stays leisure_visit -> pot_leisure,
    # see test_activity_potential_column_covers_all_subtype_activities).
    potential_column_by_activity = dict(_ACTIVITY_POTENTIAL_COLUMN)
    if leisure_visit_building_potential:
        potential_column_by_activity["leisure_visit"] = VISIT_POTENTIAL_COLUMN
    cols = ["offers_shop", "offers_leisure", "offers_other"]
    if escort_purpose:
        cols = cols + list(ESCORT_EDU_OFFER_BY_TYPE.values()) + [ESCORT_RESIDENTIAL_OFFER_COLUMN]
    if with_potentials:
        # Only require the potential columns actually consumed by the active
        # offer_specs, so a non-split path does not demand subtype potential
        # columns (byte-identical + no spurious KeyError on candidate frames
        # that carry only the aggregate potentials). Deduplicated (preserving
        # first-seen order) because the leisure/other subtypes intentionally
        # SHARE one potential column across several offer_specs entries --
        # selecting a duplicated column name from df_secondary would otherwise
        # yield a multi-column slice instead of a per-row scalar below.
        potential_cols = [potential_column_by_activity[act] for act, _ in offer_specs]
        # "escort_residential" always maps to pot_visit (VISIT_POTENTIAL_COLUMN,
        # see the fixed _ACTIVITY_POTENTIAL_COLUMN entry above), but pot_visit is
        # only appended once append_residential_visit_candidates has actually run
        # -- escort_purpose alone does not guarantee it (append_escort_candidates
        # sets ESCORT_RESIDENTIAL_OFFER_COLUMN False on every row when the visit
        # machinery is off, so "escort_residential" is never offered and pot_visit
        # is never read in that case). Filtered to columns that actually exist so
        # escort_purpose stays usable without the residential visit machinery; a
        # column genuinely missing while its offer is True would still raise
        # inside the per-row loop below (fail loud, not silently wrong).
        cols = cols + [c for c in dict.fromkeys(potential_cols) if c in df_secondary.columns]
    if leisure_visit_building_potential:
        cols = cols + [VISIT_OFFER_COLUMN]
    cols = list(dict.fromkeys(cols))
    for _, row in df_secondary[cols].iterrows():
        acts, pots = [], []
        for act, offer in offer_specs:
            if not bool(row[offer]):
                continue
            if with_potentials:
                pot = float(row[potential_column_by_activity[act]])
                # A shop subtype with a zero potential is not a candidate for
                # that subtype (the building has no daily / no non-daily retail
                # floor area); the same zero-skip applies to "leisure_visit"
                # once it is rerouted onto pot_visit (a row offering
                # "leisure_visit" with a non-positive residential potential is
                # not a real candidate). The aggregate shop/leisure/other
                # offers, and the remaining leisure/other subtypes (which
                # share one undifferentiated potential column), are kept
                # regardless of sign so the OFF path stays byte-identical.
                if shop_daily_split and act in SHOP_SUBTYPE_ACTIVITIES and pot <= 0.0:
                    continue
                if leisure_visit_building_potential and act == "leisure_visit" and pot <= 0.0:
                    continue
                if escort_purpose and act == "escort_residential" and pot <= 0.0:
                    continue
                acts.append(act)
                pots.append(pot)
            else:
                acts.append(act)
        activities.append("; ".join(acts))
        if with_potentials:
            potentials.append("; ".join(str(p) for p in pots))

    # Vectorised coordinate access (GeoSeries.x/.y) instead of a per-geometry
    # Python lambda; produces the identical (n, 2) ordering as the candidate
    # set, so the resulting locations table is byte-identical.
    coords = np.column_stack((
        df_secondary.geometry.x.values,
        df_secondary.geometry.y.values,
    ))
    data = {
        "id": df_secondary["location_id"].astype(str).values,
        "x": coords[:, 0],
        "y": coords[:, 1],
        "activities": activities,
    }
    if with_potentials:
        data["potentials"] = potentials
    return pd.DataFrame(data)


# ---------------------------------------------------------------------------
# Plans-DF construction
# ---------------------------------------------------------------------------

# Eqasim purposes that count as "secondary" (variable). ``home``/``work``/
# ``education`` are fixed (anchors). "escort" (issue #201) is only realised
# when escort_purpose is ON; membership here is inert while no escort legs
# exist, keeping the OFF path byte-identical.
SECONDARY_PURPOSES = {"shop", "leisure", "other", "escort"}
FIXED_PURPOSES = {"home", "work", "education"}


def _problem_legs(problem) -> List[Dict[str, Any]]:
    """Yield one leg dict per trip in the problem.

    Each leg dict carries ``to_act_type`` and ``leg_index`` (0-based
    within the problem). Anchor coordinates are filled in afterwards.
    """
    purposes = problem["purposes"]
    modes = problem["modes"]
    travel_times = problem["travel_times"]

    # ``purposes`` already excludes the originating fixed anchor (and the
    # trailing fixed anchor when present), but we need both anchor
    # purposes to know the to_act_type sequence over the trip legs.
    # ``find_assignment_problems`` reduces ``purposes`` to the variable
    # ones; we re-derive the full leg sequence using ``modes`` length.
    n_legs = len(modes)
    # Reconstruct the *to_act_type* per leg: every leg lands on either a
    # variable purpose (in ``problem['purposes']``) or the fixed
    # destination anchor (last leg if destination is fixed).
    fixed_destination = problem["destination"] is not None
    fixed_origin = problem["origin"] is not None

    leg_to_act = []
    var_iter = iter(purposes)
    for leg_idx in range(n_legs):
        if leg_idx == n_legs - 1 and fixed_destination:
            # Last leg lands on a fixed destination — purpose unknown
            # to chainsolvers ("home"/"work"/"education"); we'll mark
            # the to_x/to_y as known and use placeholder to_act_type.
            leg_to_act.append("__fixed__")
        else:
            leg_to_act.append(next(var_iter))
    return [
        {
            "leg_index": idx,
            "mode": modes[idx],
            "travel_time": float(travel_times[idx]),
            "to_act_type": leg_to_act[idx],
            "fixed_origin": fixed_origin,
            "fixed_destination": fixed_destination,
            "n_legs": n_legs,
        }
        for idx in range(n_legs)
    ]


def _build_plans_df(problems: List[Dict[str, Any]],
                    distributions: Dict[str, Any],
                    leisure_correction_factor: float,
                    random: np.random.RandomState,
                    shop_subtype_decider=None,
                    leisure_subtype_decider=None,
                    other_subtype_decider=None,
                    escort_location_decider=None,
                    escort_distance_by_type: bool = False) -> Tuple[pd.DataFrame, List[Dict[str, Any]], List[int], Dict[str, int]]:
    """Assemble the chainsolvers plans_df from BOUNDED problems only.

    Returns ``(plans_df, problem_meta, unbounded_indices, subtype_stats)``.
    Unbounded problems (tail / head / floating chains) are excluded — carla
    needs both endpoints anchored. They are placed by ``_fallback_place``.

    ``shop_subtype_decider`` (Tier 2: secondary_shop_daily_split). When None
    (default / OFF) the leg loop is byte-identical to the pre-feature path: a
    shop leg's activity and distance purpose are both ``"shop"``. When provided
    it is a callable ``(mode: str, travel_time_s: float) -> "shop_daily" |
    "shop_non_daily"`` that tags each shop leg's internal subtype, which becomes
    BOTH the chainsolver activity (so the leg is placed at a retail_daily /
    retail_non_daily building) AND the distance-distribution purpose (so it
    draws the shop_daily / shop_non_daily distance layer). It draws from its own
    seeded RNG (NOT ``random``), so the distance-sampling RNG stream — and hence
    the OFF path — stays byte-identical. ``subtype_stats`` reports how many shop
    legs were labelled daily / non_daily and how many fell back from a missing
    subtype distance layer to the aggregate ``"shop"`` layer (no silent
    fallback).

    ``leisure_subtype_decider`` / ``other_subtype_decider`` (Task 4, issue #127)
    mirror ``shop_subtype_decider`` exactly for the leisure and other purposes,
    each with its own dedicated seeded RNG (again NOT ``random``). The leisure
    decider returns one of ``LEISURE_SUBTYPE_ACTIVITIES``; the other decider
    returns one of ``OTHER_SUBTYPE_ACTIVITIES`` or ``"other_rest"``. The
    ``other_rest`` outcome is the one asymmetry versus shop/leisure: it is NOT a
    chainsolver activity name, so both the placement activity and the distance
    purpose stay at the plain ``"other"`` default (unchanged from the OFF path)
    -- only the realised-outcome count in ``subtype_stats`` changes. Both
    deciders default to None (OFF), leaving the leg loop byte-identical.

    ``escort_location_decider`` (issue #201) mirrors the subtype deciders for
    plan-level escort legs: it takes NO covariates and returns one of
    ESCORT_LOCATION_ACTIVITIES; the drawn name becomes the placement activity
    while the distance purpose is the single aggregate ``escort`` layer
    (fallback ``other``, counted).

    ``escort_distance_by_type`` (A3, issue #201 follow-up) refines that last
    step: when True AND a per-type layer exists for the drawn activity name
    (synthesized upstream by ``_synthesize_escort_type_layers``), the distance
    purpose becomes the drawn type itself instead of the aggregate ``escort``
    layer -- a Kita drop-off then samples the Kita-scaled layer, not the pooled
    one. Missing layers fall back COUNTED and two-level: drawn type -> aggregate
    ``escort`` (``subtype_stats["escort_type_distance_layer_fallback"]``) ->
    ``other`` (``subtype_stats["escort_distance_layer_fallback"]``); the two
    counters are mutually exclusive per leg. Default False is the OFF-path
    contract: byte-identical to the pre-A3 behaviour -- every escort leg samples
    the single aggregate ``escort`` layer (one-level fallback to ``other`` only)
    and ``subtype_stats`` carries no ``escort_type_distance_layer_fallback`` key
    at all, so callers can gate their own logging on the key's presence.
    """
    # Columnar accumulators: one typed list per output column instead of one
    # dict per leg row. At 100% (~3-4M leg rows) the list-of-dicts build held
    # hundreds of MB of dict overhead alive before from_records copied it all
    # again; the per-column lists build the same frame at a fraction of the
    # memory. The loop structure (and therefore the per-leg RNG draw order of
    # _sample_leg_distance) is unchanged, so the result is value-identical.
    col_uid: List[str] = []
    col_leg_id: List[str] = []
    col_act: List[str] = []
    col_dist: List[float] = []
    col_from_x: List[float] = []
    col_from_y: List[float] = []
    col_to_x: List[float] = []
    col_to_y: List[float] = []
    col_leg_index: List[int] = []
    col_prob_idx: List[int] = []

    problem_meta: List[Dict[str, Any]] = []
    unbounded_idx: List[int] = []

    # Subtype accounting (fallback transparency). Each decider's counters are
    # allocated only when that decider is active (ON path); on the fully-OFF
    # path (all three deciders None) subtype_stats stays the empty dict, so the
    # caller's logging gates (e.g. ``shop_subtype_decider is not None``) stay
    # consistent with the allocation gates here.
    subtype_stats: Dict[str, int] = {}
    if shop_subtype_decider is not None:
        subtype_stats.update({"shop_daily": 0, "shop_non_daily": 0, "distance_layer_fallback": 0})
    if leisure_subtype_decider is not None:
        subtype_stats.update({name: 0 for name in LEISURE_SUBTYPE_ACTIVITIES})
        subtype_stats["leisure_distance_layer_fallback"] = 0
    if other_subtype_decider is not None:
        subtype_stats.update({name: 0 for name in OTHER_SUBTYPE_ACTIVITIES})
        subtype_stats["other_rest"] = 0
        subtype_stats["other_distance_layer_fallback"] = 0
    if escort_location_decider is not None:
        subtype_stats.update({name: 0 for name in ESCORT_LOCATION_ACTIVITIES})
        subtype_stats["escort_distance_layer_fallback"] = 0
        if escort_distance_by_type:
            subtype_stats["escort_type_distance_layer_fallback"] = 0

    for prob_idx, problem in enumerate(problems):
        if problem["origin"] is None or problem["destination"] is None:
            unbounded_idx.append(prob_idx)
            continue

        legs = _problem_legs(problem)
        n_legs = problem["size"] + (
            (1 if problem["origin"] is not None else 0)
            + (1 if problem["destination"] is not None else 0)
        )
        # n_legs from modes length is authoritative.
        n_legs = len(legs)
        person_id = problem["person_id"]
        meta = {
            "person_id": person_id,
            "problem_idx": prob_idx,
            "activity_index": problem["activity_index"],
            "n_secondary": problem["size"],
            "n_legs": n_legs,
        }
        problem_meta.append(meta)

        origin_xy = (
            (float(problem["origin"][0, 0]), float(problem["origin"][0, 1]))
            if problem["origin"] is not None else (np.nan, np.nan)
        )
        dest_xy = (
            (float(problem["destination"][0, 0]),
             float(problem["destination"][0, 1]))
            if problem["destination"] is not None else (np.nan, np.nan)
        )

        for leg in legs:
            li = leg["leg_index"]
            to_act_type = leg["to_act_type"]

            # The eqasim purpose used for placement (the chainsolver activity)
            # and for the distance-distribution lookup. Default: the secondary
            # purpose itself ("shop"/"leisure"/"other"); non-secondary (fixed
            # anchor) legs use "other" for distance only.
            placement_act = to_act_type
            distance_purpose = (
                to_act_type if to_act_type in SECONDARY_PURPOSES else "other"
            )

            # Tier 2: resolve a shop leg to its daily / non-daily subtype. The
            # subtype is the chainsolver activity (-> retail_daily / non_daily
            # placement) AND the distance purpose (-> shop_daily / non_daily
            # distance layer). If the subtype layer is absent from the
            # distributions (sparse), fall back to the aggregate "shop" layer
            # for the DISTANCE only and count it; the placement activity still
            # carries the subtype so the building routing is unaffected.
            if shop_subtype_decider is not None and to_act_type == "shop":
                subtype = shop_subtype_decider(leg["mode"], leg["travel_time"])
                placement_act = subtype
                subtype_stats[subtype] += 1
                if _purpose_in_distributions(distributions, subtype):
                    distance_purpose = subtype
                else:
                    distance_purpose = "shop"
                    subtype_stats["distance_layer_fallback"] += 1

            # Task 4 (issue #127): resolve a leisure leg to one of the four
            # LEISURE_SUBTYPE_ACTIVITIES groups. Sibling to the shop block above:
            # the group is BOTH the chainsolver activity AND (with a logged
            # fallback to the aggregate "leisure" layer when the subtype
            # distance layer is absent) the distance purpose.
            if leisure_subtype_decider is not None and to_act_type == "leisure":
                group = leisure_subtype_decider(leg["mode"], leg["travel_time"])
                placement_act = group
                subtype_stats[group] += 1
                if _purpose_in_distributions(distributions, group):
                    distance_purpose = group
                else:
                    distance_purpose = "leisure"
                    subtype_stats["leisure_distance_layer_fallback"] += 1

            # Task 4 (issue #127): resolve an "other" leg to one of
            # OTHER_SUBTYPE_ACTIVITIES, or to "other_rest". Unlike shop/leisure,
            # "other_rest" is NOT itself a chainsolver activity or distance-layer
            # key -- placement_act and distance_purpose deliberately stay at
            # their to_act_type == "other" default for that outcome, so rest
            # legs are placed and distance-sampled exactly as on the OFF path.
            if other_subtype_decider is not None and to_act_type == "other":
                outcome = other_subtype_decider(leg["mode"], leg["travel_time"])
                subtype_stats[outcome] += 1
                if outcome != "other_rest":
                    placement_act = outcome
                    if _purpose_in_distributions(distributions, outcome):
                        distance_purpose = outcome
                    else:
                        distance_purpose = "other"
                        subtype_stats["other_distance_layer_fallback"] += 1

            # Issue #201: draw the location TYPE for a plan-level escort leg.
            # With escort_distance_by_type (A3) each drawn type samples its own
            # SrV-structured distance layer (keyed by the drawn activity name);
            # missing layers fall back COUNTED: type -> aggregate "escort" ->
            # "other". Without the flag all escort legs keep sampling the single
            # aggregate "escort" layer (byte-identical legacy behaviour).
            if escort_location_decider is not None and to_act_type == "escort":
                drawn = escort_location_decider()
                placement_act = drawn
                subtype_stats[drawn] += 1
                if escort_distance_by_type and _purpose_in_distributions(distributions, drawn):
                    distance_purpose = drawn
                elif _purpose_in_distributions(distributions, "escort"):
                    distance_purpose = "escort"
                    if escort_distance_by_type:
                        subtype_stats["escort_type_distance_layer_fallback"] += 1
                else:
                    distance_purpose = "other"
                    subtype_stats["escort_distance_layer_fallback"] += 1

            distance_m = _sample_leg_distance(
                distributions, leg["mode"], leg["travel_time"],
                distance_purpose,
                leisure_correction_factor, random,
            )

            # from_x/from_y: known iff first leg AND origin is fixed
            if li == 0:
                from_x, from_y = origin_xy
            else:
                from_x, from_y = (np.nan, np.nan)

            # to_x/to_y: known iff last leg AND destination is fixed
            if li == n_legs - 1 and dest_xy[0] == dest_xy[0]:  # not nan
                to_x, to_y = dest_xy
            else:
                to_x, to_y = (np.nan, np.nan)

            col_uid.append(f"{person_id}#{prob_idx}")
            col_leg_id.append(f"{person_id}#{prob_idx}#{li}")
            col_act.append(placement_act if placement_act != "__fixed__" else "home")
            col_dist.append(distance_m)
            col_from_x.append(from_x)
            col_from_y.append(from_y)
            col_to_x.append(to_x)
            col_to_y.append(to_y)
            col_leg_index.append(li)
            col_prob_idx.append(prob_idx)

    if not col_uid:
        # Preserve the legacy empty-frame shape (from_records([]) has NO
        # columns) so the no-bounded-legs early return behaves identically.
        return pd.DataFrame.from_records([]), problem_meta, unbounded_idx, subtype_stats

    plans_df = pd.DataFrame({
        "unique_person_id": col_uid,
        "unique_leg_id": col_leg_id,
        "to_act_type": col_act,
        "distance_meters": col_dist,
        "from_x": col_from_x,
        "from_y": col_from_y,
        "to_x": col_to_x,
        "to_y": col_to_y,
        "_leg_index": col_leg_index,
        "_problem_idx": col_prob_idx,
    })
    return plans_df, problem_meta, unbounded_idx, subtype_stats


def _build_rda_candidate_index(df_secondary: pd.DataFrame):
    """Build the eqasim ``CandidateIndex`` (3 KDTrees over the secondary
    candidate set) ONCE so it can be shared across both RDA fallback calls.

    The candidate coordinate array and the per-purpose ``destinations`` dict
    are derived purely from ``df_secondary`` and consume NO randomness, so
    building this once instead of once-per-fallback-call cannot change any
    drawn result. The candidate ordering (and therefore every KDTree query /
    sample index) is identical to the previous per-call construction.

    Returns the constructed ``CandidateIndex`` instance (its KDTrees are
    deterministic given the fixed candidate order).

    ``df_secondary`` here is always the LEGACY candidate frame (see the
    "Always the LEGACY frame" comment in ``execute()``), which predates issue
    #201 and therefore never carries an ``offers_escort`` column (escort
    candidates -- education facilities, residential buildings -- are appended
    only onto the REPLACE frame built by ``build_secondary_candidates`` /
    ``append_escort_candidates`` for the primary carla solve). Since "escort"
    is an unconditional member of ``SECONDARY_PURPOSES``, a purpose whose offer
    column is missing here gets an ANY-TYPE pool -- the FULL candidate set,
    via an all-True mask -- instead of being omitted from ``destinations``:
    per the #201 design spec scope amendment (docs/superpowers/specs/
    2026-07-24-escort-purpose-design.md, section 3), fallback-placed escort
    legs are meant to match any candidate type, not go unplaced. Building an
    extra KDTree over the full candidate set is harmless on the OFF path (no
    escort legs exist there, so it is never queried/sampled -- OFF-path
    output stays byte-identical; the only OFF-path cost is that one extra
    KDTree construction). The any-type substitution is itself a fallback, so
    it is logged (CLAUDE.md "Fallback transparency"), never silent.
    """
    from synthesis.population.spatial.secondary.components import CandidateIndex

    identifiers = df_secondary["location_id"].values
    # Vectorised coordinate access (GeoSeries.x/.y) instead of a per-geometry
    # Python lambda. np.column_stack preserves the exact (n, 2) row order of
    # df_secondary, so the candidate ordering is unchanged.
    coords = np.column_stack((
        df_secondary.geometry.x.values,
        df_secondary.geometry.y.values,
    ))
    n_candidates = len(df_secondary)
    destinations = {}
    any_type_purposes = []
    for purpose in SECONDARY_PURPOSES:
        offer_column = f"offers_{purpose}"
        if offer_column in df_secondary.columns:
            mask = df_secondary[offer_column].values
        else:
            # #201 spec amendment: no dedicated fallback pool for this
            # purpose (currently only "escort") -> any-type pool, an
            # all-True mask over the full candidate set.
            any_type_purposes.append(purpose)
            mask = np.ones(n_candidates, dtype=bool)
        destinations[purpose] = dict(
            identifiers=identifiers[mask],
            locations=coords[mask],
        )
    if any_type_purposes:
        print(
            "[braunschweig.secondary_chainsolvers] fallback catalog: "
            f"purpose(s) {sorted(any_type_purposes)} have no offers_* column "
            f"on the fallback candidate frame -> any-type pool (all "
            f"{n_candidates:,} candidates; #201 spec amendment)."
        )

    return CandidateIndex(destinations)


def _rda_fallback_place(problems: List[Dict[str, Any]],
                        problem_indices: List[int],
                        candidate_index,
                        distributions: Dict[str, Any],
                        leisure_correction_factor: float,
                        random: np.random.RandomState,
                        crs) -> Tuple[List[tuple], List[tuple]]:
    """Eqasim RDA-style fallback (GravityChainSolver + Angular tail + free chain).

    Drives the legacy ``AssignmentSolver`` pipeline (relaxation
    + discretization) on the subset of problems that chainsolvers' carla
    rejected — including unbounded chains (no anchored origin and/or
    destination) where the GravityChainSolver delegates to
    ``AngularTailSolver`` / ``CustomFreeChainSolver``. Output schema
    matches ``_fallback_place`` so the caller can splice rows in.

    ``candidate_index`` is the prebuilt :class:`CandidateIndex` shared across
    both fallback calls (see :func:`_build_rda_candidate_index`). It carries no
    state mutated by solving, so reusing the same instance across the unbounded
    and the failed-bounded calls is byte-identical to constructing a fresh one
    each time: the index is queried/sampled but never modified, and all
    randomness flows through ``random`` inside ``assignment_solver.solve``.
    """
    if not problem_indices:
        return [], []

    from synthesis.population.spatial.secondary.rda import (
        AssignmentSolver, DiscretizationErrorObjective,
        GravityChainSolver, AngularTailSolver, GeneralRelaxationSolver,
    )
    from synthesis.population.spatial.secondary.components import (
        CustomDistanceSampler, CustomDiscretizationSolver,
        CustomFreeChainSolver,
    )

    discretization_solver = CustomDiscretizationSolver(candidate_index)

    class _PurposeAwareDistanceSampler(CustomDistanceSampler):
        """``CustomDistanceSampler`` that understands the Tier-1 purpose-resolved
        distribution layout ``{purpose: {mode: ...}}``. The stock sampler indexes
        by mode and raises ``KeyError`` on that layout; this reuses the
        purpose-aware ``_sample_leg_distance`` (legacy ``{mode: ...}`` still works,
        auto-detected) so the fallback can actually place long-distance / unbounded
        chains instead of raising and dropping them (which crashed downstream)."""

        def sample_distances(self, problem):
            return _rda_sample_distances(
                self.distributions, problem,
                self.leisure_correction_factor, self.random,
            )

    distance_sampler = _PurposeAwareDistanceSampler(
        maximum_iterations=1000, random=random,
        distributions=distributions,
        leisure_correction_factor=leisure_correction_factor,
    )
    chain_solver = GravityChainSolver(
        random=random, eps=10.0, lateral_deviation=10.0, alpha=0.1,
        maximum_iterations=1000,
    )
    tail_solver = AngularTailSolver(random=random)
    free_solver = CustomFreeChainSolver(random, candidate_index)
    relaxation_solver = GeneralRelaxationSolver(
        chain_solver, tail_solver, free_solver,
    )
    objective = DiscretizationErrorObjective(thresholds=dict(
        car=200.0, car_passenger=200.0, pt=200.0,
        bicycle=100.0, walk=100.0,
    ))
    assignment_solver = AssignmentSolver(
        distance_sampler=distance_sampler,
        relaxation_solver=relaxation_solver,
        discretization_solver=discretization_solver,
        objective=objective,
        maximum_iterations=20,
    )

    out_rows: List[tuple] = []
    convergence_rows: List[tuple] = []
    n_failed = 0
    for prob_idx in problem_indices:
        problem = problems[prob_idx]
        try:
            result = assignment_solver.solve(problem)
        except Exception:
            n_failed += 1
            convergence_rows.append((False, problem["size"]))
            continue
        a0 = problem["activity_index"]
        for k, (identifier, location) in enumerate(zip(
            result["discretization"]["identifiers"],
            result["discretization"]["locations"],
        )):
            out_rows.append((
                problem["person_id"], a0 + k,
                identifier, geo.Point(location),
            ))
        convergence_rows.append((bool(result["valid"]), problem["size"]))

    print(
        f"[braunschweig.secondary_chainsolvers] RDA fallback placed "
        f"{len(problem_indices) - n_failed:,}/{len(problem_indices):,} "
        f"problems (raised={n_failed:,})"
    )
    return out_rows, convergence_rows


def _fallback_place(problems: List[Dict[str, Any]],
                    unbounded_idx: List[int],
                    df_secondary: pd.DataFrame,
                    random: np.random.RandomState,
                    crs) -> Tuple[List[tuple], List[tuple]]:
    """Random distance-aware placement for tail / head / floating chains.

    Picks any candidate of the right purpose; ignores distance optimisation.
    Quality is poor but coverage is preserved so downstream stages do not
    crash on missing rows. The minority of unbounded problems makes this
    acceptable as a stop-gap.

    ``df_secondary`` here is always the LEGACY candidate frame, which predates
    issue #201 and never carries an ``offers_escort`` column (mirrors
    :func:`_build_rda_candidate_index`; see its docstring). A missing
    ``offers_<purpose>`` column gets an ANY-TYPE pool -- the full candidate
    set -- rather than an empty one, per the #201 design spec scope amendment
    (fallback-placed escort legs must match any candidate type instead of
    going unplaced); logged, not silent.
    """
    if not unbounded_idx:
        return [], []

    pool: Dict[str, pd.DataFrame] = {}
    any_type_purposes = []
    for purpose in SECONDARY_PURPOSES:
        offer_column = f"offers_{purpose}"
        if offer_column in df_secondary.columns:
            pool[purpose] = df_secondary[df_secondary[offer_column]].reset_index(drop=True)
        else:
            # #201 spec amendment: any-type pool (the full candidate set)
            # instead of an empty one for a purpose with no offer column
            # (currently only "escort").
            any_type_purposes.append(purpose)
            pool[purpose] = df_secondary.reset_index(drop=True)
    if any_type_purposes:
        print(
            "[braunschweig.secondary_chainsolvers] fallback catalog: "
            f"purpose(s) {sorted(any_type_purposes)} have no offers_* column "
            f"on the fallback candidate frame -> any-type pool (all "
            f"{len(df_secondary):,} candidates; #201 spec amendment)."
        )

    out_rows: List[tuple] = []
    convergence_rows: List[tuple] = []

    for prob_idx in unbounded_idx:
        problem = problems[prob_idx]
        person_id = problem["person_id"]
        a0 = problem["activity_index"]
        n_placed = 0
        for k, purpose in enumerate(problem["purposes"]):
            cands = pool.get(purpose if purpose in SECONDARY_PURPOSES else "other")
            if cands is None or len(cands) == 0:
                continue
            i = random.randint(len(cands))
            row = cands.iloc[i]
            out_rows.append((
                person_id, a0 + k, str(row["location_id"]),
                geo.Point(float(row["geometry"].x), float(row["geometry"].y)),
            ))
            n_placed += 1
        convergence_rows.append((n_placed == problem["size"], problem["size"]))

    return out_rows, convergence_rows


# ---------------------------------------------------------------------------
# Result mapping
# ---------------------------------------------------------------------------

def _extract_locations(result_df: pd.DataFrame,
                       problem_meta: List[Dict[str, Any]],
                       df_secondary: pd.DataFrame,
                       crs) -> Tuple[gpd.GeoDataFrame, pd.DataFrame]:
    """Convert chainsolvers result rows back to eqasim's output shape.

    Pure transform (no randomness): the returned ``df_locations`` (row order,
    person_id, activity_index, location_id, geometry) and ``df_convergence``
    (valid, size, order) are byte-identical to the previous per-row loop. The
    body is fully vectorised because at full population scale the per-row Python
    loop (str.split, int casts, pd.isna, Point construction over millions of
    result rows) was the dominant single-core cost of this stage.
    """
    # Build location_id -> (x, y) lookup; here only its key set is needed to
    # decide whether the solver's candidate id is a known facility (the
    # canonical id is kept verbatim) or must fall back to a synthesised id.
    known_location_ids = set(df_secondary["location_id"].astype(str))

    # Index meta by problem_idx for activity_index / person_id recovery.
    meta_by_idx = {m["problem_idx"]: m for m in problem_meta}

    n_rows = len(result_df)
    # Empty-result fast path: ``pd.DataFrame.from_records([], columns=...)``
    # yields all-object columns (no rows to infer dtypes from). Reproduce that
    # exact (dtype-included) empty shape so the output stays byte-identical.
    if n_rows == 0:
        df_locations = gpd.GeoDataFrame(
            pd.DataFrame.from_records(
                [],
                columns=["person_id", "activity_index", "location_id", "geometry"],
            ),
            crs=crs,
        )
        df_convergence = pd.DataFrame.from_records(
            [(0 == m["n_secondary"], m["n_secondary"]) for m in problem_meta],
            columns=["valid", "size"],
        )
        return df_locations, df_convergence

    leg_ids = result_df["unique_leg_id"].to_numpy()
    to_act = result_df["to_act_type"].to_numpy()
    to_x = pd.to_numeric(result_df["to_x"], errors="coerce").to_numpy()
    to_y = pd.to_numeric(result_df["to_y"], errors="coerce").to_numpy()
    if "to_act_identifier" in result_df.columns:
        identifiers = result_df["to_act_identifier"].to_numpy()
    else:
        identifiers = np.array([None] * n_rows, dtype=object)

    # Split "{person_id}#{problem_idx}#{leg_index}" into its three fields. The
    # previous loop unpacked ``leg_id.split("#")`` into exactly three targets
    # and skipped (ValueError) any id that did not have exactly three fields, so
    # ``rsplit("#", 2)`` (three fields from the right) is combined with a hard
    # "exactly two '#'" mask to reproduce that filtering precisely.
    leg_id_series = pd.Series(leg_ids, dtype=object)
    hash_count = leg_id_series.str.count("#").to_numpy()
    split = leg_id_series.str.rsplit("#", n=2, expand=True)
    prob_idx_str = split[1].to_numpy()
    leg_idx_str = split[2].to_numpy()

    # Row-level keep mask, applied as the conjunction of every per-row skip in
    # the original loop, evaluated in result-frame order so the surviving rows
    # keep their original order.
    valid_split = hash_count == 2  # exactly three fields -> no ValueError

    # int() casts only on the rows that survived the split filter; map remaining
    # values to NaN so a downstream cast cannot raise on the skipped rows.
    prob_idx_num = pd.to_numeric(pd.Series(prob_idx_str), errors="coerce").to_numpy()
    leg_idx_num = pd.to_numeric(pd.Series(leg_idx_str), errors="coerce").to_numpy()
    # A field that does not parse as an int would have raised in the old loop;
    # such ids never occur for solver output but are excluded for safety so the
    # behaviour is at least as strict (skip rather than crash on the bad row).
    valid_split = valid_split & ~np.isnan(prob_idx_num) & ~np.isnan(leg_idx_num)

    prob_idx_int = np.where(valid_split, prob_idx_num, -1).astype(np.int64)
    leg_idx_int = np.where(valid_split, leg_idx_num, -1).astype(np.int64)

    # meta lookup (skip rows whose problem_idx is unknown), secondary-purpose
    # filter, and the NaN-coordinate filter -- all the original per-row skips.
    known_prob = np.array(
        [valid_split[i] and (prob_idx_int[i] in meta_by_idx) for i in range(n_rows)]
    )
    # Tier 2 / Task 4: the internal subtype activities (shop_daily/non_daily;
    # leisure_local/visit/activity/excursion; other_errand_short/long,
    # other_escort) are secondary too -- they map back to the eqasim "shop" /
    # "leisure" / "other" purpose respectively. Include them here so a
    # subtype-tagged leg is not silently dropped at extraction. The subtype
    # label never reaches the output schema (which carries no purpose:
    # [person_id, activity_index, location_id, geometry]); this is the implicit
    # map-back ("other_rest" needs no entry here: it is never a chainsolver
    # activity name, see _build_other_subtype_decider). Issue #201:
    # ESCORT_LOCATION_ACTIVITIES (the drawn location-TYPE names) map back to
    # the eqasim "escort" purpose the same implicit way.
    secondary_acts = (
        set(SECONDARY_PURPOSES)
        | set(SHOP_SUBTYPE_ACTIVITIES)
        | set(LEISURE_SUBTYPE_ACTIVITIES)
        | set(OTHER_SUBTYPE_ACTIVITIES)
        | set(ESCORT_LOCATION_ACTIVITIES)
    )
    is_secondary = pd.Series(to_act, dtype=object).isin(secondary_acts).to_numpy()
    coords_present = ~(np.isnan(to_x) | np.isnan(to_y))

    keep = known_prob & is_secondary & coords_present

    if not keep.any():
        df_locations = gpd.GeoDataFrame(
            pd.DataFrame.from_records(
                [],
                columns=["person_id", "activity_index", "location_id", "geometry"],
            ),
            crs=crs,
        )
        df_convergence = pd.DataFrame.from_records(
            [(0 == m["n_secondary"], m["n_secondary"]) for m in problem_meta],
            columns=["valid", "size"],
        )
        return df_locations, df_convergence

    kept_prob_idx = prob_idx_int[keep]
    kept_leg_idx = leg_idx_int[keep]

    # person_id and activity_index come from the per-problem meta (person_id
    # and activity_index + leg_index). Python ints from meta keep the int64
    # output dtype identical to the old ``from_records`` path.
    person_id = np.array(
        [meta_by_idx[p]["person_id"] for p in kept_prob_idx], dtype=np.int64
    )
    activity_index = np.array(
        [meta_by_idx[p]["activity_index"] for p in kept_prob_idx], dtype=np.int64
    ) + kept_leg_idx

    # Recover the canonical eqasim location_id from the solver's candidate
    # identifier; fall back to a synthesised "cs_{prob}_{leg}" id when the id is
    # not a string or is not a known facility (identical to the loop's rule).
    kept_cand = identifiers[keep]
    location_id = [
        cand if (isinstance(cand, str) and cand in known_location_ids)
        else f"cs_{kept_prob_idx[i]}_{kept_leg_idx[i]}"
        for i, cand in enumerate(kept_cand)
    ]

    # Geometry built in one shot from the float coordinate arrays.
    geometry = gpd.points_from_xy(to_x[keep], to_y[keep])

    df_locations = gpd.GeoDataFrame(
        {
            "person_id": person_id,
            "activity_index": activity_index,
            "location_id": np.asarray(location_id, dtype=object),
            "geometry": geometry,
        },
        crs=crs,
    )

    # Placed secondary legs per problem index, via a single groupby/size over
    # the kept rows (each problem's secondary legs have distinct activity
    # indices, so the count equals the number of distinct placed activities --
    # identical to the previous per-row accumulation).
    placed_per_prob: Dict[int, int] = (
        pd.Series(kept_prob_idx).value_counts().to_dict()
    )

    # Convergence flag in problem_meta order: valid iff all secondary legs of
    # the problem were placed. ``from_records`` keeps the bool/int64 dtypes
    # identical to the previous implementation.
    df_convergence = pd.DataFrame.from_records(
        [
            (placed_per_prob.get(m["problem_idx"], 0) == m["n_secondary"],
             m["n_secondary"])
            for m in problem_meta
        ],
        columns=["valid", "size"],
    )
    return df_locations, df_convergence


# ---------------------------------------------------------------------------
# Parallel chain solving
#
# Person chains are independent, so the population is sharded across worker
# processes. Each worker builds its own chainsolvers context (one cs.setup per
# shard) seeded deterministically from (base_seed, shard_index), so the result
# is fully reproducible given the seed and the worker count. Shard results are
# recombined in shard-index order regardless of completion order, so the output
# does not depend on scheduling.
# ---------------------------------------------------------------------------

# Per-leg result columns chainsolvers' solve() returns (used for the empty
# frame when no bounded legs are placed).
_CHAIN_RESULT_COLUMNS = [
    "unique_person_id", "unique_leg_id", "to_act_type",
    "distance_meters", "from_x", "from_y", "to_x", "to_y",
    "to_act_identifier",
]

# Number of persons per cs.solve() call within a shard. Solving in chunks
# amortises chainsolvers' per-call validation overhead; on a chunk failure the
# shard retries that chunk's persons individually so one bad person does not
# drop the rest.
_CHAIN_CHUNK_SIZE = 500

# Worker-process globals: the (read-only) locations table, solver name, and
# scorer spec are sent once per worker via the Pool initializer instead of being
# pickled with every task.
_WORKER_LOCATIONS_DF = None
_WORKER_SOLVER = None
_WORKER_SCORER_SPEC = None


def _empty_chain_result_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_CHAIN_RESULT_COLUMNS)


def _person_row_ranges(plans_df: pd.DataFrame, uid_col: str = "unique_person_id"):
    """Per-person contiguous row ranges of ``plans_df``, in appearance order.

    ``plans_df`` is built problem-by-problem, so all rows of one
    ``unique_person_id`` are adjacent. This computes ``(uid_order, starts,
    ends)`` so person/chunk/shard sub-frames can be taken as contiguous
    ``iloc`` slices instead of materialising every person's sub-frame at once
    via ``dict(tuple(groupby))`` (a memory spike at 100%: every sub-frame plus
    the dict alive simultaneously).

    Returns ``None`` when some person's rows are NOT contiguous (run count !=
    unique count) -- callers then fall back to the groupby dict, so slicing can
    never silently mix persons.
    """
    values = plans_df[uid_col].to_numpy()
    if len(values) == 0:
        empty = np.array([], dtype=np.int64)
        return values, empty, empty
    change = np.flatnonzero(values[1:] != values[:-1]) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [len(values)]))
    uid_order = values[starts]
    if len(uid_order) != pd.unique(values).size:
        return None
    return uid_order, starts, ends


def _make_person_shards(unique_persons: List[Any], n_workers: int) -> List[Tuple[int, List[Any]]]:
    """Split the person list into ``n_workers`` contiguous, balanced shards.

    Contiguous index-based slicing keeps the assignment deterministic and
    independent of the worker count's scheduling, so a run is reproducible.
    """
    n_workers = max(1, min(n_workers, len(unique_persons))) if unique_persons else 1
    shards: List[Tuple[int, List[Any]]] = []
    for shard_index, shard in enumerate(np.array_split(np.asarray(unique_persons, dtype=object), n_workers)):
        shard_list = list(shard)
        if shard_list:
            shards.append((shard_index, shard_list))
    return shards


def _derive_shard_seed(base_seed: int, shard_index: int) -> int:
    """Deterministic per-shard rng seed derived from the run seed and shard.

    Uses numpy ``SeedSequence`` so distinct shards get well-separated streams;
    the same (base_seed, shard_index) always yields the same seed.
    """
    return int(np.random.SeedSequence([int(base_seed), int(shard_index)]).generate_state(1)[0])


def _init_chain_worker(locations_df, solver, scorer_spec=None) -> None:
    # Pin BLAS/OpenMP to one thread FIRST (issue #122): with up to ~62 workers
    # each opening an ncores-sized BLAS pool, the box oversubscribes to
    # n_workers x ncores threads -- the exact failure class that segfaulted the
    # PopulationSim batches (see braunschweig.parallelism / popsim.batch).
    # Under fork the parent's BLAS is already initialised, so the helper also
    # applies a threadpoolctl runtime limit, not just the env variables.
    parallelism.limit_worker_blas_threads()
    global _WORKER_LOCATIONS_DF, _WORKER_SOLVER, _WORKER_SCORER_SPEC
    _WORKER_LOCATIONS_DF = locations_df
    _WORKER_SOLVER = solver
    _WORKER_SCORER_SPEC = scorer_spec


def _solve_person_shard(task):
    """Solve one shard of persons. Runs in a worker process (or in-process for
    the serial path). Returns ``(shard_index, result_df_or_None, failed_idx)``.

    Mirrors the legacy chunked solve loop exactly so the single-shard, seed=
    base_seed case is byte-identical to the pre-parallel serial behaviour.
    """
    import logging as _logging

    import chainsolvers as cs

    for _name in ("chainsolvers", "chainsolvers.io", "chainsolvers.locations"):
        _logging.getLogger(_name).setLevel(_logging.WARNING)

    shard_index, shard_uids, shard_df, shard_seed = task
    if _WORKER_SCORER_SPEC:
        # "_cs_parameters" is a non-Scorer key carrying the optional carla
        # selection parameters dict; pop it before forwarding to build_scorer.
        scorer_spec_copy = dict(_WORKER_SCORER_SPEC)
        cs_parameters = scorer_spec_copy.pop("_cs_parameters", None)
        scorer = build_scorer(**scorer_spec_copy)
    else:
        scorer = None
        cs_parameters = None
    ctx = cs.setup(
        locations_df=_WORKER_LOCATIONS_DF,
        solver=_WORKER_SOLVER or "carla",
        rng_seed=int(shard_seed),
        scorer=scorer,
        **({"parameters": cs_parameters} if cs_parameters is not None else {}),
    )

    # Person sub-frames are contiguous iloc slices of shard_df (rows are built
    # problem-by-problem), verified at runtime; the groupby dict is only the
    # fallback for a non-contiguous frame. A chunk of consecutive persons is
    # one contiguous block whose reset_index(drop=True) is identical (rows,
    # order, RangeIndex) to the legacy per-person concat.
    ranges = _person_row_ranges(shard_df)
    use_slices = (
        ranges is not None
        and np.array_equal(ranges[0], np.asarray(shard_uids, dtype=object))
    )
    if use_slices:
        _, row_starts, row_ends = ranges
    else:
        by_person = dict(tuple(shard_df.groupby("unique_person_id", sort=False)))

    result_chunks: List[pd.DataFrame] = []
    failed_problem_idx: List[int] = []

    for start in range(0, len(shard_uids), _CHAIN_CHUNK_SIZE):
        chunk_uids = shard_uids[start:start + _CHAIN_CHUNK_SIZE]
        if use_slices:
            chunk_df = shard_df.iloc[
                row_starts[start]:row_ends[start + len(chunk_uids) - 1]
            ].reset_index(drop=True)
        else:
            chunk_df = pd.concat([by_person[u] for u in chunk_uids], ignore_index=True)
        try:
            res_df, _seg, _v = cs.solve(ctx=ctx, plans_df=chunk_df)
            result_chunks.append(res_df)
        except Exception:
            # Retry per-person to isolate the failures.
            for offset, uid in enumerate(chunk_uids):
                person_chunk = (
                    shard_df.iloc[row_starts[start + offset]:row_ends[start + offset]]
                    if use_slices else by_person[uid]
                )
                try:
                    res_df, _seg, _v = cs.solve(ctx=ctx, plans_df=person_chunk)
                    result_chunks.append(res_df)
                except Exception:
                    try:
                        _, prob_idx_str = str(uid).rsplit("#", 1)
                        failed_problem_idx.append(int(prob_idx_str))
                    except ValueError:
                        pass

    result_df = pd.concat(result_chunks, ignore_index=True) if result_chunks else None
    return shard_index, result_df, failed_problem_idx


def _solve_chains_parallel(plans_for_cs, unique_persons, locations_df, solver,
                           base_seed, n_workers, t0, scorer_spec=None):
    """Solve all person chains across ``n_workers`` processes and recombine
    deterministically (results concatenated in shard-index order)."""
    shards = _make_person_shards(unique_persons, n_workers)

    # Shards are consecutive slices of unique_persons (appearance order), and
    # person rows are contiguous in plans_for_cs, so each shard frame is one
    # contiguous iloc block -- identical (rows, order, fresh RangeIndex) to the
    # legacy per-person concat, without holding every person's sub-frame alive
    # at once. Verified at runtime; groupby dict is the fallback.
    ranges = _person_row_ranges(plans_for_cs)
    use_slices = (
        ranges is not None
        and np.array_equal(ranges[0], np.asarray(unique_persons, dtype=object))
    )
    if not use_slices:
        by_person = dict(tuple(plans_for_cs.groupby("unique_person_id", sort=False)))

    tasks = []
    uid_pos = 0
    for shard_index, shard_uids in shards:
        if use_slices:
            _, row_starts, row_ends = ranges
            shard_frame = plans_for_cs.iloc[
                row_starts[uid_pos]:row_ends[uid_pos + len(shard_uids) - 1]
            ].reset_index(drop=True)
        else:
            shard_frame = pd.concat(
                [by_person[u] for u in shard_uids], ignore_index=True
            )
        tasks.append((
            shard_index, shard_uids, shard_frame,
            _derive_shard_seed(base_seed, shard_index),
        ))
        uid_pos += len(shard_uids)

    results_by_index: Dict[int, pd.DataFrame] = {}
    failed_problem_idx: List[int] = []
    n_done = 0
    pool_context = mp.get_context()  # platform default (fork on Linux)
    with pool_context.Pool(
        processes=len(tasks),
        initializer=_init_chain_worker,
        initargs=(locations_df, solver, scorer_spec),
    ) as pool:
        for shard_index, res_df, shard_failed in pool.imap_unordered(_solve_person_shard, tasks):
            results_by_index[shard_index] = res_df
            failed_problem_idx.extend(shard_failed)
            n_done += 1
            print(
                f"[braunschweig.secondary_chainsolvers] shard {n_done}/{len(tasks)} "
                f"done (elapsed={time.time() - t0:.0f}s)",
                flush=True,
            )

    ordered = [
        results_by_index[i] for i in sorted(results_by_index)
        if results_by_index[i] is not None
    ]
    result_df = pd.concat(ordered, ignore_index=True) if ordered else _empty_chain_result_df()
    # Deterministic order for the downstream fallback (which consumes the RNG).
    failed_problem_idx.sort()
    return result_df, failed_problem_idx


# ---------------------------------------------------------------------------
# Primary-vs-fallback accounting (fallback transparency)
#
# The PRIMARY method is the chainsolvers carla solver (cs.solve). Problems carla
# cannot place -- unbounded chains (no anchored origin and/or destination) plus
# bounded problems carla raised on -- fall through to the RDA / random FALLBACK.
# A high fallback share means the primary solver is effectively not working, so
# the share must be observable as an explicit rate rather than hidden inside the
# separate per-stage prints.
# ---------------------------------------------------------------------------

# Fallback share above which the summary line is flagged. A fallback share over
# this threshold means a large fraction of secondary trips are placed by the
# lower-quality fallback rather than the carla primary solver, i.e. carla is
# effectively not working and the result should not be trusted without
# investigation.
DEFAULT_FALLBACK_WARNING_SHARE = 0.20


def _fallback_accounting_summary(n_total_problems: int,
                                 n_unbounded: int,
                                 n_failed_bounded: int,
                                 warning_share: float = DEFAULT_FALLBACK_WARNING_SHARE) -> str:
    """Build the one-line PRIMARY (carla) vs FALLBACK accounting summary.

    Pure (no I/O, no randomness, no side effects) so it can be unit-tested
    without the optional ``chainsolvers`` package. It only counts; it never
    influences solving, fallback selection, the RNG, or any placed result.

    Args:
        n_total_problems: total number of assignment problems enumerated
            (bounded + unbounded). Equals ``len(problems)``.
        n_unbounded: unbounded problems routed straight to the fallback
            (no anchored origin and/or destination). Equals ``len(unbounded_idx)``.
        n_failed_bounded: bounded problems carla raised on, routed to the
            fallback. Equals ``len(failed_problem_idx)``.
        warning_share: fallback share (in [0, 1]) at or above which the line is
            prefixed with ``"WARNING: "``.

    Returns:
        A single human-readable log line. ``n_fallback = n_unbounded +
        n_failed_bounded`` is the FALLBACK count; the remainder
        (``n_total_problems - n_fallback``) is the PRIMARY (carla) count. The
        fallback share is reported as a percentage of all problems; when it is
        at or above ``warning_share`` the line is prefixed with ``"WARNING: "``.
    """
    n_fallback = n_unbounded + n_failed_bounded
    n_primary = n_total_problems - n_fallback
    if n_total_problems > 0:
        fallback_share = n_fallback / n_total_problems
    else:
        fallback_share = 0.0

    prefix = "WARNING: " if fallback_share >= warning_share else ""
    return (
        f"[braunschweig.secondary_chainsolvers] {prefix}primary/fallback split: "
        f"primary (carla) placed {n_primary:,}/{n_total_problems:,} problems "
        f"({(1.0 - fallback_share) * 100.0:.1f}%); "
        f"fallback placed {n_fallback:,}/{n_total_problems:,} "
        f"({fallback_share * 100.0:.1f}%) "
        f"[unbounded={n_unbounded:,}, carla-failed-bounded={n_failed_bounded:,}]"
    )


# ---------------------------------------------------------------------------
# Excursion boundary-clip transparency (issue #127, Task 6)
#
# The measured "leisure_excursion" MiD donor distances (45-100 km, design
# spec Taxonomy table) may exceed the farthest candidate actually available
# to a given leg's anchor -- buildings plus the external Gemeinde centroids
# appended by build_secondary_candidates "so carla can match desired
# distances beyond the study area instead of truncating to the area edge"
# (see the comment there). When the desired distance exceeds even that
# farthest candidate, the leg cannot be placed at its desired distance and
# necessarily clips to the edge of the candidate universe. This is measured
# and logged ONLY -- it changes no placement, sampling, or RNG draw.
# ---------------------------------------------------------------------------

# Clip share above which the summary line is flagged. Mirrors
# DEFAULT_FALLBACK_WARNING_SHARE's role: a "leisure_excursion" clip share at
# or above this fraction means most excursion legs cannot reach their
# measured donor distance with the current candidate set (region extent /
# external-candidate reach), and the resulting realised distances should not
# be read as if they matched the MiD donor tail without noting this.
DEFAULT_EXCURSION_CLIP_WARNING_SHARE = 0.50


def _excursion_desired_distances_and_anchors_m(plans_df: pd.DataFrame,
                                                problems: List[Dict[str, Any]]
                                                ) -> Tuple[np.ndarray, np.ndarray]:
    """Desired distances (metres) and anchors for ``"leisure_excursion"`` legs.

    ``plans_df`` must still carry ``_problem_idx`` (i.e. be the frame returned
    by ``_build_plans_df``, before the caller drops the helper columns for
    ``cs.solve()``). Every BOUNDED problem has both ``origin`` and
    ``destination`` fixed -- ``_build_plans_df`` routes any problem missing
    either anchor to ``unbounded_idx`` before this frame is built -- so
    ``problem["origin"]`` is always available here. The fixed origin (the
    person's actual anchor for that chain, e.g. home) is used as the leg's
    reference point for the candidate-reach ceiling: it is always available,
    unlike an intermediate, still-unresolved secondary location.

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        ``(desired_m, anchors_xy)``, parallel arrays of length
        n_excursion_legs; ``anchors_xy`` has shape ``(n, 2)``. Both are empty
        when no ``"leisure_excursion"`` leg is present (the flag is OFF, or
        no bounded leg happened to draw that group).
    """
    if plans_df.empty or "to_act_type" not in plans_df.columns:
        return np.array([], dtype=float), np.empty((0, 2), dtype=float)
    mask = (plans_df["to_act_type"] == "leisure_excursion").to_numpy()
    if not mask.any():
        return np.array([], dtype=float), np.empty((0, 2), dtype=float)
    desired_m = plans_df.loc[mask, "distance_meters"].to_numpy(dtype=float)
    prob_idx = plans_df.loc[mask, "_problem_idx"].to_numpy()
    anchors_xy = np.array(
        [
            (float(problems[p]["origin"][0, 0]), float(problems[p]["origin"][0, 1]))
            for p in prob_idx
        ],
        dtype=float,
    )
    return desired_m, anchors_xy


def _candidate_reach_ceiling_m(anchors_xy: np.ndarray, candidate_xy: np.ndarray) -> np.ndarray:
    """Per-anchor farthest-candidate distance (metres): the candidate-radius ceiling.

    For each anchor in ``anchors_xy`` (shape ``(n, 2)``), returns the maximum
    Euclidean distance to any row of ``candidate_xy`` (shape ``(m, 2)``, both
    in the same projected CRS, e.g. EPSG:25832 metres). This is a hard upper
    bound on what any placement could achieve from that anchor: no candidate
    lies farther away, so a desired distance exceeding it can never be
    matched. This does NOT model chainsolvers' own internal candidate-search
    radius (an implementation detail of the third-party ``chainsolvers``
    package, which adaptively widens its search) -- it is a purely
    geometric, data-driven ceiling derived from the candidate coordinates we
    actually feed into the solver, independent of solver internals.

    Raises
    ------
    ValueError
        If ``candidate_xy`` is empty (no ceiling can be computed).
    """
    if len(candidate_xy) == 0:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] cannot compute the "
            "leisure_excursion candidate-reach ceiling: the candidate "
            "coordinate set is empty."
        )
    # A small per-anchor loop rather than materialising the full (n x m)
    # distance matrix at once: anchor counts are the (comparatively small)
    # 'leisure_excursion' leg count, so this stays cheap even against the
    # full candidate set (tens of thousands of buildings + external
    # centroids).
    ceilings = np.empty(len(anchors_xy), dtype=float)
    for i in range(len(anchors_xy)):
        dx = candidate_xy[:, 0] - anchors_xy[i, 0]
        dy = candidate_xy[:, 1] - anchors_xy[i, 1]
        ceilings[i] = np.sqrt(dx * dx + dy * dy).max()
    return ceilings


def _excursion_boundary_clip_summary(n_clipped: int, n_total: int,
                                     warning_share: float = DEFAULT_EXCURSION_CLIP_WARNING_SHARE) -> str:
    """Build the one-line ``"leisure_excursion"`` boundary-clip transparency summary.

    Pure (no I/O, no randomness, no side effects) -- mirrors
    ``_fallback_accounting_summary``'s style. ``n_clipped`` counts
    ``"leisure_excursion"`` legs whose sampled desired distance exceeds the
    candidate-radius ceiling (``_candidate_reach_ceiling_m``): these legs
    cannot be placed at their desired distance and clip to the edge of the
    candidate universe. Measurement only; never influences placement,
    sampling, or the RNG. Logged even when ``n_clipped`` is 0 (CLAUDE.md
    "fallback transparency": the rate must always be observable, not only
    when it is non-zero).

    Args:
        n_clipped: legs whose desired distance exceeds the ceiling.
        n_total: total ``"leisure_excursion"`` legs measured this run.
        warning_share: clip share (in [0, 1]) at or above which the line is
            prefixed with ``"WARNING: "``.
    """
    if n_total == 0:
        return (
            "[braunschweig.secondary_chainsolvers] leisure_excursion "
            "boundary-clip: 0 bounded 'leisure_excursion' legs this run "
            "(nothing to measure)."
        )
    share = n_clipped / n_total
    prefix = "WARNING: " if share >= warning_share else ""
    return (
        f"[braunschweig.secondary_chainsolvers] {prefix}leisure_excursion "
        f"boundary-clip: {n_clipped:,}/{n_total:,} ({share * 100.0:.1f}%) "
        "bounded excursion legs sample a desired distance beyond the "
        "farthest available candidate and clip to the region edge."
    )


# ---------------------------------------------------------------------------
# Tier 2: daily / non-daily shop subtype decider
# ---------------------------------------------------------------------------

# Deterministic offset added to random_seed for the subtype-imputation RNG, so
# the subtype draws use a SEPARATE stream from the distance-sampling RNG
# (``random``) and therefore never perturb the distance draws -> the OFF path
# stays byte-identical.
SHOP_SUBTYPE_SEED_OFFSET = 90211

# Task 4 (issue #127): one further dedicated offset per NEW subtype decider,
# each one more than the last, so leisure and other each draw from their own
# separate RNG stream -- distinct from SHOP_SUBTYPE_SEED_OFFSET, from
# ``random`` (distance sampling), and from each other. None of the three
# subtype streams can perturb another, so any subset of {shop, leisure, other}
# splits being ON/OFF leaves the others' draws (and the distance RNG / OFF
# path) unchanged.
LEISURE_SUBTYPE_SEED_OFFSET = 90212  # SHOP_SUBTYPE_SEED_OFFSET + 1
OTHER_SUBTYPE_SEED_OFFSET = 90213    # SHOP_SUBTYPE_SEED_OFFSET + 2

# Issue #201: the escort location-type decider gets its own dedicated stream
# too, one more than the last subtype offset, so it cannot perturb the shop /
# leisure / other subtype draws, the distance RNG, or the OFF path.
ESCORT_LOCATION_SEED_OFFSET = 90214  # SHOP_SUBTYPE_SEED_OFFSET + 3


def _build_shop_subtype_decider(context, random_seed: int):
    """Build the per-leg shop daily/non-daily decider, or return None when OFF.

    Returns a callable ``(mode: str, travel_time_s: float) -> "shop_daily" |
    "shop_non_daily"`` (Tier 2) when ``secondary_shop_daily_split`` is ON, else
    ``None`` (the byte-identical OFF path). The conditional daily probability
    ``P(daily | mode, travel-time band)`` is estimated from the MiD 2023 Wege
    survey (labelled CATI/CAWI shop legs only) via
    ``braunschweig.popsim.shop_subtype.estimate_daily_probability``; per leg the
    subtype is drawn ``~ Bernoulli(P(daily | covariates))`` with a dedicated
    seeded RNG. ``secondary_shop_daily_share`` (a float in [0, 1]) overrides the
    MiD-estimated table with a flat marginal share (used to pin the share); when
    None the MiD conditional table is used. The labelled fraction is logged (no
    silent fallback).
    """
    if not context.config("secondary_shop_daily_split"):
        return None

    from braunschweig.popsim import mid as mid_module
    from braunschweig.popsim.shop_subtype import (
        SHOP_DAILY_W_ZWD,
        SHOP_NONDAILY_W_ZWD,
        estimate_daily_probability,
        impute_subtype,
        tt_band,
    )
    from braunschweig.popsim.trips import map_mode, mid_time_seconds

    pinned_share = context.config("secondary_shop_daily_share")
    min_obs = int(context.config("secondary_distance_min_obs"))

    if pinned_share is not None:
        # Flat marginal share: no covariate conditioning. ASSUMPTION-free in the
        # sense that the caller explicitly pinned it via config.
        marginal = float(pinned_share)
        prob: Dict[Any, float] = {("__marginal__", -1): marginal}
        print(
            "[braunschweig.secondary_chainsolvers] shop daily subtype: using "
            f"pinned flat daily share {marginal:.3f} "
            "(secondary_shop_daily_share set; MiD estimation skipped, "
            "labelled-fraction diagnostic N/A)."
        )
    else:
        # Estimate the conditional P(daily | mode, tt_band) from MiD Wege.
        mid_dir = context.config("braunschweig.population.popsim.mid_dir")
        mid_wege = mid_module.load_mid_wege(mid_dir)
        # estimate_daily_probability needs columns: W_ZWECK, mode, travel_time,
        # W_ZWD, W_GEW. map_mode derives "mode" from hvm_imp; travel_time is
        # arrival - departure in seconds (the same derivation the distance
        # distributions stage uses).
        mid_wege = map_mode(mid_wege)
        dep = mid_time_seconds(mid_wege, "W_SZS", "W_SZM")
        arr = mid_time_seconds(mid_wege, "W_AZS", "W_AZM")
        tt = arr - dep
        tt = tt.where(tt >= 0, tt + 24 * 3600)  # repair midnight crossing
        mid_wege = mid_wege.assign(travel_time=tt)

        n_shop = int((mid_wege["W_ZWECK"] == 4).sum())
        labelled_mask = (
            (mid_wege["W_ZWECK"] == 4)
            & mid_wege["W_ZWD"].isin(SHOP_DAILY_W_ZWD | SHOP_NONDAILY_W_ZWD)
        )
        n_labelled = int(labelled_mask.sum())
        prob = estimate_daily_probability(mid_wege, min_obs=min_obs)
        marginal = float(prob[("__marginal__", -1)])
        n_cells = sum(1 for k in prob if k != ("__marginal__", -1))
        print(
            "[braunschweig.secondary_chainsolvers] shop daily subtype: MiD "
            f"labelled shop legs {n_labelled:,}/{n_shop:,} "
            f"({100.0 * n_labelled / n_shop if n_shop else 0.0:.1f}%); "
            f"marginal daily share {marginal:.3f}; "
            f"{n_cells} (mode, tt_band) cells >= min_obs={min_obs} "
            "(thinner cells use the marginal)."
        )

    rng = np.random.RandomState(int(random_seed) + SHOP_SUBTYPE_SEED_OFFSET)

    def decide(mode: str, travel_time_s: float) -> str:
        # impute_subtype is vectorised; call it on a 1-element batch so the
        # estimation/imputation logic is shared (no duplicated probability
        # lookup). The dedicated rng keeps this independent of the distance RNG.
        is_daily = impute_subtype([mode], [travel_time_s], prob, marginal, rng)[0]
        return "shop_daily" if is_daily else "shop_non_daily"

    return decide


# ---------------------------------------------------------------------------
# Task 4 (issue #127): leisure / other subtype imputation deciders
# ---------------------------------------------------------------------------

def _inverse_cdf_choice(probs: Dict[str, float], group_names, draw: float) -> str:
    """Return the name in ``group_names`` whose cumulative probability first
    exceeds ``draw`` (standard inverse-CDF sampling): walk ``group_names`` in
    the given fixed order while accumulating a running sum, and pick the first
    entry whose cumulative probability exceeds ``draw``.

    This is exactly the per-leg selection rule
    ``braunschweig.popsim.purpose_subtype.impute_groups`` applies internally
    (see that function's determinism note) -- reused here as a plain one-leg
    helper INSTEAD OF calling ``impute_groups`` once per leg, because
    ``impute_groups`` is designed for a single BATCHED call over many legs and
    logs an aggregate marginal-fallback-rate message on every invocation.
    Calling it with a length-1 batch (as the per-leg decider architecture
    requires, mirroring ``_build_shop_subtype_decider``) would therefore emit
    one log line per fallback leg -- log spam at population scale (millions of
    legs). This helper performs the identical maths (one draw, a fixed-order
    cumulative sum, first-exceeding-index selection) without that per-call
    logging; the MODEL-level fallback rate (how many (mode, tt_band) cells got
    their own estimate vs. the marginal) is already logged once, at
    decider-build time, by ``estimate_group_probabilities`` itself -- so no
    fallback-rate signal is lost, only the per-leg spam.
    """
    cumulative = np.cumsum([probs.get(name, 0.0) for name in group_names])
    choice = int(np.clip(np.searchsorted(cumulative, draw, side="right"), 0, len(group_names) - 1))
    return group_names[choice]


def _build_leisure_subtype_decider(context, random_seed: int):
    """Build the per-leg leisure subtype decider, or return None when OFF.

    Sibling to ``_build_shop_subtype_decider``. Returns a callable
    ``(mode: str, travel_time_s: float) -> str``, one of
    ``LEISURE_SUBTYPE_ACTIVITIES`` (the ``purpose_subtype.LEISURE_GROUPS``
    keys), when ``secondary_leisure_subtype_split`` is ON, else ``None`` (the
    byte-identical OFF path). ``P(group | mode, tt_band)`` is estimated from
    the MiD 2023 Wege survey via
    ``braunschweig.popsim.purpose_subtype.estimate_group_probabilities`` (Task
    2, issue #127); that call logs the labelled-leg share and the (mode,
    tt_band) cell coverage ONCE, here, at decider-build time. Per leg the
    decider draws exactly one uniform sample from a dedicated seeded RNG
    (``LEISURE_SUBTYPE_SEED_OFFSET``, NOT ``random``) and resolves it via
    ``_inverse_cdf_choice`` -- see that helper's docstring for why the per-leg
    draw is done inline rather than via a per-leg call to ``impute_groups``.
    """
    if not context.config("secondary_leisure_subtype_split"):
        return None

    from braunschweig.popsim import mid as mid_module
    from braunschweig.popsim.purpose_subtype import (
        LEISURE_SPEC,
        estimate_group_probabilities,
        tt_band,
    )
    from braunschweig.popsim.trips import map_mode, mid_time_seconds

    min_obs = int(context.config("secondary_distance_min_obs"))
    mid_dir = context.config("braunschweig.population.popsim.mid_dir")
    mid_wege = mid_module.load_mid_wege(mid_dir)
    # estimate_group_probabilities needs W_ZWECK, mode, travel_time, W_GEW,
    # W_ZWD. map_mode derives "mode" from hvm_imp; travel_time is arrival -
    # departure in seconds (the same derivation as the shop decider / the
    # distance distributions stage).
    mid_wege = map_mode(mid_wege)
    dep = mid_time_seconds(mid_wege, "W_SZS", "W_SZM")
    arr = mid_time_seconds(mid_wege, "W_AZS", "W_AZM")
    tt = arr - dep
    tt = tt.where(tt >= 0, tt + 24 * 3600)  # repair midnight crossing
    mid_wege = mid_wege.assign(travel_time=tt)

    cell_probs, marginal = estimate_group_probabilities(mid_wege, LEISURE_SPEC, min_obs=min_obs)
    group_names = sorted(marginal)
    print(
        "[braunschweig.secondary_chainsolvers] leisure subtype: marginal shares "
        + ", ".join(f"{name}={marginal[name]:.3f}" for name in group_names)
    )

    rng = np.random.RandomState(int(random_seed) + LEISURE_SUBTYPE_SEED_OFFSET)

    def decide(mode: str, travel_time_s: float) -> str:
        probs = cell_probs.get((mode, tt_band(travel_time_s)), marginal)
        return _inverse_cdf_choice(probs, group_names, rng.random_sample())

    return decide


def _build_other_subtype_decider(context, random_seed: int):
    """Build the per-leg "other" errand/escort/rest subtype decider, or None
    when OFF.

    Sibling to ``_build_shop_subtype_decider`` / ``_build_leisure_subtype_decider``.
    Returns a callable ``(mode: str, travel_time_s: float) -> str``, one of
    ``OTHER_SUBTYPE_ACTIVITIES`` (``"other_errand_short"``/``"other_errand_long"``/
    ``"other_escort"``) or ``"other_rest"``, when ``secondary_other_subtype_split``
    is ON, else ``None`` (the byte-identical OFF path).

    The MiD "other" umbrella (following_purpose == "other", i.e. raw W_ZWECK in
    {5, 6, 10}) is split in TWO composed stages, mirroring how the
    distance-distribution layer treats it (Task 3, issue #127 --
    ``braunschweig.popsim.distance_distributions``):

    1. A coarse, ALWAYS-labelled 3-way split {errand, escort, rest} estimated
       directly from the raw W_ZWECK code via a local
       ``purpose_subtype.SubtypeSpec`` with ``group_col="W_ZWECK"``: escort =
       W_ZWECK in ``purpose_subtype.OTHER_ESCORT_ZWECK`` ({6}); errand =
       W_ZWECK in ``purpose_subtype.OTHER_ERRAND_ZWECK`` ({5}); rest = the
       remaining "other" W_ZWECK codes. "rest" is derived from
       ``braunschweig.popsim.trips.PURPOSE_BY_W_ZWECK`` -- the single source of
       truth for which raw W_ZWECK codes map to the eqasim "other" purpose --
       rather than hardcoded, so it can never silently drift from that
       mapping. No W_ZWD is needed for this split, so it is never thinned by a
       missing detail code (escort legs in particular carry no W_ZWD at all).
    2. Within errand (W_ZWECK == 5) legs only, the existing W_ZWD-based
       short/long split (``purpose_subtype.OTHER_ERRAND_SPEC``, Task 2), with
       its own marginal fallback for unlabelled-W_ZWD errand legs.

    Both stages are estimated conditionally on (mode, tt_band); each logs its
    own labelled-leg share and cell coverage ONCE, here, at decider-build time.
    Per leg the two stages are composed into ONE 4-outcome probability vector
    -- P(escort), P(rest), P(errand) * P(short | errand), P(errand) * P(long |
    errand) -- and exactly one uniform draw from a dedicated seeded RNG
    (``OTHER_SUBTYPE_SEED_OFFSET``, NOT ``random``) selects the outcome via
    ``_inverse_cdf_choice``, so every "other" leg -- errand, escort, or rest --
    consumes the same single draw per leg as the shop/leisure deciders.

    When ``escort_purpose`` is ON (issue #201) escort is realised as its own
    plan-level purpose upstream (see ``_build_escort_location_decider``), so no
    leg with ``following_purpose == "other"`` can carry a raw W_ZWECK in
    ``OTHER_ESCORT_ZWECK`` any more. Stage 1 then collapses to a 2-way
    {errand, rest} split estimated only on the remaining "other" W_ZWECK codes,
    and the outcome vocabulary drops ``"other_escort"`` accordingly. With
    ``escort_purpose`` OFF this is value-identical to the previous 3-way split
    (same ``group_names`` tuple, same probability composition, same single
    draw).
    """
    if not context.config("secondary_other_subtype_split"):
        return None

    escort_purpose_on = bool(context.config("escort_purpose"))  # one-arg: execute-context read; key declared in configure()

    from braunschweig.popsim import mid as mid_module
    from braunschweig.popsim.purpose_subtype import (
        OTHER_ERRAND_SPEC,
        OTHER_ERRAND_ZWECK,
        OTHER_ESCORT_ZWECK,
        SubtypeSpec,
        estimate_group_probabilities,
        tt_band,
    )
    from braunschweig.popsim.trips import PURPOSE_BY_W_ZWECK, map_mode, mid_time_seconds

    min_obs = int(context.config("secondary_distance_min_obs"))
    mid_dir = context.config("braunschweig.population.popsim.mid_dir")
    mid_wege = mid_module.load_mid_wege(mid_dir)
    mid_wege = map_mode(mid_wege)
    dep = mid_time_seconds(mid_wege, "W_SZS", "W_SZM")
    arr = mid_time_seconds(mid_wege, "W_AZS", "W_AZM")
    tt = arr - dep
    tt = tt.where(tt >= 0, tt + 24 * 3600)  # repair midnight crossing
    mid_wege = mid_wege.assign(travel_time=tt)

    # Stage 1: coarse errand/(escort/)rest split, labelled directly by the raw
    # W_ZWECK code (never thinned by a missing W_ZWD).
    other_zweck = frozenset(
        code for code, purpose in PURPOSE_BY_W_ZWECK.items() if purpose == "other"
    )
    if escort_purpose_on:
        # Issue #201: with escort as a dedicated plan-level purpose no escort
        # leg reaches following_purpose == "other" any more, so the coarse
        # split estimates only {errand, rest} on the remaining "other" codes.
        other_zweck = other_zweck - OTHER_ESCORT_ZWECK
        coarse_groups = {"errand": OTHER_ERRAND_ZWECK,
                         "rest": other_zweck - OTHER_ERRAND_ZWECK}
    else:
        coarse_groups = {"errand": OTHER_ERRAND_ZWECK,
                         "escort": OTHER_ESCORT_ZWECK,
                         "rest": other_zweck - OTHER_ERRAND_ZWECK - OTHER_ESCORT_ZWECK}
    coarse_spec = SubtypeSpec(
        purpose_label="other_coarse",
        zweck_values=other_zweck,
        groups=coarse_groups,
        sentinels=frozenset(),
        group_col="W_ZWECK",
    )
    coarse_cell_probs, coarse_marginal = estimate_group_probabilities(
        mid_wege, coarse_spec, min_obs=min_obs)

    # Stage 2: within errand legs, the existing W_ZWD-based short/long split.
    errand_cell_probs, errand_marginal = estimate_group_probabilities(
        mid_wege, OTHER_ERRAND_SPEC, min_obs=min_obs)

    # Issue #201: "escort" is only a coarse_marginal key when escort_purpose is
    # OFF (Stage 1 above only builds that group in the 3-way OFF-path spec) --
    # include it in the summary line only when present, rather than a KeyError
    # or a fabricated 0.000 entry for a group that was never estimated.
    escort_share = coarse_marginal.get("escort")
    escort_summary = f"escort={escort_share:.3f}, " if escort_share is not None else ""
    print(
        "[braunschweig.secondary_chainsolvers] other subtype: coarse marginal shares "
        f"{escort_summary}errand={coarse_marginal['errand']:.3f}, "
        f"rest={coarse_marginal['rest']:.3f}; errand marginal shares "
        f"other_errand_short={errand_marginal['other_errand_short']:.3f}, "
        f"other_errand_long={errand_marginal['other_errand_long']:.3f}"
    )

    outcome_names = ["other_errand_short", "other_errand_long", "other_rest"]
    if not escort_purpose_on:
        outcome_names.append("other_escort")
    group_names = tuple(sorted(outcome_names))
    rng = np.random.RandomState(int(random_seed) + OTHER_SUBTYPE_SEED_OFFSET)

    def decide(mode: str, travel_time_s: float) -> str:
        band = tt_band(travel_time_s)
        coarse = coarse_cell_probs.get((mode, band), coarse_marginal)
        errand = errand_cell_probs.get((mode, band), errand_marginal)
        p_errand = coarse.get("errand", 0.0)
        probs = {
            "other_rest": coarse.get("rest", 0.0),
            "other_errand_short": p_errand * errand.get("other_errand_short", 0.0),
            "other_errand_long": p_errand * errand.get("other_errand_long", 0.0),
        }
        if not escort_purpose_on:
            probs["other_escort"] = coarse.get("escort", 0.0)
        return _inverse_cdf_choice(probs, group_names, rng.random_sample())

    return decide


def _build_escort_location_decider(context, random_seed: int):
    """Build the per-leg escort location-TYPE decider, or None when OFF.

    Issue #201: every plan-level "escort" leg draws ONE location category
    (education by school type / other / leisure / residential / shop) from the
    configured weight vector -- no covariate conditioning; the weights are the
    SrV-2023-BS+RGB observed destination-type shares
    (scripts/derive_escort_location_weights.py). Returns a callable
    ``() -> str`` yielding one of ESCORT_LOCATION_ACTIVITIES, consuming exactly
    one uniform draw per call from a dedicated seeded RNG
    (ESCORT_LOCATION_SEED_OFFSET), so the distance RNG and the three subtype
    decider streams stay untouched (OFF path byte-identical).
    """
    if not context.config("escort_purpose"):
        return None

    # Execute-context config() takes the key alone (declared defaults live in
    # configure(), wired by the next task); see
    # tests/test_execute_context_config_contract.py for the two-argument
    # crash this avoids.
    activities = list(context.config("escort_locations_activities"))
    weights = [float(w) for w in context.config("escort_locations_weights")]

    if len(activities) != len(weights):
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] escort_locations_activities and "
            f"escort_locations_weights must have the same length, got "
            f"{len(activities)} and {len(weights)}."
        )
    if len(set(activities)) != len(activities):
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] escort_locations_activities "
            f"contains duplicate escort location categories: {activities}."
        )
    unknown = sorted(set(activities) - set(ESCORT_CATEGORY_TO_ACTIVITY))
    if unknown:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] unknown escort location "
            f"category(ies) {unknown}; allowed: {sorted(ESCORT_CATEGORY_TO_ACTIVITY)}."
        )
    if any(w < 0.0 for w in weights) or sum(weights) <= 0.0:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] escort_locations_weights must be "
            "non-negative with a positive sum."
        )

    total = float(sum(weights))
    probs = {
        ESCORT_CATEGORY_TO_ACTIVITY[category]: weight / total
        for category, weight in zip(activities, weights)
    }
    group_names = tuple(ESCORT_CATEGORY_TO_ACTIVITY[c] for c in activities)
    print(
        "[braunschweig.secondary_chainsolvers] escort location draw: "
        + ", ".join(f"{c}={w / total:.3f}" for c, w in zip(activities, weights))
        + " (SrV 2023 BS+RGB derived defaults; see "
          "srv2023_escort_destination_types.csv)"
    )

    rng = np.random.RandomState(int(random_seed) + ESCORT_LOCATION_SEED_OFFSET)

    def decide() -> str:
        return _inverse_cdf_choice(probs, group_names, rng.random_sample())

    return decide


def _build_escort_distance_factor_map(context):
    """{activity_name: factor} for escort distance-by-type (A3), or None when OFF.

    Factors are SrV between-type structure ratios applied to the MiD escort
    level (spec 2026-08-11). Keys are the chainsolver activity names the
    escort location decider draws (ESCORT_CATEGORY_TO_ACTIVITY values), so the
    leg loop can use the drawn name as the distance-layer key directly.
    """
    if not context.config("escort_distance_by_type"):
        return None
    if not context.config("escort_purpose"):
        raise RuntimeError(
            "[braunschweig.secondary_chainsolvers] escort_distance_by_type requires "
            "escort_purpose to be ON (there is no escort distance layer to scale)."
        )
    activities = list(context.config("escort_distance_factor_activities"))
    factors = [float(f) for f in context.config("escort_distance_factors")]
    if len(activities) != len(factors):
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] escort_distance_factor_activities "
            f"and escort_distance_factors must have the same length, got "
            f"{len(activities)} and {len(factors)}."
        )
    if len(set(activities)) != len(activities):
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] escort_distance_factor_activities "
            f"contains duplicate escort location categories: {activities}."
        )
    unknown = sorted(set(activities) - set(ESCORT_CATEGORY_TO_ACTIVITY))
    if unknown:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] unknown escort location "
            f"categor{'y' if len(unknown) == 1 else 'ies'} in "
            f"escort_distance_factor_activities: {unknown}."
        )
    if any(f <= 0.0 for f in factors):
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] escort_distance_factors must be "
            f"positive, got {factors}."
        )

    # Vocabulary consistency (final-review finding): escort_locations_activities
    # (what the location decider actually draws) and escort_distance_factor_activities
    # (what has a factor entry) are configured independently -- a draw category
    # missing a factor entry falls back silently unless flagged HERE, before any
    # leg is placed. Reading escort_locations_activities is safe: this same stage
    # declares it in configure(), so it is always present once execute() runs.
    drawn_categories = set(context.config("escort_locations_activities"))
    missing_factors = sorted(drawn_categories - set(activities))
    if missing_factors:
        print(
            "[braunschweig.secondary_chainsolvers] WARNING: escort_distance_by_type: "
            f"no distance factor for drawn categor{'y' if len(missing_factors) == 1 else 'ies'} "
            f"{missing_factors} -- their legs will fall back counted to the aggregate "
            "escort layer."
        )
    return {ESCORT_CATEGORY_TO_ACTIVITY[c]: f for c, f in zip(activities, factors)}


def _rate_pct(count, total) -> float:
    """Percentage of ``count`` over ``total``, or 0.0 when ``total`` is falsy
    (guards the ZeroDivisionError on an empty leg group, e.g. no bounded
    escort legs at all). Shared by every fallback-rate / per-group-share
    percentage the execute() summary print block below reports, so the
    guarded formula is defined once instead of being repeated inline at
    each call site."""
    return 100.0 * count / total if total else 0.0


# ---------------------------------------------------------------------------
# synpp execute
# ---------------------------------------------------------------------------

def execute(context):
    # Import eagerly (not used here directly) to fail fast with a clear error if
    # the optional dependency is missing, rather than deep inside a worker; the
    # actual solving imports it again in _solve_person_shard. Kept lazy at
    # function scope so tests without the dependency can import this module.
    import chainsolvers  # noqa: F401

    df_trips = context.stage("synthesis.population.trips").sort_values(
        by=["person_id", "trip_index"]
    )
    df_trips["travel_time"] = (
        df_trips["arrival_time"] - df_trips["departure_time"]
    )

    # Household escort link (issue #201 Phase 2): before enumerating assignment
    # problems, rewrite linked escorters' plan-level "escort" purposes to the
    # fixed "escort_linked" purpose so they anchor at the child's education
    # location instead of drawing a location type. Unlinked escorters keep the
    # plain "escort" purpose and go through the SrV-weighted draw.
    escort_household_link = bool(context.config("escort_household_link"))
    df_escort_links = None
    linked_location_rows = None
    escort_activity_anchors = None
    if escort_household_link:
        if not bool(context.config("escort_purpose")):
            raise RuntimeError(
                "[braunschweig.secondary_chainsolvers] escort_household_link requires "
                "escort_purpose to be ON (there is no plan-level escort purpose to link)."
            )
        from braunschweig.synthesis.locations.escort_links import (
            assign_escort_anchors, build_escort_links,
        )
        df_persons_link = context.stage("synthesis.population.sampled")
        if "HP_ALTER" not in df_persons_link.columns:
            raise RuntimeError(
                "[braunschweig.secondary_chainsolvers] escort_household_link needs the "
                "HP_ALTER age column on synthesis.population.sampled (popsim_mid "
                "persons carry it); disable escort_household_link for producers "
                "without it."
            )
        df_persons_link = df_persons_link[["person_id", "household_id", "HP_ALTER"]]
        _df_work_link, df_education_link = context.stage(
            "synthesis.population.spatial.primary.locations")
        df_escort_links, link_stats = build_escort_links(
            df_persons_link, df_education_link, df_trips,
            max_child_age_years=int(
                context.config("escort_household_link_max_child_age_years")),
        )
        # Per-activity anchors under the consecutive-run rule (multi-child
        # fix): the trip rewrite, the anchors dict for the problem splitter,
        # and the appended location rows ALL derive from this ONE assignment,
        # so every escort_linked boundary resolves by construction.
        linked_location_rows, anchor_stats = assign_escort_anchors(
            df_trips, df_escort_links)
        df_trips = rewrite_linked_escort_trips(df_trips, linked_location_rows)
        escort_activity_anchors = {
            (row.person_id, row.activity_index): row.geometry
            for row in linked_location_rows.itertuples(index=False)
        }
        print(
            "[braunschweig.secondary_chainsolvers] escort household link: "
            f"{link_stats['n_linked']:,}/{link_stats['n_escorters']:,} escorters "
            f"linked ({100.0 * link_stats['link_rate'] if link_stats['n_escorters'] else 0.0:.1f}%) "
            f"to {link_stats['n_child_links']:,} escorter-child links; "
            f"{anchor_stats['n_anchored']:,}/{anchor_stats['n_escort_activities']:,} "
            f"escort activities anchored across {anchor_stats['n_runs']:,} runs, "
            f"{anchor_stats['n_overflow_to_draw']:,} beyond the linkable children "
            "-> SrV-weighted draw."
        )
    df_primary, crs = _prepare_primary(context)

    distance_distributions = context.stage(
        "synthesis.population.spatial.secondary.distance_distributions"
    )
    # The LEGACY candidate frame stays a separate, named variable: the
    # RDA/unbounded fallback intentionally places on it (stable sec_* ids),
    # while the primary solve uses the assembled REPLACE candidate set below.
    df_secondary_legacy = context.stage("synthesis.locations.secondary")
    df_secondary = df_secondary_legacy

    # Apply the same calibration tweaks as the legacy stage so the
    # input-side distributions are bit-comparable. ``_resample_distributions``
    # returns a resampled deep copy; the cached stage object (shared with the
    # legacy locations stage) is never mutated, so it can never be
    # double-resampled across consumers.
    distance_distributions = _resample_distributions(distance_distributions, dict(
        car=0.0, car_passenger=0.1, pt=0.5, bicycle=0.0, walk=-0.5,
    ))

    # Escort distance-by-type (A3): synthesize per-type layers on the PRIVATE
    # resampled copy (never the shared cached stage object).
    escort_distance_factor_map = _build_escort_distance_factor_map(context)
    if escort_distance_factor_map is not None:
        distance_distributions = _synthesize_escort_type_layers(
            distance_distributions, escort_distance_factor_map)
        print(
            "[braunschweig.secondary_chainsolvers] escort distance-by-type: "
            + ", ".join(f"{a} x{f:.3f}" for a, f in escort_distance_factor_map.items())
            + " (SrV between-type structure on the MiD escort level; "
              "srv2023_escort_distance_factors.csv)"
        )

    random_seed = context.config("random_seed")
    random = np.random.RandomState(random_seed)
    leisure_corr = float(context.config("leisure_correction_factor"))

    # Tier 2 / Task 4: shop / leisure / other subtype deciders (each None when
    # its flag is OFF, so the leg loop and the candidate build stay
    # byte-identical). Built before solving so their MiD load /
    # probability-estimation logging happens exactly once, up front.
    shop_daily_split = bool(context.config("secondary_shop_daily_split"))
    shop_subtype_decider = _build_shop_subtype_decider(context, random_seed)
    leisure_subtype_split = bool(context.config("secondary_leisure_subtype_split"))
    leisure_subtype_decider = _build_leisure_subtype_decider(context, random_seed)
    other_subtype_split = bool(context.config("secondary_other_subtype_split"))
    other_subtype_decider = _build_other_subtype_decider(context, random_seed)
    escort_purpose_on = bool(context.config("escort_purpose"))
    escort_location_decider = _build_escort_location_decider(context, random_seed)
    leisure_visit_building_potential = bool(context.config("leisure_visit_building_potential"))

    fallback_strategy = (
        context.config("braunschweig.chainsolvers.fallback") or "rda"
    )
    fallback_strategy = fallback_strategy.lower()
    if fallback_strategy not in {"rda", "random"}:
        raise RuntimeError(
            f"[braunschweig.secondary_chainsolvers] unknown fallback "
            f"strategy {fallback_strategy!r} (expected 'rda' or 'random')."
        )

    # The RDA candidate index (3 KDTrees over the full secondary candidate set)
    # is expensive to build and is independent of the problems being placed, so
    # it is constructed at most ONCE and reused across both fallback calls
    # (unbounded chains and failed-bounded problems). It consumes no randomness,
    # so building it once instead of twice cannot change any drawn result. Built
    # lazily so the "random" strategy and the no-fallback case never pay for it.
    rda_index_cache: Dict[str, Any] = {}

    def _run_fallback(problem_indices):
        if not problem_indices:
            return [], []
        if fallback_strategy == "rda":
            if "index" not in rda_index_cache:
                # Always the LEGACY frame: previously the closure late-bound
                # df_secondary, so a run with zero unbounded chains but some
                # carla-failed problems would have built the index on the
                # REPLACE set instead -- pinning to legacy makes the fallback
                # candidate set deterministic regardless of call order.
                rda_index_cache["index"] = _build_rda_candidate_index(df_secondary_legacy)
            return _rda_fallback_place(
                problems, problem_indices, rda_index_cache["index"],
                distance_distributions, leisure_corr, random, crs,
            )
        return _fallback_place(
            problems, problem_indices, df_secondary_legacy, random, crs,
        )

    print(
        "[braunschweig.secondary_chainsolvers] enumerating "
        "assignment problems..."
    )
    t0 = time.time()
    problems = list(find_assignment_problems(
        df_trips, df_primary, activity_anchors=escort_activity_anchors,
    ))
    print(
        f"[braunschweig.secondary_chainsolvers] {len(problems):,} problems "
        f"in {time.time() - t0:.1f}s — building chainsolvers plans..."
    )

    plans_df, problem_meta, unbounded_idx, subtype_stats = _build_plans_df(
        problems, distance_distributions, leisure_corr, random,
        shop_subtype_decider=shop_subtype_decider,
        leisure_subtype_decider=leisure_subtype_decider,
        other_subtype_decider=other_subtype_decider,
        escort_location_decider=escort_location_decider,
        escort_distance_by_type=escort_distance_factor_map is not None,
    )
    if escort_location_decider is None and len(plans_df) and \
            (plans_df["to_act_type"] == "escort").any():
        raise RuntimeError(
            "[braunschweig.secondary_chainsolvers] plans contain 'escort' legs but "
            "escort_purpose is OFF in this stage -- the donor mapping and the "
            "chainsolver must be driven by the SAME escort_purpose config key."
        )
    print(
        f"[braunschweig.secondary_chainsolvers] bounded problems: "
        f"{len(problem_meta):,}; unbounded (fallback): {len(unbounded_idx):,}"
    )
    if shop_subtype_decider is not None:
        n_daily = subtype_stats["shop_daily"]
        n_nondaily = subtype_stats["shop_non_daily"]
        n_shop_legs = n_daily + n_nondaily
        realised_daily = (n_daily / n_shop_legs) if n_shop_legs else 0.0
        n_dist_fb = subtype_stats["distance_layer_fallback"]
        print(
            "[braunschweig.secondary_chainsolvers] shop subtype labelling "
            "(bounded shop legs only; unbounded go to fallback untagged): "
            f"{n_shop_legs:,} bounded shop legs -> daily {n_daily:,} "
            f"({100.0 * realised_daily:.1f}%), non_daily {n_nondaily:,} "
            f"({100.0 * (1.0 - realised_daily):.1f}%); "
            f"distance-layer fallback to aggregate 'shop' "
            f"{n_dist_fb:,}/{n_shop_legs:,} "
            f"({_rate_pct(n_dist_fb, n_shop_legs):.1f}%)"
        )
    if leisure_subtype_decider is not None:
        n_by_group = {name: subtype_stats[name] for name in LEISURE_SUBTYPE_ACTIVITIES}
        n_leisure_legs = sum(n_by_group.values())
        n_dist_fb = subtype_stats["leisure_distance_layer_fallback"]
        shares = ", ".join(
            f"{name} {count:,} ({_rate_pct(count, n_leisure_legs):.1f}%)"
            for name, count in n_by_group.items()
        )
        print(
            "[braunschweig.secondary_chainsolvers] leisure subtype labelling "
            "(bounded leisure legs only; unbounded go to fallback untagged): "
            f"{n_leisure_legs:,} bounded leisure legs -> {shares}; "
            f"distance-layer fallback to aggregate 'leisure' "
            f"{n_dist_fb:,}/{n_leisure_legs:,} "
            f"({_rate_pct(n_dist_fb, n_leisure_legs):.1f}%)"
        )
    if other_subtype_decider is not None:
        n_by_outcome = {name: subtype_stats[name] for name in (*OTHER_SUBTYPE_ACTIVITIES, "other_rest")}
        n_other_legs = sum(n_by_outcome.values())
        n_dist_fb = subtype_stats["other_distance_layer_fallback"]
        shares = ", ".join(
            f"{name} {count:,} ({_rate_pct(count, n_other_legs):.1f}%)"
            for name, count in n_by_outcome.items()
        )
        print(
            "[braunschweig.secondary_chainsolvers] other subtype labelling "
            "(bounded other legs only; unbounded go to fallback untagged): "
            f"{n_other_legs:,} bounded other legs -> {shares}; "
            f"distance-layer fallback to aggregate 'other' "
            f"{n_dist_fb:,}/{n_other_legs:,} "
            f"({_rate_pct(n_dist_fb, n_other_legs):.1f}%)"
        )
    if escort_location_decider is not None:
        n_by_type = {name: subtype_stats[name] for name in ESCORT_LOCATION_ACTIVITIES}
        n_escort_legs = sum(n_by_type.values())
        n_dist_fb = subtype_stats["escort_distance_layer_fallback"]
        shares = ", ".join(
            f"{name} {count:,} ({_rate_pct(count, n_escort_legs):.1f}%)"
            for name, count in n_by_type.items()
        )
        print(
            "[braunschweig.secondary_chainsolvers] escort location draw "
            "(bounded escort legs only; unbounded go to fallback untyped): "
            f"{n_escort_legs:,} bounded escort legs -> {shares}; "
            f"distance-layer fallback to aggregate 'other' "
            f"{n_dist_fb:,}/{n_escort_legs:,} "
            f"({_rate_pct(n_dist_fb, n_escort_legs):.1f}%)"
        )
        if "escort_type_distance_layer_fallback" in subtype_stats:
            n_type_fb = subtype_stats["escort_type_distance_layer_fallback"]
            print(
                "[braunschweig.secondary_chainsolvers] escort distance-by-type: "
                f"per-type layer used {n_escort_legs - n_type_fb - n_dist_fb:,}"
                f"/{n_escort_legs:,} escort legs "
                f"({_rate_pct(n_escort_legs - n_type_fb - n_dist_fb, n_escort_legs):.1f}%), "
                f"fallback to aggregate 'escort' {n_type_fb:,} "
                f"({_rate_pct(n_type_fb, n_escort_legs):.1f}%)"
            )
            # 0.2 (20%) is a HEURISTIC escalation threshold (final-review finding,
            # not a scientifically derived bound): above it the per-type layers are
            # effectively not doing their job (CLAUDE.md fallback-transparency rule
            # 2 -- a high fallback rate is a failure signal, not a tolerated cost).
            if n_escort_legs and n_type_fb > 0.2 * n_escort_legs:
                print(
                    "[braunschweig.secondary_chainsolvers] WARNING: per-type "
                    "distance-layer fallback above 20% -- the per-type layers are "
                    "effectively not working (check escort_distance_factor_activities "
                    "vs the draw vocabulary)."
                )
    print(
        f"[braunschweig.secondary_chainsolvers] fallback strategy: "
        f"{fallback_strategy}"
    )

    fallback_rows, fallback_conv = _run_fallback(unbounded_idx)

    if len(plans_df) == 0:
        print(
            "[braunschweig.secondary_chainsolvers] no bounded legs to place; "
            "returning fallback-only result."
        )
        # No bounded problems -> carla placed nothing; every problem went to the
        # fallback. Report the (100% fallback) split so the rate stays observable
        # on this early-return path too. Counting only.
        print(
            _fallback_accounting_summary(
                n_total_problems=len(problems),
                n_unbounded=len(unbounded_idx),
                n_failed_bounded=0,
            ),
            flush=True,
        )
        # Task 6, issue #127: no bounded legs at all -> no "leisure_excursion"
        # legs either; still print the (0/0) line so the rate stays
        # observable on this early-return path too (no silent gap).
        if leisure_subtype_decider is not None:
            print(_excursion_boundary_clip_summary(0, 0), flush=True)
        df_loc = gpd.GeoDataFrame(
            pd.DataFrame.from_records(
                fallback_rows,
                columns=["person_id", "activity_index", "location_id", "geometry"],
            ),
            geometry="geometry", crs=crs,
        )
        df_conv = pd.DataFrame.from_records(
            fallback_conv, columns=["valid", "size"]
        )
        return df_loc, df_conv

    print(
        f"[braunschweig.secondary_chainsolvers] {len(plans_df):,} plan rows; "
        f"building chainsolvers context..."
    )
    sec_enabled = context.config("secondary_building_potentials")
    if sec_enabled:
        # Build the scorer spec. "_cs_parameters" carries the optional carla
        # selection parameters dict and is popped in _solve_person_shard before
        # forwarding the remaining keys to build_scorer(**...).
        selection = str(context.config("secondary_scorer_selection") or "top_n")
        # Pass parameters= to cs.setup ONLY for "mnl"; for all other values
        # (including the default "top_n") pass nothing so carla uses its native
        # defaults -- the only way to stay byte-identical. CarlaConfig has no
        # temperature field; secondary_scorer_mnl_temperature is reserved for
        # Task 8 eval and is NOT wired into cs.setup here.
        cs_parameters = (
            {
                "selection_strategy_complex_case": "mnl",
                "selection_strategy_two_leg_case": "mnl",
            }
            if selection == "mnl" else None
        )
        scorer_spec = {
            "enabled": True,
            "mode": context.config("secondary_scorer_mode"),
            "pot_weight": context.config("secondary_scorer_pot_weight"),
            "dist_dev_weight": context.config("secondary_scorer_dist_dev_weight"),
            "attr_transform": str(context.config("secondary_scorer_attr_transform") or "linear"),
            "_cs_parameters": cs_parameters,
        }
    else:
        scorer_spec = None
    # NOTE: the RDA/unbounded fallback intentionally uses the LEGACY frame
    # (df_secondary_legacy); only the primary chainsolver solve uses the
    # REPLACE candidates. The assembled set (gpkg sec_b_* + legacy other +
    # external centroids + residential visit rows) comes from the dedicated
    # secondary_candidates stage -- the SAME stage the facilities writer
    # consumes, so every realisable location id is guaranteed to exist as a
    # MATSim facility (2026-07-11 LinkAssignment fix).
    if sec_enabled:
        df_secondary = context.stage(
            "braunschweig.synthesis.locations.secondary_candidates")
    # Tier 2 / Task 4 require the building-potential candidate set: the subtype
    # legs (shop_daily/non_daily; leisure_local/visit/activity/excursion;
    # other_errand_short/long, other_escort) can only be placed at buildings
    # carrying those subtype activities, which exist only on the
    # with_potentials path. A subtype split without building potentials would
    # leave carla with no candidates for the subtype activities -> fail fast
    # (no silent fallback).
    if shop_daily_split and not sec_enabled:
        raise RuntimeError(
            "[braunschweig.secondary_chainsolvers] secondary_shop_daily_split "
            "requires secondary_building_potentials to be ON (the daily / "
            "non-daily shop placement needs the retail_daily / retail_non_daily "
            "building candidates). Enable secondary_building_potentials or "
            "disable secondary_shop_daily_split."
        )
    if leisure_subtype_split and not sec_enabled:
        raise RuntimeError(
            "[braunschweig.secondary_chainsolvers] secondary_leisure_subtype_split "
            "requires secondary_building_potentials to be ON (the leisure subtype "
            "placement needs the pot_leisure building candidates). Enable "
            "secondary_building_potentials or disable secondary_leisure_subtype_split."
        )
    if other_subtype_split and not sec_enabled:
        raise RuntimeError(
            "[braunschweig.secondary_chainsolvers] secondary_other_subtype_split "
            "requires secondary_building_potentials to be ON (the other "
            "errand/escort subtype placement needs the pot_other building "
            "candidates). Enable secondary_building_potentials or disable "
            "secondary_other_subtype_split."
        )
    # Task 5, issue #127: residential pot_visit placement for leisure_visit
    # legs. Fail-fast (no silent fallback to pot_leisure) on every
    # precondition -- a broken wiring here must never quietly degrade to the
    # Task-4 shared-potential behaviour.
    if leisure_visit_building_potential and not leisure_subtype_split:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] leisure_visit_building_potential "
            "requires secondary_leisure_subtype_split to be ON (there is no "
            "'leisure_visit' activity without the leisure subtype split). Enable "
            "secondary_leisure_subtype_split or disable "
            "leisure_visit_building_potential."
        )
    if leisure_visit_building_potential and not sec_enabled:
        raise ValueError(
            "[braunschweig.secondary_chainsolvers] leisure_visit_building_potential "
            "requires secondary_building_potentials to be ON (the residential visit "
            "placement needs the pot_visit candidate column on the building-potential "
            "candidate frame). Enable secondary_building_potentials or disable "
            "leisure_visit_building_potential."
        )
    if escort_purpose_on and not sec_enabled:
        raise RuntimeError(
            "[braunschweig.secondary_chainsolvers] escort_purpose requires "
            "secondary_building_potentials to be ON (the escort placement needs "
            "the education/residential/aggregate candidate potentials). Enable "
            "secondary_building_potentials or disable escort_purpose."
        )
    # The residential visit candidates themselves are appended by the
    # secondary_candidates stage (df_secondary above already carries them when
    # the flag is ON); _build_locations_df below still needs the flag for the
    # offers_visit/pot_visit schema wiring and its own fail-fast check.

    # Task 6, issue #127: excursion boundary-clip transparency. Reads the
    # already-sampled desired distances (plans_df["distance_meters"]) and the
    # already-finalised candidate set (df_secondary) -- this block places
    # nothing and draws no random number; see the module-level comment above
    # _excursion_boundary_clip_summary.
    if leisure_subtype_decider is not None:
        desired_m, anchors_xy = _excursion_desired_distances_and_anchors_m(plans_df, problems)
        if desired_m.size > 0:
            excursion_candidates = df_secondary.loc[df_secondary["pot_leisure"] > 0.0]
            if len(excursion_candidates) == 0:
                raise RuntimeError(
                    "[braunschweig.secondary_chainsolvers] leisure_excursion "
                    "boundary-clip check found zero candidates with "
                    "pot_leisure > 0, but leisure_subtype_split sampled "
                    "bounded 'leisure_excursion' legs -- the building-"
                    "potentials wiring is broken (this is not an expected "
                    "empty-candidate run)."
                )
            candidate_xy = np.column_stack((
                excursion_candidates.geometry.x.to_numpy(),
                excursion_candidates.geometry.y.to_numpy(),
            ))
            ceiling_m = _candidate_reach_ceiling_m(anchors_xy, candidate_xy)
            _, n_clipped, n_total = boundary_clip_share(desired_m, ceiling_m)
        else:
            n_clipped, n_total = 0, 0
        print(_excursion_boundary_clip_summary(n_clipped, n_total))

    locations_df = _build_locations_df(
        df_secondary, with_potentials=sec_enabled,
        shop_daily_split=shop_daily_split,
        leisure_subtype_split=leisure_subtype_split,
        other_subtype_split=other_subtype_split,
        leisure_visit_building_potential=leisure_visit_building_potential,
        escort_purpose=escort_purpose_on,
    )

    solver_name = context.config("braunschweig.chainsolvers.solver") or "carla"
    # One base seed drawn from the deterministic RandomState. Drawing exactly
    # once here (as the legacy single cs.setup did) preserves the RNG stream for
    # the downstream fallback, so the serial path stays byte-identical.
    base_seed = int(random.randint(0, 2**31 - 1))

    # Drop helper columns chainsolvers does not expect.
    plans_for_cs = plans_df.drop(columns=["_leg_index", "_problem_idx"])
    unique_persons = plans_for_cs["unique_person_id"].drop_duplicates().to_list()
    n_total = len(unique_persons)

    parallel_enabled = bool(context.config("braunschweig.chainsolvers.parallel"))
    configured_procs = context.config("braunschweig.chainsolvers.processes")
    # chainsolvers.processes (when set) overrides the synpp `processes` count; either
    # honours the auto sentinel (0/null/"auto" -> cores - reserve) so the chain solver
    # scales with the box. A key left unset (None) defers to `processes`. An explicit
    # positive integer is used verbatim (resolve_workers is the identity there), so
    # existing configs stay byte-identical.
    from braunschweig.parallelism import resolve_workers
    _requested_procs = configured_procs if configured_procs is not None else context.config("processes")
    n_workers = resolve_workers(_requested_procs)
    n_workers = max(1, min(n_workers, n_total)) if n_total else 1
    run_parallel = parallel_enabled and n_workers > 1 and n_total > 0

    print(
        f"[braunschweig.secondary_chainsolvers] running cs.solve() "
        f"({'parallel, %d workers' % n_workers if run_parallel else 'serial'}; "
        f"{n_total:,} persons)...",
        flush=True,
    )
    t0 = time.time()

    if run_parallel:
        result_df, failed_problem_idx = _solve_chains_parallel(
            plans_for_cs, unique_persons, locations_df, solver_name,
            base_seed, n_workers, t0, scorer_spec,
        )
    else:
        # Serial path: a single shard over all persons seeded with base_seed, so
        # the chunked solve loop is byte-identical to the pre-parallel behaviour.
        _init_chain_worker(locations_df, solver_name, scorer_spec)
        _shard_idx, result_df, failed_problem_idx = _solve_person_shard(
            (0, unique_persons, plans_for_cs, base_seed)
        )
        if result_df is None:
            result_df = _empty_chain_result_df()

    n_failed = len(failed_problem_idx)

    # Route failed bounded problems through the configured fallback.
    extra_rows, extra_conv = _run_fallback(failed_problem_idx)
    fallback_rows = list(fallback_rows) + extra_rows
    fallback_conv = list(fallback_conv) + extra_conv
    print(
        f"[braunschweig.secondary_chainsolvers] cs.solve() finished in "
        f"{time.time() - t0:.1f}s; "
        f"persons solved={n_total - n_failed:,}, failed={n_failed:,}",
        flush=True,
    )

    # Consolidated PRIMARY (carla) vs FALLBACK accounting over ALL problems
    # (bounded + unbounded), so the carla-vs-fallback usage is observable as an
    # explicit rate. n_failed counts the bounded problems carla raised on;
    # len(unbounded_idx) the chains carla could never accept -- both go to the
    # fallback. A high fallback share is flagged with a WARNING prefix because it
    # means carla is effectively not working. Counting only; no placed result,
    # selection, or RNG draw is affected.
    print(
        _fallback_accounting_summary(
            n_total_problems=len(problems),
            n_unbounded=len(unbounded_idx),
            n_failed_bounded=n_failed,
        ),
        flush=True,
    )

    df_locations, df_convergence = _extract_locations(
        result_df, problem_meta, df_secondary, crs,
    )

    # Append fallback rows for unbounded chains.
    if fallback_rows:
        df_fb = gpd.GeoDataFrame(
            pd.DataFrame.from_records(
                fallback_rows,
                columns=["person_id", "activity_index", "location_id", "geometry"],
            ),
            geometry="geometry", crs=crs,
        )
        df_locations = pd.concat([df_locations, df_fb], ignore_index=True)
    if fallback_conv:
        df_convergence = pd.concat(
            [df_convergence, pd.DataFrame.from_records(fallback_conv, columns=["valid", "size"])],
            ignore_index=True,
        )

    # Anchored escort activities are fixed boundaries (escort_linked), so the
    # problem splitter never places them; append their pre-anchored rows
    # (issue #201 Phase 2). They reference PRIMARY education facility ids, which
    # the facilities coverage check accepts via extra_valid_ids.
    if linked_location_rows is not None and len(linked_location_rows):
        df_linked = gpd.GeoDataFrame(linked_location_rows, geometry="geometry", crs=crs)
        df_locations = pd.concat([df_locations, df_linked], ignore_index=True)

    if len(df_convergence):
        print(
            "[braunschweig.secondary_chainsolvers] success rate: "
            f"{df_convergence['valid'].mean():.4f} "
            f"({df_convergence['valid'].sum()}/{len(df_convergence)} "
            f"problems fully placed)"
        )
    return df_locations, df_convergence
