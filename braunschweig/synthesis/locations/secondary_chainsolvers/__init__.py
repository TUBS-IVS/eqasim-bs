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
import hashlib
import inspect
import multiprocessing as mp
import time
from collections import defaultdict
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
    plans,
    srv_candidates,
    srv_location_types,
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


# ---------------------------------------------------------------------------
# Pure helpers (unit-testable without a synpp context)
# ---------------------------------------------------------------------------



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
    plans,
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
    # the eqasim "escort" purpose the same implicit way. Issue #262: the drawn
    # SrV location categories (SRV_LEISURE_CATEGORIES / SRV_OTHER_CATEGORIES)
    # map back to "leisure" / "other" by exactly the same mechanism -- they are
    # chainsolver-internal placement activities that never reach the output
    # schema. The two aggregate-placement categories ("leisure_misc",
    # "other_misc") never appear as a to_act_type (SRV_AGGREGATE_PLACEMENT
    # resolves them to the plain purpose in the leg loop), so their membership
    # here is inert -- listing the full vocabulary keeps this set in lockstep
    # with the category constants instead of encoding that indirection twice.
    secondary_acts = (
        set(SECONDARY_PURPOSES)
        | set(SHOP_SUBTYPE_ACTIVITIES)
        | set(LEISURE_SUBTYPE_ACTIVITIES)
        | set(OTHER_SUBTYPE_ACTIVITIES)
        | set(ESCORT_LOCATION_ACTIVITIES)
        | set(SRV_LEISURE_CATEGORIES)
        | set(SRV_OTHER_CATEGORIES)
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
                                                problems: List[Dict[str, Any]],
                                                row_mask=None
                                                ) -> Tuple[np.ndarray, np.ndarray]:
    """Desired distances (metres) and anchors for the selected excursion legs.

    ``plans_df`` must still carry ``_problem_idx`` (i.e. be the frame returned
    by ``_build_plans_df``, before the caller drops the helper columns for
    ``cs.solve()``). Every BOUNDED problem has both ``origin`` and
    ``destination`` fixed -- ``_build_plans_df`` routes any problem missing
    either anchor to ``unbounded_idx`` before this frame is built -- so
    ``problem["origin"]`` is always available here. The fixed origin (the
    person's actual anchor for that chain, e.g. home) is used as the leg's
    reference point for the candidate-reach ceiling: it is always available,
    unlike an intermediate, still-unresolved secondary location.

    ``row_mask`` selects the rows to measure. ``None`` (default) keeps the legacy
    selection -- ``to_act_type == "leisure_excursion"``, i.e. the placement
    activity, which IS the MiD subtype whenever ``secondary_srv_location_types``
    is OFF. With that flag ON the placement activity is the drawn SrV category
    instead, so the caller passes a boolean mask built from
    ``DISTANCE_LABEL_COLUMN`` (optionally intersected with a placement category);
    the coordinate/anchor logic below is shared by both paths rather than
    duplicated (issue #262).

    Returns
    -------
    tuple[np.ndarray, np.ndarray]
        ``(desired_m, anchors_xy)``, parallel arrays of length
        n_selected_legs; ``anchors_xy`` has shape ``(n, 2)``. Both are empty
        when the selection matches no row (e.g. the flag is OFF and no bounded
        leg happened to draw that group).

    Raises
    ------
    ValueError
        If ``row_mask`` length does not match ``plans_df``.
    """
    if plans_df.empty or "to_act_type" not in plans_df.columns:
        return np.array([], dtype=float), np.empty((0, 2), dtype=float)
    if row_mask is None:
        mask = (plans_df["to_act_type"] == "leisure_excursion").to_numpy()
    else:
        mask = np.asarray(row_mask, dtype=bool)
        if mask.shape != (len(plans_df),):
            raise ValueError(
                "[braunschweig.secondary_chainsolvers] row_mask must have one entry "
                f"per plans_df row: got {mask.shape}, expected ({len(plans_df)},)."
            )
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
                                     warning_share: float = DEFAULT_EXCURSION_CLIP_WARNING_SHARE,
                                     placement_category: str = None) -> str:
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
        placement_category: with ``secondary_srv_location_types`` ON the same
            measurement is resolved per DRAWN placement category (each category
            has its own candidate pool, hence its own reach ceiling); naming it
            here tags the line ``[placement=<category>]``. ``None`` (default)
            produces the legacy, placement-agnostic wording unchanged.
    """
    scope = f" [placement={placement_category}]" if placement_category else ""
    if n_total == 0:
        return (
            "[braunschweig.secondary_chainsolvers] leisure_excursion "
            f"boundary-clip{scope}: 0 bounded 'leisure_excursion' legs this run "
            "(nothing to measure)."
        )
    share = n_clipped / n_total
    prefix = "WARNING: " if share >= warning_share else ""
    return (
        f"[braunschweig.secondary_chainsolvers] {prefix}leisure_excursion "
        f"boundary-clip{scope}: {n_clipped:,}/{n_total:,} ({share * 100.0:.1f}%) "
        "bounded excursion legs sample a desired distance beyond the "
        "farthest available candidate and clip to the region edge."
    )


def _srv_placement_potential_column(placement_activity: str) -> str:
    """Candidate potential column backing a drawn SrV placement activity.

    The drawn category is the chainsolver activity, so its candidate pool is
    exactly the rows the emission in :func:`_build_locations_df` offers it to:
    a category activity maps to its own ``pot_<category>`` (``pot_visit`` for
    ``leisure_visit``, see :func:`srv_category_potential_column`), while the two
    aggregate-placement categories resolve to the plain purpose and therefore to
    ``pot_leisure`` / ``pot_other`` via ``_ACTIVITY_POTENTIAL_COLUMN``. One
    mapping, used by both the emission and this diagnostic.
    """
    if placement_activity in SRV_PLACEMENT_CATEGORIES:
        return srv_category_potential_column(placement_activity)
    return _ACTIVITY_POTENTIAL_COLUMN[placement_activity]


def _srv_excursion_boundary_clip_lines(plans_df: pd.DataFrame,
                                       problems: List[Dict[str, Any]],
                                       df_secondary,
                                       warning_share: float = DEFAULT_EXCURSION_CLIP_WARNING_SHARE
                                       ) -> List[str]:
    """Excursion boundary-clip lines under SrV placement, PER DRAWN CATEGORY.

    Restores the Task-6 diagnostic (issue #127) for the
    ``secondary_srv_location_types`` ON path, where it had gone structurally
    inert: the diagnostic used to select its legs by placement activity
    ``== "leisure_excursion"``, but with SrV placement the activity is the drawn
    category and the MiD subtype survives only as ``DISTANCE_LABEL_COLUMN``.
    Legs are therefore selected by that label, and then grouped by the drawn
    placement category -- each category is placed on its OWN candidate pool
    (landuse points for ``leisure_outdoor``, residential rows for
    ``leisure_visit``, the aggregate buildings for ``leisure_misc``, plus the
    external Gemeinde centroids, which carry every category potential since the
    escape step), so each has its own reach ceiling and must be measured
    separately. Measurement only: reads the already-sampled desired distances
    and the already-assembled candidate set, places nothing, draws no random
    number.

    Returns one line per drawn category (alphabetically, for a deterministic
    log) plus one aggregate total line, or a single "nothing to measure" line
    when no bounded excursion leg exists.

    Raises
    ------
    RuntimeError
        If a drawn placement category has zero positive-potential candidates --
        broken wiring rather than thin data, mirroring the legacy
        ``pot_leisure`` fail-fast.
    """
    if plans_df.empty or DISTANCE_LABEL_COLUMN not in plans_df.columns:
        return [_excursion_boundary_clip_summary(0, 0)]
    excursion_mask = (plans_df[DISTANCE_LABEL_COLUMN] == "leisure_excursion").to_numpy()
    if not excursion_mask.any():
        return [_excursion_boundary_clip_summary(0, 0)]

    lines = []
    n_clipped_all = 0
    n_total_all = 0
    placement_acts = plans_df.loc[excursion_mask, "to_act_type"]
    for category in sorted(set(placement_acts)):
        category_mask = excursion_mask & (plans_df["to_act_type"] == category).to_numpy()
        desired_m, anchors_xy = _excursion_desired_distances_and_anchors_m(
            plans_df, problems, row_mask=category_mask)
        if desired_m.size == 0:
            continue
        potential_column = _srv_placement_potential_column(category)
        if potential_column not in df_secondary.columns:
            raise RuntimeError(
                "[braunschweig.secondary_chainsolvers] leisure_excursion boundary-clip: "
                f"placement category {category!r} needs candidate column "
                f"'{potential_column}', which the candidate set does not carry -- the "
                "SrV candidate wiring is broken."
            )
        category_candidates = df_secondary.loc[df_secondary[potential_column] > 0.0]
        if len(category_candidates) == 0:
            raise RuntimeError(
                "[braunschweig.secondary_chainsolvers] leisure_excursion boundary-clip "
                f"found zero candidates with {potential_column} > 0, but bounded "
                f"'leisure_excursion' legs were placed on {category!r} -- the candidate "
                "wiring is broken (this is not an expected empty-candidate run)."
            )
        candidate_xy = np.column_stack((
            category_candidates.geometry.x.to_numpy(),
            category_candidates.geometry.y.to_numpy(),
        ))
        ceiling_m = _candidate_reach_ceiling_m(anchors_xy, candidate_xy)
        _, n_clipped, n_total = boundary_clip_share(desired_m, ceiling_m)
        n_clipped_all += n_clipped
        n_total_all += n_total
        lines.append(_excursion_boundary_clip_summary(
            n_clipped, n_total, warning_share=warning_share, placement_category=category))
    lines.append(_excursion_boundary_clip_summary(
        n_clipped_all, n_total_all, warning_share=warning_share))
    return lines




def _srv_location_draw_summary_lines(subtype_stats: Dict[str, int]) -> List[str]:
    """One draw-rate line per SrV-covered purpose plus a pooled-total line.

    Pure (no I/O, no randomness) so the exact wording is testable. Each purpose's
    line reports how many bounded legs drew each location category AND that
    purpose's OWN marginal-fallback rate -- draws resolved from the purpose's
    marginal distribution because the pinned table has no ``(mode, distance
    band)`` cell for the leg (CLAUDE.md fallback transparency: the rate must
    always be observable, not only when it is non-zero). The
    ``SRV_LOCATION_MARGINAL_FALLBACK_WARN_SHARE`` escalation is evaluated PER
    PURPOSE, because a pooled rate lets a badly covered purpose hide behind a
    well covered one (the purposes differ in leg volume by several times). The
    trailing pooled line is informational only and never warns.
    """
    lines = []
    n_all = 0
    n_marginal_all = 0
    for purpose, categories in (
        ("leisure", SRV_LEISURE_CATEGORIES), ("other", SRV_OTHER_CATEGORIES),
    ):
        counts = {
            name: subtype_stats[SRV_LOCATION_STAT_PREFIX + name] for name in categories
        }
        n_legs = sum(counts.values())
        n_all += n_legs
        shares = ", ".join(
            f"{name} {count:,} ({_rate_pct(count, n_legs):.1f}%)"
            for name, count in counts.items()
        )
        n_marginal = subtype_stats[srv_location_marginal_fallback_stat(purpose)]
        n_marginal_all += n_marginal
        share = (n_marginal / n_legs) if n_legs else 0.0
        prefix = "WARNING: " if share >= SRV_LOCATION_MARGINAL_FALLBACK_WARN_SHARE else ""
        lines.append(
            f"[braunschweig.secondary_chainsolvers] {prefix}srv location draw ({purpose}): "
            f"{n_legs:,} bounded {purpose} legs -> {shares}; marginal fallback (no "
            f"(mode, band) cell in the pinned table) {n_marginal:,}/{n_legs:,} "
            f"({share * 100.0:.1f}%)"
        )
    lines.append(
        "[braunschweig.secondary_chainsolvers] srv location draw: marginal fallback "
        f"total {n_marginal_all:,}/{n_all:,} "
        f"({_rate_pct(n_marginal_all, n_all):.1f}%) -- see the per-purpose lines above "
        "for the rates the warning threshold is applied to"
    )
    return lines


# Pinned draw-vs-reference table produced by scripts/derive_srv_location_types.py
# (Task 1). Committed reference data -- regenerate there, never edit by hand.
# Also carries purpose="shop" rows (a validation-only contribution for a
# different feature, issue #242); srv_location_draw_summary EXCLUDES them --
# shop location choice is not decided by this decider.
DEFAULT_SRV_LOCATION_TYPE_SHARES_PATH = (
    "eqasim-data/data/braunschweig/srv/srv2023_secondary_type_shares.csv"
)

# HEURISTIC escalation threshold (percentage points, NOT a scientifically
# derived bound): the maximum tolerated |drawn_share - reference_share| for a
# category before the per-run draw-summary writer emits a WARN. Configurable
# via ``srv_location_share_warn_pp`` (declared in ``configure``).
DEFAULT_SRV_LOCATION_SHARE_WARN_PP = 5.0

# Column order of the srv_location_draw_summary.csv artifact and the
# DataFrame returned by srv_location_draw_summary(); kept as a module
# constant so the writer and the tests agree on the schema.
SRV_LOCATION_DRAW_SUMMARY_COLUMNS = (
    "purpose", "category", "drawn_share", "reference_share",
    "drawn_median_desired_km", "reference_median_euclid_km", "n_drawn",
)

# Honesty note (CLAUDE.md "No invented reference values" + issue #262 plan):
# this summary is a DRAW-COHERENCE check, not a validation of realised model
# output against SrV. Reused verbatim as the CSV header comment and quoted in
# the function docstring below so both surfaces state the same caveat.
SRV_LOCATION_DRAW_SUMMARY_HONESTY_NOTE = (
    "This table compares DRAWN desired-distance medians (the leg's sampled "
    "target distance, before candidate search) against the SrV "
    "euclidean-equivalent medians of srv2023_secondary_type_shares.csv. It is "
    "a draw-coherence check on the category<->distance decider, NOT a "
    "validation of REALISED (placed) distances: carla's candidate search can "
    "still deviate from the desired distance (top_n selection inertness, "
    "backlog Tier-0 item (a)), which is assessed separately in the A/B "
    "validation run. Never read this file as \"validated against SrV\"."
)


def srv_location_draw_summary(subtype_stats: Dict[str, int],
                               desired_by_category: Dict[str, List[float]],
                               shares_df: pd.DataFrame) -> pd.DataFrame:
    """Per-category drawn-vs-reference coherence table (issue #262, Task 9).

    IMPORTANT -- read before using this table: it compares DRAWN
    desired-distance medians against the SrV euclidean-equivalent medians. It
    is a draw-coherence check on the ``srv_location_decider``, NOT a
    validation of REALISED (placed) distances -- carla's candidate search can
    still deviate from the desired distance (top_n selection inertness,
    backlog Tier-0 item (a)); that is assessed in the A/B validation run, not
    here. See :data:`SRV_LOCATION_DRAW_SUMMARY_HONESTY_NOTE`.

    Parameters
    ----------
    subtype_stats:
        The ``subtype_stats`` dict returned by ``_build_plans_df`` (namespaced
        ``SRV_LOCATION_STAT_PREFIX + category`` draw counters). Missing keys
        are treated as zero draws (a category the decider never drew in this
        run, e.g. under a small/synthetic input).
    desired_by_category:
        The ``desired_by_category`` dict returned by ``_build_plans_df``:
        ``{category: [desired_km, ...]}``, keyed by the BARE category name.
        A category absent from this dict gets a NaN
        ``drawn_median_desired_km`` (no legs to take a median over).
    shares_df:
        The pinned ``srv2023_secondary_type_shares.csv`` frame (columns
        ``purpose``, ``category``, ``weight_share``, ``weighted_median_euclid_km``,
        among others -- see :func:`load_srv_location_type_shares`). Its
        ``purpose="shop"`` rows are VALIDATION-ONLY rows for a different
        feature (issue #242) and are excluded here: this decider only draws
        for ``SRV_LOCATION_PURPOSES`` (leisure, other).

    Returns
    -------
    pandas.DataFrame
        One row per ``(purpose, category)`` for every category in
        ``SRV_LEISURE_CATEGORIES`` / ``SRV_OTHER_CATEGORIES`` (the full pinned
        vocabulary for the two SrV-covered purposes), columns
        :data:`SRV_LOCATION_DRAW_SUMMARY_COLUMNS`. ``drawn_share`` is
        ``n_drawn`` over that PURPOSE's total drawn legs (sums to 1.0 per
        purpose when at least one leg was drawn); a category the decider
        never drew still gets its own row with ``n_drawn=0`` and
        ``drawn_share=0.0`` (never silently omitted). ``reference_share`` /
        ``reference_median_euclid_km`` are looked up from ``shares_df``; a
        category absent from the pinned reference (should not occur for the
        fixed vocabulary above, but not assumed) gets NaN there instead of a
        fabricated value.
    """
    shares_lookup = shares_df[shares_df["purpose"].isin(SRV_LOCATION_PURPOSES)].set_index(
        ["purpose", "category"]
    )

    rows = []
    for purpose, categories in (
        ("leisure", SRV_LEISURE_CATEGORIES), ("other", SRV_OTHER_CATEGORIES),
    ):
        counts = {
            category: int(subtype_stats.get(SRV_LOCATION_STAT_PREFIX + category, 0))
            for category in categories
        }
        n_legs = sum(counts.values())
        for category in categories:
            n_drawn = counts[category]
            drawn_share = (n_drawn / n_legs) if n_legs else float("nan")
            desired_km = desired_by_category.get(category, [])
            drawn_median_desired_km = float(np.median(desired_km)) if desired_km else float("nan")
            if (purpose, category) in shares_lookup.index:
                reference_row = shares_lookup.loc[(purpose, category)]
                reference_share = float(reference_row["weight_share"])
                reference_median_euclid_km = float(reference_row["weighted_median_euclid_km"])
            else:
                reference_share = float("nan")
                reference_median_euclid_km = float("nan")
            rows.append({
                "purpose": purpose,
                "category": category,
                "drawn_share": drawn_share,
                "reference_share": reference_share,
                "drawn_median_desired_km": drawn_median_desired_km,
                "reference_median_euclid_km": reference_median_euclid_km,
                "n_drawn": n_drawn,
            })
    return pd.DataFrame(rows, columns=list(SRV_LOCATION_DRAW_SUMMARY_COLUMNS))


def load_srv_location_type_shares(path: str) -> pd.DataFrame:
    """Load the pinned ``srv2023_secondary_type_shares.csv`` reference table.

    Thin wrapper around ``pd.read_csv(path, comment="#")`` (the file's header
    is a block of ``#``-prefixed provenance comments, see the file itself);
    kept as a named function so the load convention is documented once and
    both the stage writer and the tests share it.
    """
    return pd.read_csv(path, comment="#")





def _rate_pct(count, total) -> float:
    """Percentage of ``count`` over ``total``, or 0.0 when ``total`` is falsy
    (guards the ZeroDivisionError on an empty leg group, e.g. no bounded
    escort legs at all). Shared by every fallback-rate / per-group-share
    percentage the execute() summary print block below reports, so the
    guarded formula is defined once instead of being repeated inline at
    each call site."""
    return 100.0 * count / total if total else 0.0


# Filename of the per-run draw-summary artifact written by
# _write_srv_location_draw_summary (issue #262, Task 9). Kept as a module
# constant so the writer and any downstream reader agree on the name.
SRV_LOCATION_DRAW_SUMMARY_FILENAME = "srv_location_draw_summary.csv"


def _write_srv_location_draw_summary(context, subtype_stats: Dict[str, int],
                                      desired_by_category: Dict[str, List[float]]) -> None:
    """Write the per-run draw-summary artifact and WARN on large deviations
    (issue #262, Task 9).

    Loads the pinned ``srv_location_type_shares_path`` reference table, builds
    :func:`srv_location_draw_summary`, and writes it as
    ``SRV_LOCATION_DRAW_SUMMARY_FILENAME`` under the stage's synpp output
    directory (``context.path()``), prefixed with
    :data:`SRV_LOCATION_DRAW_SUMMARY_HONESTY_NOTE` as a ``#``-commented CSV
    header (mirrors the pinned-CSV convention used by
    ``scripts/derive_srv_location_types.py``: read back with
    ``pd.read_csv(path, comment="#")``). Emits one WARN line per category
    whose ``|drawn_share - reference_share|`` exceeds
    ``srv_location_share_warn_pp`` percentage points (a category with no
    pinned reference, i.e. NaN ``reference_share``, is skipped -- there is
    nothing to compare against, and that is not itself a draw failure).

    This is a stage-side effect (file I/O + logging), never called on the OFF
    path -- the caller in ``execute()`` gates it on
    ``srv_location_decider is not None``.
    """
    shares_path = context.config("srv_location_type_shares_path")
    shares_df = load_srv_location_type_shares(shares_path)
    summary_df = srv_location_draw_summary(subtype_stats, desired_by_category, shares_df)

    # Zero-leg purpose (review finding, Minor): a purpose with reference rows
    # but NO drawn legs at all is near-100% non-coverage and must be loud, not
    # silent -- the per-category loop below would otherwise say nothing about
    # it (drawn_share is 0.0 for every one of its categories, which reads like
    # "no deviation" unless the total is checked separately).
    for purpose in SRV_LOCATION_PURPOSES:
        n_purpose_drawn = int(summary_df.loc[summary_df["purpose"] == purpose, "n_drawn"].sum())
        if n_purpose_drawn == 0:
            print(
                "[braunschweig.secondary_chainsolvers] WARNING: srv location draw "
                f"summary: 0 drawn legs for purpose {purpose!r} while "
                "secondary_srv_location_types is ON -- verify this run actually "
                f"produced bounded {purpose!r} legs (an entirely unbounded/"
                "fallback-only run would explain this, but a bounded run with "
                "zero draws for a whole purpose is a wiring bug, not noise)."
            )

    warn_pp = float(context.config("srv_location_share_warn_pp"))
    for row in summary_df.itertuples(index=False):
        if pd.isna(row.reference_share):
            # Fallback-transparency rule (CLAUDE.md): a category from the fixed
            # code vocabulary (SRV_LEISURE_CATEGORIES / SRV_OTHER_CATEGORIES)
            # with NO row in the pinned reference is a vocabulary-drift signal
            # -- e.g. the CSV was regenerated with a renamed or dropped
            # category -- and must be surfaced loudly, never silently skipped.
            print(
                "[braunschweig.secondary_chainsolvers] WARNING: srv location draw "
                f"summary: {row.purpose}/{row.category} has NO matching row in "
                f"the pinned reference ({shares_path}) -- possible drift between "
                "the code vocabulary (SRV_LEISURE_CATEGORIES / SRV_OTHER_CATEGORIES) "
                "and the pinned CSV; reference_share/reference_median_euclid_km "
                "are NaN and this category cannot be compared."
            )
            continue
        deviation_pp = abs(row.drawn_share - row.reference_share) * 100.0
        if deviation_pp > warn_pp:
            print(
                "[braunschweig.secondary_chainsolvers] WARNING: srv location draw "
                f"summary: {row.purpose}/{row.category} drawn_share "
                f"{row.drawn_share * 100.0:.1f}% deviates from the pinned reference "
                f"share {row.reference_share * 100.0:.1f}% by {deviation_pp:.1f} "
                f"percentage points (> srv_location_share_warn_pp={warn_pp:.1f})."
            )

    output_path = "%s/%s" % (context.path(), SRV_LOCATION_DRAW_SUMMARY_FILENAME)
    with open(output_path, "w", encoding="utf-8", newline="") as handle:
        handle.write(
            "# " + SRV_LOCATION_DRAW_SUMMARY_HONESTY_NOTE.replace("\n", "\n# ") + "\n"
        )
        handle.write(f"# Reference: {shares_path}\n")
        handle.write(f"# srv_location_share_warn_pp={warn_pp}\n")
        summary_df.to_csv(handle, index=False)
    print(
        "[braunschweig.secondary_chainsolvers] wrote srv location draw summary "
        f"({len(summary_df)} category rows) to {output_path}"
    )


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

    locations_df = _build_locations_df(
        df_secondary, with_potentials=sec_enabled,
        shop_daily_split=shop_daily_split,
        leisure_subtype_split=leisure_subtype_split,
        other_subtype_split=other_subtype_split,
        leisure_visit_building_potential=leisure_visit_building_potential,
        escort_purpose=escort_purpose_on,
        srv_location_types=srv_location_types_on,
    )

    solver_name = context.config("braunschweig.chainsolvers.solver") or "carla"
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
