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
    for mode, mode_distributions in distributions.items():
        for distribution in mode_distributions["distributions"]:
            distribution["cdf"] = _resample_cdf(distribution["cdf"], factors[mode])


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

    coords = np.vstack(
        df_secondary["geometry"].apply(lambda g: np.array([g.x, g.y])).values
    )
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
    rows: List[Dict[str, Any]] = []
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

            rows.append({
                "unique_person_id": f"{person_id}#{prob_idx}",
                "unique_leg_id": f"{person_id}#{prob_idx}#{li}",
                "to_act_type": (
                    to_act_type if to_act_type != "__fixed__" else "home"
                ),
                "distance_meters": distance_m,
                "from_x": from_x,
                "from_y": from_y,
                "to_x": to_x,
                "to_y": to_y,
                "_leg_index": li,
                "_problem_idx": prob_idx,
            })

    plans_df = pd.DataFrame.from_records(rows)
    return plans_df, problem_meta, unbounded_idx


def _rda_fallback_place(problems: List[Dict[str, Any]],
                        problem_indices: List[int],
                        df_secondary: pd.DataFrame,
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
    """
    if not problem_indices:
        return [], []

    from synthesis.population.spatial.secondary.rda import (
        AssignmentSolver, DiscretizationErrorObjective,
        GravityChainSolver, AngularTailSolver, GeneralRelaxationSolver,
    )
    from synthesis.population.spatial.secondary.components import (
        CustomDistanceSampler, CustomDiscretizationSolver,
        CandidateIndex, CustomFreeChainSolver,
    )

    identifiers = df_secondary["location_id"].values
    coords = np.vstack(
        df_secondary["geometry"].apply(lambda g: np.array([g.x, g.y])).values
    )
    destinations = {}
    for purpose in SECONDARY_PURPOSES:
        mask = df_secondary[f"offers_{purpose}"].values
        destinations[purpose] = dict(
            identifiers=identifiers[mask],
            locations=coords[mask],
        )

    candidate_index = CandidateIndex(destinations)
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
    """Convert chainsolvers result rows back to eqasim's output shape."""
    # Build location_id -> (x, y) lookup for fallback geometry construction.
    coord_lookup = {
        str(lid): (float(g.x), float(g.y))
        for lid, g in zip(df_secondary["location_id"].astype(str),
                          df_secondary["geometry"])
    }

    # Index meta by problem_idx for activity_index recovery.
    meta_by_idx = {m["problem_idx"]: m for m in problem_meta}

    out_rows = []
    convergence_rows = []

    # Group result rows by (person, problem) — leg order preserved by
    # the input order (chainsolvers preserves row order on output).
    # The solver's candidate identifier travels with each row, so we read it
    # positionally from the same zip rather than re-scanning result_df per row
    # (the latter is O(n^2) over the whole result frame and dominates runtime
    # at 25% scale). Positional read is also more robust than a leg_id lookup
    # if two rows ever share a unique_leg_id.
    if "to_act_identifier" in result_df.columns:
        identifiers = result_df["to_act_identifier"]
    else:
        identifiers = [None] * len(result_df)
    for (uid, leg_id, to_act, to_x, to_y, cand) in zip(
        result_df["unique_person_id"],
        result_df["unique_leg_id"],
        result_df["to_act_type"],
        result_df["to_x"],
        result_df["to_y"],
        identifiers,
    ):
        # uid = "{person_id}#{problem_idx}"
        # leg_id = "{person_id}#{problem_idx}#{leg_index}"
        try:
            person_str, prob_idx_str, leg_idx_str = leg_id.split("#")
        except ValueError:
            continue
        prob_idx = int(prob_idx_str)
        leg_idx = int(leg_idx_str)
        meta = meta_by_idx.get(prob_idx)
        if meta is None:
            continue

        # Only emit rows for *secondary* legs (skip the trailing leg
        # that lands on a fixed anchor).
        if to_act not in SECONDARY_PURPOSES:
            continue

        if pd.isna(to_x) or pd.isna(to_y):
            continue  # solver failed to place this leg — skip

        person_id = meta["person_id"]
        activity_index = meta["activity_index"] + leg_idx

        # Recover the canonical eqasim location_id from the candidate
        # identifier the solver wrote for this row (read positionally above).
        loc_id = cand if isinstance(cand, str) else None
        if loc_id is None or loc_id not in coord_lookup:
            # fallback: synthesise an id from coords (downstream only
            # cares that the geometry is set correctly).
            loc_id = f"cs_{prob_idx}_{leg_idx}"

        out_rows.append((
            person_id, activity_index, loc_id, geo.Point(float(to_x), float(to_y))
        ))

    # Convergence flag: per problem, valid if all secondary legs placed.
    placed_per_problem: Dict[int, int] = {}
    for r in out_rows:
        pid_aidx = (r[0], r[1])
        # Reverse engineer prob_idx from activity_index
        # (cheap: count placed entries per (person, activity_index))
        placed_per_problem.setdefault(pid_aidx, 0)
        placed_per_problem[pid_aidx] += 1

    for meta in problem_meta:
        n_expected = meta["n_secondary"]
        person_id = meta["person_id"]
        a0 = meta["activity_index"]
        n_placed = sum(
            1 for k in placed_per_problem
            if k[0] == person_id and a0 <= k[1] < a0 + n_expected
        )
        convergence_rows.append((n_placed == n_expected, n_expected))

    df_locations = pd.DataFrame.from_records(
        out_rows,
        columns=["person_id", "activity_index", "location_id", "geometry"],
    )
    df_locations = gpd.GeoDataFrame(df_locations, crs=crs)
    df_convergence = pd.DataFrame.from_records(
        convergence_rows, columns=["valid", "size"]
    )
    return df_locations, df_convergence


# ---------------------------------------------------------------------------
# synpp execute
# ---------------------------------------------------------------------------

def execute(context):
    import logging as _logging
    import chainsolvers as cs  # imported lazily so tests w/o the dep work

    # Chainsolvers' io module emits INFO per solve() call (input summary,
    # column check, results summary). At 25% scale this floods the log
    # with hundreds of thousands of lines — bump the threshold to WARNING
    # so only real problems surface.
    _logging.getLogger("chainsolvers").setLevel(_logging.WARNING)
    _logging.getLogger("chainsolvers.io").setLevel(_logging.WARNING)
    _logging.getLogger("chainsolvers.locations").setLevel(_logging.WARNING)

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
    # input-side distributions are bit-comparable.
    _resample_distributions(distance_distributions, dict(
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

    def _run_fallback(problem_indices):
        if not problem_indices:
            return [], []
        if fallback_strategy == "rda":
            return _rda_fallback_place(
                problems, problem_indices, df_secondary,
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

    ctx = cs.setup(
        locations_df=locations_df,
        solver=context.config("braunschweig.chainsolvers.solver") or "carla",
        rng_seed=int(random.randint(0, 2**31 - 1)),
    )

    print("[braunschweig.secondary_chainsolvers] running cs.solve()...")
    t0 = time.time()
    # Drop helper columns chainsolvers does not expect.
    plans_for_cs = plans_df.drop(columns=["_leg_index", "_problem_idx"])

    # Solve in chunks; fall back to per-person within a failing chunk so
    # that a single problematic person does not abort thousands of valid
    # ones. Chunked solving avoids the per-call validation overhead.
    failed_problem_idx: List[int] = []
    result_chunks: List[pd.DataFrame] = []
    unique_persons = plans_for_cs["unique_person_id"].drop_duplicates().to_list()
    n_total = len(unique_persons)
    CHUNK_SIZE = 500
    n_done = 0
    n_failed = 0
    n_log_step = max(CHUNK_SIZE, n_total // 20)
    by_person = dict(tuple(plans_for_cs.groupby("unique_person_id", sort=False)))

    for start in range(0, n_total, CHUNK_SIZE):
        chunk_uids = unique_persons[start:start + CHUNK_SIZE]
        chunk_df = pd.concat([by_person[u] for u in chunk_uids], ignore_index=True)
        try:
            res_df, _seg, _v = cs.solve(ctx=ctx, plans_df=chunk_df)
            result_chunks.append(res_df)
        except Exception:
            # Retry per-person to isolate the failures.
            for uid in chunk_uids:
                person_chunk = by_person[uid]
                try:
                    res_df, _seg, _v = cs.solve(ctx=ctx, plans_df=person_chunk)
                    result_chunks.append(res_df)
                except Exception:
                    try:
                        _, prob_idx_str = str(uid).rsplit("#", 1)
                        failed_problem_idx.append(int(prob_idx_str))
                    except ValueError:
                        pass
                    n_failed += 1
        n_done += len(chunk_uids)
        if n_done % n_log_step < CHUNK_SIZE or n_done == n_total:
            print(
                f"[braunschweig.secondary_chainsolvers] solved "
                f"{n_done:,}/{n_total:,} persons (failed={n_failed:,}, "
                f"elapsed={time.time() - t0:.0f}s)"
            )

    if result_chunks:
        result_df = pd.concat(result_chunks, ignore_index=True)
    else:
        result_df = pd.DataFrame(columns=[
            "unique_person_id", "unique_leg_id", "to_act_type",
            "distance_meters", "from_x", "from_y", "to_x", "to_y",
            "to_act_identifier",
        ])

    # Route failed bounded problems through the configured fallback.
    extra_rows, extra_conv = _run_fallback(failed_problem_idx)
    fallback_rows = list(fallback_rows) + extra_rows
    fallback_conv = list(fallback_conv) + extra_conv
    print(
        f"[braunschweig.secondary_chainsolvers] cs.solve() finished in "
        f"{time.time() - t0:.1f}s; "
        f"persons solved={n_total - n_failed:,}, failed={n_failed:,}"
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
