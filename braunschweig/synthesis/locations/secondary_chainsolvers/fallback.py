"""Fallback placement for problems the carla solver cannot solve.

``_rda_fallback_place`` re-runs eqasim's GravityChainSolver /
AngularTailSolver / CustomFreeChainSolver pipeline over the LEGACY
candidate frame (the default ``rda`` strategy, preserving the legacy
distance-error objective); ``_fallback_place`` is the pre-2026-04-26
random-candidate stop-gap kept for the ``random`` strategy.
``_build_rda_candidate_index`` builds the KDTree candidate index shared by
both rda fallback calls (unbounded chains and failed-bounded problems).

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

from __future__ import annotations

from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd
import shapely.geometry as geo

from .distance_sampling import _rda_sample_distances
from .plans import SECONDARY_PURPOSES


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
