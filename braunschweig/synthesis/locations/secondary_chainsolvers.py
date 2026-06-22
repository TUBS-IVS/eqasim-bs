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

from synthesis.population.spatial.secondary.problems import (
    find_assignment_problems,
)


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
    """
    distributions = copy.deepcopy(distributions)
    for mode, mode_distributions in distributions.items():
        for distribution in mode_distributions["distributions"]:
            distribution["cdf"] = _resample_cdf(distribution["cdf"], factors[mode])
    return distributions


def _sample_leg_distance(distributions, mode, travel_time, purpose,
                         leisure_correction_factor, random):
    """Replicates ``CustomDistanceSampler.sample_distances`` for one leg."""
    mode_distribution = distributions[mode]
    bound_index = int(np.count_nonzero(travel_time > mode_distribution["bounds"]))
    mode_distribution = mode_distribution["distributions"][bound_index]
    distance = mode_distribution["values"][
        int(np.count_nonzero(random.random_sample() > mode_distribution["cdf"]))
    ]
    if purpose == "leisure":
        distance *= leisure_correction_factor
    return float(distance)


def _build_locations_df(df_secondary: pd.DataFrame) -> pd.DataFrame:
    """Convert eqasim secondary candidates → chainsolvers ``locations_df``."""
    activities = []
    for _, row in df_secondary[["offers_shop", "offers_leisure", "offers_other"]].iterrows():
        acts = []
        if bool(row["offers_shop"]):
            acts.append("shop")
        if bool(row["offers_leisure"]):
            acts.append("leisure")
        if bool(row["offers_other"]):
            acts.append("other")
        activities.append("; ".join(acts))

    # Vectorised coordinate access (GeoSeries.x/.y) instead of a per-geometry
    # Python lambda; produces the identical (n, 2) ordering as the candidate
    # set, so the resulting locations table is byte-identical.
    coords = np.column_stack((
        df_secondary.geometry.x.values,
        df_secondary.geometry.y.values,
    ))
    out = pd.DataFrame({
        "id": df_secondary["location_id"].astype(str).values,
        "x": coords[:, 0],
        "y": coords[:, 1],
        "activities": activities,
    })
    return out


# ---------------------------------------------------------------------------
# Plans-DF construction
# ---------------------------------------------------------------------------

# Eqasim purposes that count as "secondary" (variable). ``home``/``work``/
# ``education`` are fixed (anchors).
SECONDARY_PURPOSES = {"shop", "leisure", "other"}
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
                    random: np.random.RandomState) -> Tuple[pd.DataFrame, List[Dict[str, Any]], List[int]]:
    """Assemble the chainsolvers plans_df from BOUNDED problems only.

    Returns ``(plans_df, problem_meta, unbounded_indices)``. Unbounded
    problems (tail / head / floating chains) are excluded — carla needs
    both endpoints anchored. They are placed by ``_fallback_place``.
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
            distance_m = _sample_leg_distance(
                distributions, leg["mode"], leg["travel_time"],
                to_act_type if to_act_type in SECONDARY_PURPOSES else "other",
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
            col_act.append(to_act_type if to_act_type != "__fixed__" else "home")
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
        return pd.DataFrame.from_records([]), problem_meta, unbounded_idx

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
    return plans_df, problem_meta, unbounded_idx


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
    destinations = {}
    for purpose in SECONDARY_PURPOSES:
        mask = df_secondary[f"offers_{purpose}"].values
        destinations[purpose] = dict(
            identifiers=identifiers[mask],
            locations=coords[mask],
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
    distance_sampler = CustomDistanceSampler(
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
    """
    if not unbounded_idx:
        return [], []

    pool: Dict[str, pd.DataFrame] = {}
    for purpose in SECONDARY_PURPOSES:
        pool[purpose] = df_secondary[df_secondary[f"offers_{purpose}"]].reset_index(drop=True)

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
    is_secondary = pd.Series(to_act, dtype=object).isin(SECONDARY_PURPOSES).to_numpy()
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

# Worker-process globals: the (read-only) locations table and solver name are
# sent once per worker via the Pool initializer instead of being pickled with
# every task.
_WORKER_LOCATIONS_DF = None
_WORKER_SOLVER = None


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


def _init_chain_worker(locations_df, solver) -> None:
    global _WORKER_LOCATIONS_DF, _WORKER_SOLVER
    _WORKER_LOCATIONS_DF = locations_df
    _WORKER_SOLVER = solver


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
    ctx = cs.setup(
        locations_df=_WORKER_LOCATIONS_DF,
        solver=_WORKER_SOLVER or "carla",
        rng_seed=int(shard_seed),
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
                           base_seed, n_workers, t0):
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
        initargs=(locations_df, solver),
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
    df_primary, crs = _prepare_primary(context)

    distance_distributions = context.stage(
        "synthesis.population.spatial.secondary.distance_distributions"
    )
    df_secondary = context.stage("synthesis.locations.secondary")

    # Apply the same calibration tweaks as the legacy stage so the
    # input-side distributions are bit-comparable. ``_resample_distributions``
    # returns a resampled deep copy; the cached stage object (shared with the
    # legacy locations stage) is never mutated, so it can never be
    # double-resampled across consumers.
    distance_distributions = _resample_distributions(distance_distributions, dict(
        car=0.0, car_passenger=0.1, pt=0.5, bicycle=0.0, walk=-0.5,
    ))

    random = np.random.RandomState(context.config("random_seed"))
    leisure_corr = float(context.config("leisure_correction_factor"))
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
                rda_index_cache["index"] = _build_rda_candidate_index(df_secondary)
            return _rda_fallback_place(
                problems, problem_indices, rda_index_cache["index"],
                distance_distributions, leisure_corr, random, crs,
            )
        return _fallback_place(
            problems, problem_indices, df_secondary, random, crs,
        )

    print(
        "[braunschweig.secondary_chainsolvers] enumerating "
        "assignment problems..."
    )
    t0 = time.time()
    problems = list(find_assignment_problems(df_trips, df_primary))
    print(
        f"[braunschweig.secondary_chainsolvers] {len(problems):,} problems "
        f"in {time.time() - t0:.1f}s — building chainsolvers plans..."
    )

    plans_df, problem_meta, unbounded_idx = _build_plans_df(
        problems, distance_distributions, leisure_corr, random,
    )
    print(
        f"[braunschweig.secondary_chainsolvers] bounded problems: "
        f"{len(problem_meta):,}; unbounded (fallback): {len(unbounded_idx):,}"
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
    locations_df = _build_locations_df(df_secondary)

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
            base_seed, n_workers, t0,
        )
    else:
        # Serial path: a single shard over all persons seeded with base_seed, so
        # the chunked solve loop is byte-identical to the pre-parallel behaviour.
        _init_chain_worker(locations_df, solver_name)
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

    if len(df_convergence):
        print(
            "[braunschweig.secondary_chainsolvers] success rate: "
            f"{df_convergence['valid'].mean():.4f} "
            f"({df_convergence['valid'].sum()}/{len(df_convergence)} "
            f"problems fully placed)"
        )
    return df_locations, df_convergence
