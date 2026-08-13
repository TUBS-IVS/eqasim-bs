"""Plans-DF construction: the per-leg loop feeding the carla solver.

``_build_plans_df`` walks every assignment problem's legs, samples each
leg's desired distance, applies the subtype / escort / SrV location-type
deciders (all pre-built, passed in) and emits the chainsolvers plans frame
plus the bookkeeping the fallback and reporting stages need.
``SECONDARY_PURPOSES`` / ``FIXED_PURPOSES`` define the purpose taxonomy;
``PLANS_HELPER_COLUMNS`` are stripped by ``_plans_frame_for_solver`` before
the frame is handed to chainsolvers.

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from .activity_types import (
    ESCORT_LOCATION_ACTIVITIES,
    LEISURE_SUBTYPE_ACTIVITIES,
    OTHER_SUBTYPE_ACTIVITIES,
)
from .distance_sampling import _purpose_in_distributions, _sample_leg_distance
from .srv_location_types import (
    SRV_AGGREGATE_PLACEMENT,
    SRV_LEISURE_CATEGORIES,
    SRV_LOCATION_PURPOSES,
    SRV_LOCATION_STAT_PREFIX,
    SRV_OTHER_CATEGORIES,
    srv_location_marginal_fallback_stat,
)


# ---------------------------------------------------------------------------
# Plans-DF construction
# ---------------------------------------------------------------------------

# Eqasim purposes that count as "secondary" (variable). ``home``/``work``/
# ``education`` are fixed (anchors). "escort" (issue #201) is only realised
# when escort_purpose is ON; membership here is inert while no escort legs
# exist, keeping the OFF path byte-identical.
SECONDARY_PURPOSES = {"shop", "leisure", "other", "escort"}
FIXED_PURPOSES = {"home", "work", "education"}

# Helper column carrying a leisure/other leg's MiD distance LABEL (the subtype
# group that drove its distance layer) alongside the SrV placement category
# (issue #262). Present ONLY when the SrV location decider is active: with SrV
# placement ON the placement activity is the drawn category, so this is the only
# remaining handle on the MiD subtype -- the Task-6 excursion boundary-clip
# diagnostic selects its legs through it. Like ``_leg_index`` / ``_problem_idx``
# it is stage-internal and dropped before ``cs.solve()``.
DISTANCE_LABEL_COLUMN = "_distance_label"

# Stage-internal plans_df columns chainsolvers must never see. Order matters for
# the OFF path: dropping the same two legacy columns keeps that frame identical.
PLANS_HELPER_COLUMNS = ("_leg_index", "_problem_idx", DISTANCE_LABEL_COLUMN)


def _plans_frame_for_solver(plans_df: pd.DataFrame) -> pd.DataFrame:
    """Drop the stage-internal helper columns before handing plans to ``cs.solve``.

    ``DISTANCE_LABEL_COLUMN`` only exists on the SrV ON path, so the drop list is
    filtered to the columns actually present -- on the OFF path this removes
    exactly the two legacy helpers, byte-identically to the previous inline
    ``drop(columns=["_leg_index", "_problem_idx"])``.
    """
    return plans_df.drop(
        columns=[column for column in PLANS_HELPER_COLUMNS if column in plans_df.columns])


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
                    escort_distance_by_type: bool = False,
                    srv_location_decider=None) -> Tuple[pd.DataFrame, List[Dict[str, Any]], List[int],
                                                        Dict[str, int], Dict[str, List[float]]]:
    """Assemble the chainsolvers plans_df from BOUNDED problems only.

    Returns ``(plans_df, problem_meta, unbounded_indices, subtype_stats,
    desired_by_category)``. Unbounded problems (tail / head / floating
    chains) are excluded — carla needs both endpoints anchored. They are
    placed by ``_fallback_place``.

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

    ``srv_location_decider`` (issue #262, design A2) DECOUPLES placement from the
    MiD distance label for the two SrV-covered purposes
    (``SRV_LOCATION_PURPOSES``: leisure, other). It is the callable built by
    ``_build_srv_location_decider`` -- ``(purpose, mode, distance_m) ->
    (category, used_marginal)`` -- and is called AFTER ``_sample_leg_distance``,
    so the category is drawn conditioned on the leg's ALREADY SAMPLED desired
    distance and the SrV type<->distance correlation carries over. The drawn
    category (resolved through ``SRV_AGGREGATE_PLACEMENT``, which maps the two
    ``*_misc`` categories back onto the plain aggregate purpose) becomes the
    placement activity, REPLACING the MiD subtype assignment: with this decider
    active the leisure/other subtype deciders still choose the DISTANCE layer
    (and still count their outcomes) but no longer set the placement activity.
    Draws come from that decider's own dedicated seeded RNG (NOT ``random``), so
    the distance-sampling stream -- and hence the OFF path -- stays
    byte-identical. Its counters live in a dedicated ``subtype_stats`` key
    namespace (``SRV_LOCATION_STAT_PREFIX`` + category, plus one
    ``srv_location_marginal_fallback_<purpose>`` counter PER PURPOSE for draws
    resolved from that purpose's marginal distribution because the pinned table
    has no matching (mode, band) cell): ``leisure_visit`` is both a MiD subtype
    and an SrV category, so shared keys would double-count in both log lines, and
    a pooled fallback counter would let a badly covered purpose hide behind a
    well covered one. Default None (OFF) leaves the leg loop byte-identical.

    With that decider active the frame gains ONE extra column,
    ``DISTANCE_LABEL_COLUMN`` (``"_distance_label"``): the MiD subtype group that
    drove the leg's distance layer, i.e. what the placement activity would have
    been without the SrV draw (the drawn leisure/other subtype; the aggregate
    purpose for a leg without an active subtype decider or with an
    ``"other_rest"`` outcome; ``None`` for shop / escort / fixed-anchor legs). It
    is what keeps the Task-6 excursion boundary-clip diagnostic measurable under
    SrV placement (see ``_srv_excursion_boundary_clip_lines``), is stage-internal
    like ``_leg_index`` / ``_problem_idx`` (dropped by
    ``_plans_frame_for_solver`` before ``cs.solve``), and is NOT emitted at all on
    the OFF path, so that frame keeps exactly its legacy columns.

    ``desired_by_category`` (issue #262, Task 9) collects, for every leg the
    ``srv_location_decider`` actually drew a category for, that leg's already
    -sampled desired distance in KILOMETRES, keyed by the BARE (unprefixed)
    category name -- ``{category: [desired_km, ...]}``. This is a draw-
    coherence input only: it lets ``srv_location_draw_summary`` compare the
    DRAWN desired-distance median against the SrV euclidean-equivalent median
    for the same category. It reuses ``distance_m`` already sampled by
    ``_sample_leg_distance`` for the leg (no extra RNG draw) and appends in the
    same deterministic leg-loop order as ``subtype_stats``. Stays an empty
    dict on the OFF path (``srv_location_decider is None``).
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
    # Issue #262: MiD distance label per leg, filled (and emitted) ONLY when the
    # SrV location decider is active -- see DISTANCE_LABEL_COLUMN.
    col_distance_label: List[Any] = []

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
    if srv_location_decider is not None:
        # Prefixed keys (see SRV_LOCATION_STAT_PREFIX): "leisure_visit" is both a
        # MiD subtype and an SrV category, so unprefixed counters would be shared
        # between the two deciders and inflate both log lines.
        subtype_stats.update({
            SRV_LOCATION_STAT_PREFIX + name: 0
            for name in SRV_LEISURE_CATEGORIES + SRV_OTHER_CATEGORIES
        })
        # One marginal-fallback counter PER PURPOSE (never pooled): the purposes
        # differ several-fold in leg volume, so a single pooled counter would let
        # a badly covered purpose hide behind a well covered one.
        subtype_stats.update({
            srv_location_marginal_fallback_stat(purpose): 0
            for purpose in SRV_LOCATION_PURPOSES
        })

    # Draw-coherence input for srv_location_draw_summary (issue #262, Task 9):
    # the desired distance (km) of every leg the SrV decider drew a category
    # for, keyed by the BARE category name (unprefixed -- unlike subtype_stats,
    # there is no "leisure_visit" collision here since this dict is never
    # shared with the MiD subtype counters). Stays empty on the OFF path.
    desired_by_category: Dict[str, List[float]] = defaultdict(list)

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

            # Issue #262: the MiD distance LABEL of a leisure/other leg, i.e. the
            # subtype group that would have been the placement activity before
            # this feature. Emitted as DISTANCE_LABEL_COLUMN on the ON path only
            # (see the docstring), where it is the only surviving handle on the
            # MiD subtype -- the Task-6 excursion boundary-clip diagnostic
            # selects its legs through it. Defaults to the aggregate purpose,
            # which is also the layer a leg without an active subtype decider
            # (or an "other_rest" outcome) actually samples; None for shop /
            # escort / fixed-anchor legs, which have no MiD subtype label.
            distance_label = (
                to_act_type if to_act_type in SRV_LOCATION_PURPOSES else None
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
            #
            # Issue #262: when the SrV location decider is active it OWNS the
            # placement activity (drawn further below, from the sampled desired
            # distance), so the MiD group here stays a pure DISTANCE label.
            if leisure_subtype_decider is not None and to_act_type == "leisure":
                group = leisure_subtype_decider(leg["mode"], leg["travel_time"])
                if srv_location_decider is None:
                    placement_act = group
                distance_label = group
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
            #
            # Issue #262: as in the leisure block above, an active SrV location
            # decider owns the placement activity and the MiD outcome here stays
            # a pure DISTANCE label.
            if other_subtype_decider is not None and to_act_type == "other":
                outcome = other_subtype_decider(leg["mode"], leg["travel_time"])
                subtype_stats[outcome] += 1
                if outcome != "other_rest":
                    if srv_location_decider is None:
                        placement_act = outcome
                    distance_label = outcome
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

            # Issue #262 (design A2): draw the SrV-2023 location CATEGORY AFTER
            # the desired distance, so the type<->distance correlation observed in
            # SrV carries over to the placement. The drawn category replaces the
            # MiD subtype as the placement activity ("leisure_misc"/"other_misc"
            # resolve back to the plain aggregate purpose); the distance keeps the
            # MiD layer it was already sampled from. Shop and escort legs never
            # reach this decider -- they have their own.
            if srv_location_decider is not None and to_act_type in SRV_LOCATION_PURPOSES:
                category, used_marginal = srv_location_decider(
                    to_act_type, leg["mode"], distance_m)
                placement_act = SRV_AGGREGATE_PLACEMENT.get(category, category)
                subtype_stats[SRV_LOCATION_STAT_PREFIX + category] += 1
                if used_marginal:
                    subtype_stats[srv_location_marginal_fallback_stat(to_act_type)] += 1
                # Draw-coherence input (issue #262, Task 9): the leg's already
                # -sampled desired distance, in km, keyed by the BARE category
                # name. No extra RNG draw -- distance_m was sampled above.
                desired_by_category[category].append(distance_m / 1000.0)

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
            if srv_location_decider is not None:
                # ON path only: appended (and the column emitted) solely when the
                # SrV decider is active, so the OFF-path frame keeps exactly its
                # legacy columns and stays byte-identical.
                col_distance_label.append(distance_label)

    if not col_uid:
        # Preserve the legacy empty-frame shape (from_records([]) has NO
        # columns) so the no-bounded-legs early return behaves identically.
        return (pd.DataFrame.from_records([]), problem_meta, unbounded_idx, subtype_stats,
                dict(desired_by_category))

    plans_data = {
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
    }
    if srv_location_decider is not None:
        # Appended LAST so the OFF path's column order is untouched (the golden
        # frame-equality tests pin it).
        plans_data[DISTANCE_LABEL_COLUMN] = col_distance_label
    plans_df = pd.DataFrame(plans_data)
    return plans_df, problem_meta, unbounded_idx, subtype_stats, dict(desired_by_category)
