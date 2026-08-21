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

Package layout (issue #266 split; formerly one 5300-line module): this
``__init__`` is the synpp stage (``configure``/``execute``/``validate``)
and re-exports every submodule name, so external imports of the stage
module path keep working unchanged. The submodules:

    activity_types     internal chainsolver activity-name vocabularies
    candidate_columns  candidate offer/potential column vocabularies
    candidates         candidate-set assembly, locations_df, scorer
    srv_candidates     SrV per-category columns, landuse + external escapes
    distance_sampling  desired-distance sampling from the MiD CDFs
    deciders           per-leg subtype / escort location deciders
    srv_location_types SrV-2023 category vocabulary, loader, decider
    escort             escort household-link rewrite + distance factors
    plans              plans-DF construction (the per-leg loop)
    fallback           rda / random fallback placement
    parallel_solving   person-sharded parallel solve
    results            solver output back to the eqasim schema
    reporting          fallback/clip/draw-rate transparency reporting

``validate()`` hashes all submodule sources into the synpp validation
token because ``get_stage_hash`` only covers this file -- a helper-only
change devalidates the cached stage output exactly like an edit here.
"""

from __future__ import annotations

import hashlib
import inspect
import time
from typing import Any, Dict, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd

from braunschweig.calibration.secondary_measurement import boundary_clip_share
from synthesis.population.spatial.secondary.problems import (
    find_assignment_problems,
)

# ---------------------------------------------------------------------------
# Package submodules (extracted stage sections). Every name is re-exported
# here so external consumers (secondary_candidates stage, calibration
# scripts, tests) keep importing from the stage module path unchanged.
# Each submodule MUST also be listed in _HELPER_MODULES below so its source
# participates in the synpp cache-validation token.
# ---------------------------------------------------------------------------

from . import (
    activity_types,
    candidate_columns,
    candidates,
    deciders,
    distance_sampling,
    escort,
    fallback,
    parallel_solving,
    plans,
    reporting,
    results,
    solver_defaults,
    srv_candidates,
    srv_location_types,
)
from .solver_defaults import DEFAULT_CHAIN_SOLVER  # noqa: F401  (re-export)
from .reporting import (  # noqa: F401  (re-exports)
    DEFAULT_EXCURSION_CLIP_WARNING_SHARE,
    DEFAULT_FALLBACK_WARNING_SHARE,
    DEFAULT_SRV_LOCATION_SHARE_WARN_PP,
    DEFAULT_SRV_LOCATION_TYPE_SHARES_PATH,
    SRV_LOCATION_DRAW_SUMMARY_COLUMNS,
    SRV_LOCATION_DRAW_SUMMARY_FILENAME,
    SRV_LOCATION_DRAW_SUMMARY_HONESTY_NOTE,
    _candidate_reach_ceiling_m,
    _excursion_boundary_clip_summary,
    _excursion_desired_distances_and_anchors_m,
    _fallback_accounting_summary,
    _rate_pct,
    _srv_excursion_boundary_clip_lines,
    _srv_location_draw_summary_lines,
    _srv_placement_potential_column,
    _write_srv_location_draw_summary,
    load_srv_location_type_shares,
    srv_location_draw_summary,
)
from .parallel_solving import (  # noqa: F401  (re-exports)
    _CHAIN_CHUNK_SIZE,
    _CHAIN_RESULT_COLUMNS,
    _derive_shard_seed,
    _empty_chain_result_df,
    _init_chain_worker,
    _make_person_shards,
    _person_row_ranges,
    _solve_chains_parallel,
    _solve_person_shard,
)
from .results import (  # noqa: F401  (re-exports)
    _extract_locations,
)
from .fallback import (  # noqa: F401  (re-exports)
    _build_rda_candidate_index,
    _fallback_place,
    _rda_fallback_place,
)
from .plans import (  # noqa: F401  (re-exports)
    DISTANCE_LABEL_COLUMN,
    FIXED_PURPOSES,
    PLANS_HELPER_COLUMNS,
    SECONDARY_PURPOSES,
    _build_plans_df,
    _plans_frame_for_solver,
    _problem_legs,
)
from .candidates import (  # noqa: F401  (re-exports)
    _build_locations_df,
    append_escort_candidates,
    append_residential_visit_candidates,
    build_scorer,
    build_secondary_candidates,
    external_candidates_cordon_warning,
)
from .srv_candidates import (  # noqa: F401  (re-exports)
    SRV_BUILDING_CATEGORY_BASE_POTENTIAL,
    append_external_category_escapes,
    append_landuse_candidates,
    append_location_category_columns,
    check_category_supply,
    check_visit_pool_supply,
    external_centroid_mask,
)
from .escort import (  # noqa: F401  (re-exports)
    _build_escort_distance_factor_map,
    rewrite_linked_escort_trips,
)
from .srv_location_types import (  # noqa: F401  (re-exports)
    DEFAULT_SRV_LOCATION_TYPE_PROBS_PATH,
    EXTERNAL_CATEGORY_ESCAPE_CATEGORIES,
    SRV_AGGREGATE_PLACEMENT,
    SRV_LEISURE_CATEGORIES,
    SRV_LOCATION_MARGINAL_FALLBACK_STAT_PREFIX,
    SRV_LOCATION_MARGINAL_FALLBACK_WARN_SHARE,
    SRV_LOCATION_PURPOSES,
    SRV_LOCATION_SEED_OFFSET,
    SRV_LOCATION_STAT_PREFIX,
    SRV_OTHER_CATEGORIES,
    SRV_PLACEMENT_CATEGORIES,
    _SRV_LOCATION_TYPE_CATEGORIES_BY_PURPOSE,
    _SRV_LOCATION_TYPE_PROB_TOLERANCE,
    _SRV_LOCATION_TYPE_REQUIRED_COLUMNS,
    _build_srv_location_decider,
    _validate_srv_location_type_prerequisites,
    load_srv_location_type_probs,
    srv_category_offer_column,
    srv_category_potential_column,
    srv_location_marginal_fallback_stat,
)
from .candidate_columns import (  # noqa: F401  (re-exports)
    ESCORT_EDU_OFFER_BY_TYPE,
    ESCORT_EDU_POTENTIAL_COLUMN,
    ESCORT_RESIDENTIAL_OFFER_COLUMN,
    VISIT_CANDIDATE_WARN_FACTOR,
    VISIT_OFFER_COLUMN,
    VISIT_POTENTIAL_COLUMN,
    _ACTIVITY_POTENTIAL_COLUMN,
)
from .deciders import (  # noqa: F401  (re-exports)
    ESCORT_LOCATION_SEED_OFFSET,
    LEISURE_SUBTYPE_SEED_OFFSET,
    OTHER_SUBTYPE_SEED_OFFSET,
    SHOP_SUBTYPE_SEED_OFFSET,
    _build_escort_location_decider,
    _build_leisure_subtype_decider,
    _build_other_subtype_decider,
    _build_shop_subtype_decider,
    _inverse_cdf_choice,
)
from .distance_sampling import (  # noqa: F401  (re-exports)
    _purpose_in_distributions,
    _rda_sample_distances,
    _resample_cdf,
    _resample_distributions,
    _sample_leg_distance,
    _synthesize_escort_type_layers,
)
from .activity_types import (  # noqa: F401  (re-exports)
    DEFAULT_ESCORT_DISTANCE_FACTORS,
    DEFAULT_ESCORT_LOCATIONS_ACTIVITIES,
    DEFAULT_ESCORT_LOCATIONS_WEIGHTS,
    ESCORT_CATEGORY_TO_ACTIVITY,
    ESCORT_LOCATION_ACTIVITIES,
    LEISURE_SUBTYPE_ACTIVITIES,
    OTHER_SUBTYPE_ACTIVITIES,
    SHOP_SUBTYPE_ACTIVITIES,
)

# Worker-process state (set by _init_chain_worker, read by _solve_person_shard)
# lives in parallel_solving as MUTABLE module globals. A static re-export would
# freeze the import-time value (None), so these are delegated dynamically via
# the module-level __getattr__ below (PEP 562) to always reflect the current
# worker state.
_WORKER_STATE_ATTRIBUTES = (
    "_WORKER_LOCATIONS_DF", "_WORKER_SOLVER", "_WORKER_SCORER_SPEC",
)


def __getattr__(name):
    if name in _WORKER_STATE_ATTRIBUTES:
        return getattr(parallel_solving, name)
    raise AttributeError(
        f"module {__name__!r} has no attribute {name!r}"
    )


# ---------------------------------------------------------------------------
# synpp cache validation
# ---------------------------------------------------------------------------

# Extracted helper submodules of this stage package. synpp's get_stage_hash
# only hashes THIS file's source (inspect.getsource of the stage module), so
# without the validate() hook below a change confined to a helper submodule
# would silently reuse the stale cached stage output on a partial rerun.
# Every submodule extracted from this package MUST be listed here.
_HELPER_MODULES: Tuple[Any, ...] = (
    activity_types,
    candidate_columns,
    candidates,
    deciders,
    distance_sampling,
    escort,
    fallback,
    parallel_solving,
    plans,
    reporting,
    results,
    solver_defaults,
    srv_candidates,
    srv_location_types,
)


def validate(context):
    """synpp validation token: md5 over the helper submodules' sources.

    synpp compares this token against the one stored with the cached stage
    output and devalidates the cache on mismatch, so helper-only source
    changes recompute the stage just like changes to this file itself.
    """
    digest = hashlib.md5()
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    return digest.hexdigest()


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

    # chainsolvers tuning.
    context.config("braunschweig.chainsolvers.solver", DEFAULT_CHAIN_SOLVER)
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

    # SrV-grounded location types (issue #262). When ON, every leisure / other
    # leg draws an OBSERVED SrV-2023-BS+RGB destination category conditioned on
    # (mode, euclidean distance band) AFTER its desired distance was sampled
    # (design A2), and that category -- not the MiD distance subtype -- decides
    # where the leg is placed. The eqasim output purpose stays "leisure" /
    # "other"; the category is internal to the chainsolver. Requires the
    # building-potential candidate set, both subtype splits and the residential
    # visit machinery (checked, fail-fast, in execute() via
    # _validate_srv_location_type_prerequisites). OFF (default) is
    # byte-identical.
    #
    # Both keys are declared UNCONDITIONALLY -- never inside an `if` block and
    # never as the right operand of a short-circuiting `or` (the issue #201 trap
    # documented at secondary_candidates.py: an undeclared key makes execute()'s
    # one-argument config() read raise synpp's PipelineError instead of reaching
    # the intended fail-fast guard).
    context.config("secondary_srv_location_types", False)
    context.config("srv_location_type_probs_path", DEFAULT_SRV_LOCATION_TYPE_PROBS_PATH)

    # Per-run draw-summary artifact (issue #262, Task 9): a draw-coherence
    # check comparing the drawn category shares / desired-distance medians
    # against the pinned SrV reference (srv2023_secondary_type_shares.csv).
    # Declared UNCONDITIONALLY (see the comment above on the two keys just
    # above) so a misconfigured OFF-flag run never hits synpp's
    # declared-keys-only PipelineError; the writer itself only runs when
    # secondary_srv_location_types is ON (execute() checks the flag before
    # loading either path).
    context.config("srv_location_type_shares_path", DEFAULT_SRV_LOCATION_TYPE_SHARES_PATH)
    context.config("srv_location_share_warn_pp", DEFAULT_SRV_LOCATION_SHARE_WARN_PP)


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












# ---------------------------------------------------------------------------
# synpp execute
# ---------------------------------------------------------------------------

def _apply_escort_household_link(context, df_trips):
    """Household escort link (issue #201 Phase 2), or a no-op when OFF.

    Before the assignment problems are enumerated, rewrite linked escorters'
    plan-level "escort" purposes to the fixed "escort_linked" purpose so they
    anchor at the child's education location instead of drawing a location
    type; unlinked escorters keep the plain "escort" purpose and go through
    the SrV-weighted draw. Returns ``(df_trips, linked_location_rows,
    escort_activity_anchors)`` -- the latter two are ``None`` when the flag
    is OFF (the byte-identical path).
    """
    if not bool(context.config("escort_household_link")):
        return df_trips, None, None
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
    return df_trips, linked_location_rows, escort_activity_anchors


def _build_scorer_spec(context, sec_enabled):
    """Scorer spec forwarded to the workers, or ``None`` when potentials are OFF.

    "_cs_parameters" carries the optional carla selection parameters dict and
    is popped in _solve_person_shard before forwarding the remaining keys to
    build_scorer(**...). Pass parameters= to cs.setup ONLY for "mnl"; for all
    other values (including the default "top_n") pass nothing so carla uses
    its native defaults -- the only way to stay byte-identical. CarlaConfig
    has no temperature field; secondary_scorer_mnl_temperature is reserved
    for Task 8 eval and is NOT wired into cs.setup here.
    """
    if not sec_enabled:
        return None
    selection = str(context.config("secondary_scorer_selection") or "top_n")
    cs_parameters = (
        {
            "selection_strategy_complex_case": "mnl",
            "selection_strategy_two_leg_case": "mnl",
        }
        if selection == "mnl" else None
    )
    return {
        "enabled": True,
        "mode": context.config("secondary_scorer_mode"),
        "pot_weight": context.config("secondary_scorer_pot_weight"),
        "dist_dev_weight": context.config("secondary_scorer_dist_dev_weight"),
        "attr_transform": str(context.config("secondary_scorer_attr_transform") or "linear"),
        "_cs_parameters": cs_parameters,
    }


def _validate_candidate_flag_prerequisites(*, sec_enabled, shop_daily_split,
                                           leisure_subtype_split, other_subtype_split,
                                           leisure_visit_building_potential,
                                           escort_purpose_on):
    """Fail fast on flag combinations the candidate set cannot serve.

    Tier 2 / Task 4 require the building-potential candidate set: the subtype
    legs (shop_daily/non_daily; leisure_local/visit/activity/excursion;
    other_errand_short/long, other_escort) can only be placed at buildings
    carrying those subtype activities, which exist only on the
    with_potentials path. A subtype split without building potentials would
    leave carla with no candidates for the subtype activities -> fail fast
    (no silent fallback). Task 5 (issue #127): the residential pot_visit
    placement checks every precondition too -- a broken wiring here must
    never quietly degrade to the Task-4 shared-potential behaviour.
    """
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


def _report_excursion_boundary_clip(plans_df, problems, df_secondary, *,
                                    leisure_subtype_decider, srv_location_decider):
    """Excursion boundary-clip transparency (Task 6, issue #127).

    Reads the already-sampled desired distances (plans_df["distance_meters"])
    and the already-finalised candidate set (df_secondary) -- this reporting
    places nothing and draws no random number; see the module-level comment
    above _excursion_boundary_clip_summary. A no-op when the leisure subtype
    split is OFF (no "leisure_excursion" legs exist then).
    """
    if leisure_subtype_decider is not None and srv_location_decider is not None:
        # Issue #262: with SrV placement ON, "leisure_excursion" is a DISTANCE
        # label only -- no plan row carries it as a placement activity. The
        # measurement is therefore driven by DISTANCE_LABEL_COLUMN and resolved
        # per DRAWN placement category, each against its own candidate pool (and
        # hence its own reach ceiling).
        for line in _srv_excursion_boundary_clip_lines(plans_df, problems, df_secondary):
            print(line)
    elif leisure_subtype_decider is not None:
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


def _log_subtype_draw_rates(context, subtype_stats, desired_by_category, *,
                            shop_subtype_decider, leisure_subtype_decider,
                            other_subtype_decider, escort_location_decider,
                            srv_location_decider):
    """Log per-decider draw shares and distance-layer fallback rates.

    One block per ACTIVE decider (an OFF decider is ``None`` and prints
    nothing, keeping the OFF path byte-identical). Counts cover BOUNDED legs
    only -- unbounded chains go to the fallback placer untagged. Logging and
    the per-run SrV draw-summary artifact only; no placed result, selection,
    or RNG draw is affected (CLAUDE.md fallback transparency).
    """
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
    if srv_location_decider is not None:
        # Drawn-category rates per purpose + the marginal-fallback rate
        # (CLAUDE.md fallback transparency). Counts cover BOUNDED leisure/other
        # legs only; unbounded chains go to the fallback placer untyped.
        for line in _srv_location_draw_summary_lines(subtype_stats):
            print(line)
        # Task 9: per-run draw-summary CSV artifact + share/median coherence
        # WARNs against the pinned SrV reference. A draw-coherence check only
        # -- see SRV_LOCATION_DRAW_SUMMARY_HONESTY_NOTE.
        _write_srv_location_draw_summary(context, subtype_stats, desired_by_category)


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

    df_trips, linked_location_rows, escort_activity_anchors = (
        _apply_escort_household_link(context, df_trips)
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

    # SrV location types (issue #262, A2). The prerequisite guard runs HERE --
    # earlier than its sibling flag guards further below -- so a misconfigured
    # run fails before the pinned probability CSV is loaded and before any leg is
    # placed; the guard itself is pure and mirrors the identical one in the
    # secondary_candidates stage.
    srv_location_types_on = bool(context.config("secondary_srv_location_types"))
    _validate_srv_location_type_prerequisites(
        srv_location_types=srv_location_types_on,
        secondary_building_potentials=bool(context.config("secondary_building_potentials")),
        leisure_subtype_split=leisure_subtype_split,
        other_subtype_split=other_subtype_split,
        leisure_visit_building_potential=leisure_visit_building_potential,
    )
    srv_location_decider = _build_srv_location_decider(context, random_seed)

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

    plans_df, problem_meta, unbounded_idx, subtype_stats, desired_by_category = _build_plans_df(
        problems, distance_distributions, leisure_corr, random,
        shop_subtype_decider=shop_subtype_decider,
        leisure_subtype_decider=leisure_subtype_decider,
        other_subtype_decider=other_subtype_decider,
        escort_location_decider=escort_location_decider,
        escort_distance_by_type=escort_distance_factor_map is not None,
        srv_location_decider=srv_location_decider,
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
    _log_subtype_draw_rates(
        context, subtype_stats, desired_by_category,
        shop_subtype_decider=shop_subtype_decider,
        leisure_subtype_decider=leisure_subtype_decider,
        other_subtype_decider=other_subtype_decider,
        escort_location_decider=escort_location_decider,
        srv_location_decider=srv_location_decider,
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
    scorer_spec = _build_scorer_spec(context, sec_enabled)
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
    # The residential visit candidates themselves are appended by the
    # secondary_candidates stage (df_secondary above already carries them when
    # the flag is ON); _build_locations_df below still needs the flag for the
    # offers_visit/pot_visit schema wiring and its own fail-fast check.
    _validate_candidate_flag_prerequisites(
        sec_enabled=sec_enabled,
        shop_daily_split=shop_daily_split,
        leisure_subtype_split=leisure_subtype_split,
        other_subtype_split=other_subtype_split,
        leisure_visit_building_potential=leisure_visit_building_potential,
        escort_purpose_on=escort_purpose_on,
    )

    _report_excursion_boundary_clip(
        plans_df, problems, df_secondary,
        leisure_subtype_decider=leisure_subtype_decider,
        srv_location_decider=srv_location_decider,
    )

    locations_df = _build_locations_df(
        df_secondary, with_potentials=sec_enabled,
        shop_daily_split=shop_daily_split,
        leisure_subtype_split=leisure_subtype_split,
        other_subtype_split=other_subtype_split,
        leisure_visit_building_potential=leisure_visit_building_potential,
        escort_purpose=escort_purpose_on,
        srv_location_types=srv_location_types_on,
    )

    solver_name = context.config("braunschweig.chainsolvers.solver") or DEFAULT_CHAIN_SOLVER
    # One base seed drawn from the deterministic RandomState. Drawing exactly
    # once here (as the legacy single cs.setup did) preserves the RNG stream for
    # the downstream fallback, so the serial path stays byte-identical.
    base_seed = int(random.randint(0, 2**31 - 1))

    # Drop helper columns chainsolvers does not expect (issue #262 adds the
    # ON-path-only DISTANCE_LABEL_COLUMN to that set).
    plans_for_cs = _plans_frame_for_solver(plans_df)
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
