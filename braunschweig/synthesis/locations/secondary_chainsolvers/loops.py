"""Strolls and dog walks: which synthetic leisure legs are loops, and where they are placed (#679).

The MiD 2023 diary records a stroll (``W_ZWD`` 710) or a dog walk (711) as ONE trip whose length
is the whole loop: the person leaves the previous activity, walks (or rides) around, and comes
back to the same place. The secondary-location stage takes such a loop activity out of the chain
and places it at the activity before it. This module holds the pure functions of that rule and
wires into no stage itself:

- :func:`flag_loops` decides WHICH legs are loops (below);
- :func:`select_collapsible_loops` keeps on the solver the loops that cannot be taken out (an
  anchored or linked loop activity, the first loop after an outside stay);
- :func:`collapse_loops` takes the other loop activities out of the trips table before the chain
  solver runs, the ``to_collapsed_*`` functions move the anchors and links of the collapsed persons
  to the collapsed numbering, and :func:`restore_loops` puts the loops back afterwards at the place
  of the activity they start from (see :func:`collapse_loops` for the index convention and the
  assumption).

Two sources of the flag, in this order:

1. By label. A leg whose donor carries a leisure detail code (``W_ZWD`` in the group codes of
   ``purpose_subtype.leisure_spec``) is a loop exactly when the code is in :data:`LOOP_CODES`.
2. By draw. A leg whose donor's ``W_ZWD`` is present but is no leisure group code (every other
   code: the design sentinels 2202 "not asked in PAPI", 4402 "child under 14", 999, ..., the
   cross-purpose codes 503, 603 and 605 (shop and errand detail codes on leisure legs, ADR-0207
   assumption) and, with ``purpose_subtype_codeplan_sentinels`` on, 799 "Freizeit k.A.") draws
   ``Bernoulli(p)`` once, ``p`` being the weighted MiD share of loops
   among the labelled legs of its cell ``(mode, tt_band, starts_at_home, way_home_follows)``.
   ``tt_band`` is ``braunschweig.popsim.purpose_subtype.tt_band`` (defined in
   ``braunschweig.popsim.shop_subtype``), the band function the leisure subtype decider uses.

ASSUMPTION: the loop probability estimated on the legs whose detail was asked (CATI/CAWI) holds
for the legs where it was not asked (PAPI, children). The MiD cannot test this: the unasked legs
are exactly the ones without the label.

Scope of the flag: ``following_purpose == "leisure"``, mode in :data:`LOOP_MODES`, ``W_ZWECK`` in
``purpose_subtype.LEISURE_ZWECK`` and a donor detail code present. Every other leg is False and
consumes no random number. A leg with a ``W_ZWECK`` or ``W_ZWD`` that is NaN (closure trips,
in-commuters: no donor leg) is out of scope; the legs that meet every other scope condition but
have no ``W_ZWD`` are counted (``missing_detail_code`` in the stats of :func:`flag_loops`).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

import geopandas as gpd
import numpy as np
import pandas as pd

from braunschweig.popsim.distance_distributions import _build_preceding_purpose
# LOOP_CODES / LOOP_MODES are owned by purpose_subtype (the loop-free leisure estimates of
# distance_distributions need them, and that module cannot import this one: this one imports it)
# and re-exported here under their established names.
from braunschweig.popsim.purpose_subtype import (  # noqa: F401 (LOOP_* re-exported)
    LEISURE_ZWECK, LOOP_CODES, LOOP_MODES, leisure_spec, tt_band)
from braunschweig.popsim.shop_subtype import drop_legs_of_unknown_duration
from braunschweig.popsim.stage.config_keys import KEY_SECONDARY_LOOP_ZERO_DISPLACEMENT
from braunschweig.popsim.trips import (
    map_mode, map_purpose, mid_travel_time_seconds, restrict_to_weekday_diary_legs)
from braunschweig.synthesis.portal_trips.config_keys import OUTSIDE_PURPOSE

from .fallback import FALLBACK_WARN_RATE

logger = logging.getLogger(__name__)

#: Offset added to ``random_seed`` for the loop draw's own stream. It differs from the offsets of
#: the stage's other streams (``deciders.SHOP_SUBTYPE_SEED_OFFSET``,
#: ``deciders.LEISURE_SUBTYPE_SEED_OFFSET``, ``deciders.OTHER_SUBTYPE_SEED_OFFSET``,
#: ``deciders.ESCORT_LOCATION_SEED_OFFSET``, ``srv_location_types.SRV_LOCATION_SEED_OFFSET``)
#: and from the solver's own stream (``random_seed`` itself), so the loop draw neither reads the
#: uniforms of another stream nor perturbs the subtype, escort, SrV location-type or distance
#: draws; ``tests/test_secondary_loops.py`` checks that these offsets are pairwise distinct.
LOOP_SEED_OFFSET = 90216

#: Config key of the feature flag (declared by the stage wiring, not by this module). Owned by
#: ``braunschweig.popsim.stage.config_keys`` since the distance distributions stage reads it too.
KEY_ENABLED = KEY_SECONDARY_LOOP_ZERO_DISPLACEMENT

#: Columns :func:`flag_loops` reads from the synthetic trips.
_TRIP_COLUMNS = ("person_id", "trip_index", "mode", "preceding_purpose", "following_purpose",
                 "travel_time", "W_ZWD", "W_ZWECK")

#: Columns :func:`estimate_loop_probabilities` reads from the MiD Wege table.
_MID_COLUMNS = ("H_ID", "P_ID", "W_ID", "W_ZWECK", "W_ZWD", "W_GEW", "hvm_imp")

_STAT_KEYS = ("in_scope", "by_label", "by_draw", "draws", "draws_from_marginal",
              "missing_detail_code")

#: Purposes whose place is fixed before the chain solver runs; a loop that starts at one of them
#: copies that place, and ``loop_origin`` names it by the purpose.
PRIMARY_LOOP_ORIGINS = ("home", "work", "education")

#: ``loop_origin`` of a loop that starts at any other activity: the place the solver drew for it.
SECONDARY_LOOP_ORIGIN = "secondary"

#: The values of ``origin_purpose`` in the loop table and of ``loop_origin`` on a loop row.
LOOP_ORIGINS = (*PRIMARY_LOOP_ORIGINS, SECONDARY_LOOP_ORIGIN)

#: Columns of the index map :func:`collapse_loops` returns.
INDEX_MAP_COLUMNS = ("person_id", "local_activity_index", "activity_index")

#: Columns of the loop table :func:`collapse_loops` returns.
LOOP_TABLE_COLUMNS = ("person_id", "activity_index", "origin_purpose", "origin_activity_index")

#: Reasons :func:`select_collapsible_loops` keeps a loop on the solver, keys of its counts.
KEPT_LOOP_REASONS = ("kept_loop_is_linked_activity", "kept_after_outside")

#: Columns :func:`collapse_loops` reads or rewrites; every other column is carried unchanged.
_COLLAPSE_COLUMNS = ("person_id", "trip_index", "preceding_purpose", "following_purpose",
                     "is_first_trip", "is_last_trip")

#: Columns of the solver's location rows (``results._extract_locations``).
_LOCATION_COLUMNS = ("person_id", "activity_index", "location_id", "geometry")


@dataclass(frozen=True)
class LoopProbabilities:
    """P(loop | leg without a detail code), estimated on the MiD legs with a detail code.

    Attributes
    ----------
    cells:
        ``(mode, tt_band, starts_at_home, way_home_follows) -> probability`` (weighted by
        ``W_GEW``) for the cells with at least ``min_obs`` unweighted observations. ``tt_band`` is
        ``purpose_subtype.tt_band`` of the leg's travel time in seconds.
    marginal:
        ``mode -> probability``, weighted over all labelled legs of the mode; the stand-in for a
        cell that is absent from ``cells``.
    n_observations:
        Unweighted observation counts: per cell key (every cell, also those under ``min_obs``,
        so a reader sees why a cell is absent) and per mode (the marginal's universe).
    labelled_codes:
        The ``W_ZWD`` codes that count as labelled (the group codes of the leisure spec the
        estimate used). A leg with another code draws. ``flag_loops`` reads this set, so the
        estimation universe and the application use one definition of "labelled".
    """

    cells: dict
    marginal: dict
    n_observations: dict
    labelled_codes: frozenset


def _require_unique_index(frame: pd.DataFrame, caller: str) -> None:
    """Raise a ``ValueError`` naming ``caller`` and the duplicate count when the index repeats."""
    if not frame.index.is_unique:
        n_duplicates = int(frame.index.duplicated().sum())
        raise ValueError(
            f"[secondary_loops] {caller} needs a unique index on its frame to align its result; "
            f"{n_duplicates} of {len(frame)} index labels repeat an earlier one. Reset the index "
            "(``df.reset_index(drop=True)``) before the call.")


def _next_leg_is_home(frame: pd.DataFrame, group_columns: list, order_column: str) -> pd.Series:
    """True where the NEXT leg of the same person (by ``order_column``) ends at home.

    The last leg of a person has no next leg and gets False. Aligned to ``frame.index``, which must
    be unique (the callers check it and name themselves in the error).
    """
    ordered = frame.sort_values([*group_columns, order_column])
    next_purpose = ordered.groupby(group_columns, sort=False)["following_purpose"].shift(-1)
    return (next_purpose == "home").reindex(frame.index)


def way_home_follows(df_trips: pd.DataFrame) -> pd.Series:
    """Per synthetic trip: does the next trip of the same person (by ``trip_index``) go home?

    Needs ``person_id``, ``trip_index`` and ``following_purpose``. The last trip of a person is
    False. Aligned to ``df_trips.index``, which must be unique (``ValueError`` otherwise).
    """
    _require_columns(df_trips, ("person_id", "trip_index", "following_purpose"), "trips")
    _require_unique_index(df_trips, "way_home_follows")
    return _next_leg_is_home(df_trips, ["person_id"], "trip_index")


def _require_columns(frame: pd.DataFrame, columns, what: str) -> None:
    missing = [column for column in columns if column not in frame.columns]
    if missing:
        raise ValueError(f"[secondary_loops] the {what} frame is missing column(s) {missing}.")


def estimate_loop_probabilities(mid_wege: pd.DataFrame, *, min_obs: int, weekday_only: bool,
                                codeplan_sentinels: bool = False) -> LoopProbabilities:
    """Estimate P(loop) per cell and per mode on the labelled MiD leisure legs.

    Universe: legs with ``W_ZWECK`` in ``purpose_subtype.LEISURE_ZWECK``, ``W_ZWD`` in the group
    codes of ``leisure_spec(codeplan_sentinels)`` and a mode in :data:`LOOP_MODES`, weighted with
    ``W_GEW``; a loop is a leg with ``W_ZWD`` in :data:`LOOP_CODES`. ``codeplan_sentinels`` must
    be the value of ``purpose_subtype_codeplan_sentinels`` the subtype decider uses (with it on,
    799 is a no-detail code and not part of the universe).

    ``weekday_only`` restricts the table to the weekday diary legs
    (``trips.restrict_to_weekday_diary_legs``) before anything is derived, as the distance layers
    and the subtype deciders do; the preceding purpose and the next leg are then read in the
    restricted diary. Mode, travel time and the preceding purpose come from the helpers of the
    distance layers (``map_mode``, ``mid_travel_time_seconds``, ``_build_preceding_purpose``);
    ``following_purpose`` is ``map_purpose`` with its defaults, which only matters here as "home"
    (``W_ZWECK`` 8 and 9). Legs without a travel time are dropped and counted in the log.

    Cells with fewer than ``min_obs`` unweighted observations are not returned in ``cells``; the
    per-mode marginal covers them. Raises ``ValueError`` when the universe is empty.
    """
    if min_obs < 1:
        raise ValueError(f"[secondary_loops] min_obs must be >= 1, got {min_obs}.")
    _require_columns(mid_wege, _MID_COLUMNS, "MiD Wege")
    labelled_codes = leisure_spec(codeplan_sentinels).group_codes

    wege = mid_wege
    if weekday_only:
        wege = restrict_to_weekday_diary_legs(wege, log_tag="[secondary_loops]")
    wege = map_mode(map_purpose(wege))
    wege["following_purpose"] = wege["purpose"]
    wege["preceding_purpose"] = _build_preceding_purpose(wege)
    _require_unique_index(wege, "estimate_loop_probabilities")
    wege["way_home_follows"] = _next_leg_is_home(wege, ["H_ID", "P_ID"], "W_ID")
    wege["travel_time"] = mid_travel_time_seconds(
        wege, log_tag="[secondary_loops]", log=logger)

    universe = wege[wege["W_ZWECK"].isin(LEISURE_ZWECK)
                    & wege["W_ZWD"].isin(labelled_codes)
                    & wege["mode"].isin(LOOP_MODES)]
    if universe.empty:
        raise ValueError(
            "[secondary_loops] no labelled leisure leg by foot or bicycle in the MiD table "
            f"({len(wege)} legs, weekday_only={weekday_only}); cannot estimate loop probabilities.")
    universe = drop_legs_of_unknown_duration(universe, label="secondary_loops").copy()

    universe["_band"] = universe["travel_time"].map(tt_band)
    universe["_is_loop"] = universe["W_ZWD"].isin(LOOP_CODES)
    universe["_weight"] = universe["W_GEW"].astype(float)
    universe["_starts_at_home"] = (universe["preceding_purpose"] == "home")

    cells: Dict[Tuple, float] = {}
    marginal: Dict[str, float] = {}
    n_observations: Dict[object, int] = {}

    def weighted_loop_share(legs: pd.DataFrame, what: str) -> float:
        weight_total = float(legs["_weight"].sum())
        if not weight_total > 0.0:
            raise ValueError(
                f"[secondary_loops] the W_GEW weights of {what} sum to {weight_total}; "
                "cannot estimate a probability from them.")
        return float(legs.loc[legs["_is_loop"], "_weight"].sum() / weight_total)

    for mode, mode_legs in universe.groupby("mode"):
        marginal[mode] = weighted_loop_share(mode_legs, f"the {mode} legs")
        n_observations[mode] = len(mode_legs)

    n_thin_cells = 0
    cell_columns = ["mode", "_band", "_starts_at_home", "way_home_follows"]
    for (mode, band, starts_at_home, way_home), cell_legs in universe.groupby(cell_columns):
        key = (mode, int(band), bool(starts_at_home), bool(way_home))
        n_observations[key] = len(cell_legs)
        if len(cell_legs) >= min_obs:
            cells[key] = weighted_loop_share(cell_legs, f"the cell {key}")
        else:
            n_thin_cells += 1

    logger.info(
        "[secondary_loops] estimate on %d labelled leisure legs (%s; weekday_only=%s, "
        "codeplan_sentinels=%s): marginal P(loop) %s; %d/%d (mode, tt_band, starts_at_home, "
        "way_home_follows) cells reach min_obs=%d, %d fall back to the marginal",
        len(universe), ", ".join(f"{mode} {n_observations[mode]}" for mode in sorted(marginal)),
        weekday_only, codeplan_sentinels,
        {mode: round(share, 4) for mode, share in sorted(marginal.items())},
        len(cells), len(cells) + n_thin_cells, min_obs, n_thin_cells)
    if not cells:
        logger.warning(
            "[secondary_loops] ALL cells are below min_obs=%d; every drawn leg will use its "
            "mode's marginal, i.e. no cell-level signal was estimated.", min_obs)

    return LoopProbabilities(cells=cells, marginal=marginal, n_observations=n_observations,
                             labelled_codes=frozenset(labelled_codes))


def flag_loops(df_trips: pd.DataFrame, probabilities: LoopProbabilities,
               random: np.random.RandomState) -> Tuple[pd.Series, dict]:
    """Flag the synthetic trips that lead into a loop activity.

    Returns ``(flags, stats)``: ``flags`` is a Boolean Series on ``df_trips.index`` (True: the
    trip leads into a stroll or dog walk), ``stats`` has the keys ``in_scope``, ``by_label``,
    ``by_draw``, ``draws``, ``draws_from_marginal`` and ``missing_detail_code``, each a dict
    ``mode -> count`` over :data:`LOOP_MODES`:

    - ``in_scope``: legs of the scope in the module docstring;
    - ``by_label``: in-scope legs flagged True by their donor's label (710 or 711);
    - ``draws``: in-scope legs without a labelled code, each of which consumed one random number;
    - ``by_draw``: the ``draws`` legs flagged True;
    - ``draws_from_marginal``: the ``draws`` legs whose cell has no estimate of its own and took
      the mode's marginal;
    - ``missing_detail_code``: legs outside the scope ONLY because their ``W_ZWD`` is NaN (leisure
      destination, a mode of :data:`LOOP_MODES`, a ``W_ZWECK`` in ``LEISURE_ZWECK``). They stay
      not-loop and draw nothing; they are counted so that a donor table that lost its detail
      codes shows in the log (WARNING above ``FALLBACK_WARN_RATE`` of in scope plus these).

    The draws are taken in ``(person_id, trip_index)`` order, one ``random.random_sample`` value
    per drawing leg, so the result depends on the seed and the legs, not on the frame's row order.
    Rows out of scope and labelled rows consume no draw.

    ``df_trips.index`` must be unique. Raises ``ValueError`` (before any random number is drawn
    for the legs concerned) when the index repeats, when a leg to draw has no finite
    ``travel_time`` (it cannot be banded), and when a drawing leg's mode has no marginal (no
    silent default probability).
    """
    _require_columns(df_trips, _TRIP_COLUMNS, "trips")
    _require_unique_index(df_trips, "flag_loops")

    leisure_leg_of_loop_mode = ((df_trips["following_purpose"] == "leisure")
                                & df_trips["mode"].isin(LOOP_MODES)
                                & df_trips["W_ZWECK"].isin(LEISURE_ZWECK))
    in_scope = leisure_leg_of_loop_mode & df_trips["W_ZWD"].notna()
    missing_detail_code = leisure_leg_of_loop_mode & df_trips["W_ZWD"].isna()
    labelled = in_scope & df_trips["W_ZWD"].isin(probabilities.labelled_codes)
    by_label = labelled & df_trips["W_ZWD"].isin(LOOP_CODES)
    to_draw = in_scope & ~labelled

    flags = by_label.copy()
    by_draw = pd.Series(False, index=df_trips.index)
    from_marginal = pd.Series(False, index=df_trips.index)

    if bool(to_draw.any()):
        drawing = df_trips[to_draw].sort_values(["person_id", "trip_index"])
        # A leg without a finite travel time cannot be banded; stop with the stage tag and the
        # persons concerned instead of the bare error of tt_band.
        finite = np.isfinite(pd.to_numeric(drawing["travel_time"], errors="coerce").to_numpy(
            dtype=float))
        if not finite.all():
            raise ValueError(
                f"[secondary_loops] flag_loops: {int((~finite).sum())} of {len(drawing)} legs "
                "to draw have no finite travel_time (seconds) and cannot be banded; person_ids "
                f"{drawing.loc[~finite, 'person_id'].unique()[:5].tolist()} (first five).")
        starts_at_home = (drawing["preceding_purpose"] == "home")
        way_home = way_home_follows(df_trips).loc[drawing.index]
        # tt_band itself (not a copy of its edges) bands the validated column, so the cell key
        # is the one the estimate used; it is a per-leg Python call, kept for that reason.
        bands = drawing["travel_time"].map(tt_band)

        probability = np.empty(len(drawing), dtype=float)
        used_marginal = np.zeros(len(drawing), dtype=bool)
        for position, (mode, band, at_home, home_follows) in enumerate(zip(
                drawing["mode"], bands, starts_at_home, way_home)):
            key = (mode, int(band), bool(at_home), bool(home_follows))
            if key in probabilities.cells:
                probability[position] = probabilities.cells[key]
            elif mode in probabilities.marginal:
                probability[position] = probabilities.marginal[mode]
                used_marginal[position] = True
            else:
                raise ValueError(
                    f"[secondary_loops] no loop probability for mode {mode!r}: neither the cell "
                    f"{key} nor a marginal of that mode was estimated (marginal modes: "
                    f"{sorted(probabilities.marginal)}).")
        # All three input checks above have passed: only now does the call consume its random
        # numbers (one per drawing leg, in the sorted order of ``drawing``).
        uniform = random.random_sample(len(drawing))
        drawn_flag = uniform < probability
        flags.loc[drawing.index] = drawn_flag
        by_draw.loc[drawing.index] = drawn_flag
        from_marginal.loc[drawing.index] = used_marginal

    masks = {"in_scope": in_scope, "by_label": by_label, "by_draw": by_draw, "draws": to_draw,
             "draws_from_marginal": from_marginal, "missing_detail_code": missing_detail_code}
    stats = {key: _per_mode(df_trips, masks[key]) for key in _STAT_KEYS}
    _log_stats(stats)
    return flags.astype(bool), stats


def _per_mode(df_trips: pd.DataFrame, mask: pd.Series) -> dict:
    return {mode: int((mask & (df_trips["mode"] == mode)).sum()) for mode in sorted(LOOP_MODES)}


def _log_stats(stats: dict) -> None:
    """Log the flag sources per mode with rates; a high marginal share is a WARNING.

    Fallback transparency (CLAUDE.md): the primary path of a drawing leg is its own cell; the
    marginal is the fallback, and its rate is logged against ``FALLBACK_WARN_RATE``. A leg without
    any detail code is neither labelled nor drawn but left not-loop; its share of the legs that
    have a leisure destination, a loop mode and a leisure ``W_ZWECK`` is logged against the same
    rate.
    """
    for mode in sorted(LOOP_MODES):
        n_missing = stats["missing_detail_code"][mode]
        n_candidates = stats["in_scope"][mode] + n_missing
        missing_rate = n_missing / n_candidates if n_candidates else 0.0
        missing_message = (
            "[secondary_loops] %s: %d of %d leisure legs (%.1f%%) carry no W_ZWD at all and are "
            "left not-loop without a draw (primary: %d with a detail code)")
        missing_arguments = (mode, n_missing, n_candidates, 100.0 * missing_rate,
                             stats["in_scope"][mode])
        if missing_rate > FALLBACK_WARN_RATE:
            logger.warning(missing_message + " -- share above %.0f%%: the donor detail codes "
                           "are probably lost upstream", *missing_arguments,
                           100.0 * FALLBACK_WARN_RATE)
        else:
            logger.info(missing_message, *missing_arguments)
        in_scope = stats["in_scope"][mode]
        draws = stats["draws"][mode]
        from_marginal = stats["draws_from_marginal"][mode]
        in_scope_rate = (lambda count: 100.0 * count / in_scope if in_scope else 0.0)
        marginal_rate = from_marginal / draws if draws else 0.0
        message = (
            "[secondary_loops] %s: in scope %d; loops by label %d (%.1f%%), by draw %d (%.1f%%); "
            "legs drawn %d (%.1f%%), of which %d (%.1f%%) from the mode marginal (primary: own "
            "cell %d)")
        arguments = (
            mode, in_scope, stats["by_label"][mode], in_scope_rate(stats["by_label"][mode]),
            stats["by_draw"][mode], in_scope_rate(stats["by_draw"][mode]),
            draws, in_scope_rate(draws), from_marginal, 100.0 * marginal_rate,
            draws - from_marginal)
        if marginal_rate > FALLBACK_WARN_RATE:
            logger.warning(message + " -- marginal share above %.0f%%: the cell estimates are "
                           "thin for this mode", *arguments, 100.0 * FALLBACK_WARN_RATE)
        else:
            logger.info(message, *arguments)


def _aligned_loop_flags(is_loop: pd.Series, df_trips: pd.DataFrame, caller: str) -> pd.Series:
    """``is_loop`` on ``df_trips.index`` (any order); ``ValueError`` naming ``caller`` unless one
    Boolean per trip."""
    if (len(is_loop) != len(df_trips) or not is_loop.index.is_unique
            or not df_trips.index.isin(is_loop.index).all()):
        raise ValueError(
            f"[secondary_loops] {caller}: is_loop must hold exactly one flag per trip, on "
            f"the index of df_trips (as flag_loops returns it); got {len(is_loop)} flags for "
            f"{len(df_trips)} trips, {int((~df_trips.index.isin(is_loop.index)).sum())} trips "
            "without a flag.")
    if not pd.api.types.is_bool_dtype(is_loop.dtype):
        raise ValueError(f"[secondary_loops] {caller}: is_loop must be Boolean, got dtype "
                         f"{is_loop.dtype}.")
    # A nullable Boolean passes the dtype check but may hold NA, which is neither loop nor not.
    n_missing = int(is_loop.isna().sum())
    if n_missing:
        raise ValueError(f"[secondary_loops] {caller}: {n_missing} of {len(is_loop)} "
                         "is_loop flags are missing (NA); every trip needs True or False.")
    return is_loop.reindex(df_trips.index).astype(bool)


def select_collapsible_loops(df_trips: pd.DataFrame, is_loop: pd.Series,
                             linked_activity_keys) -> Tuple[pd.Series, dict]:
    """The loops :func:`collapse_loops` may take out of the chains, and the counts of those kept.

    ``is_loop`` is the flag of :func:`flag_loops` on ``df_trips.index`` (a trip leads INTO the
    loop activity ``trip_index + 1``); ``linked_activity_keys`` holds the ``(person_id,
    activity_index)`` of every activity the stage fixes or links by its index (anchor keys,
    linked escort and portal rows, both sides of a passive joint link). A flagged loop is KEPT on
    the solver, and counted under the first reason that applies (:data:`KEPT_LOOP_REASONS`):

    - ``kept_loop_is_linked_activity``: the loop activity itself is in ``linked_activity_keys``.
      Its index is the key of the anchor or link, which needs the activity in the chain (in
      practice a stroll a child takes along with an adult, #385);
    - ``kept_after_outside``: the loop starts at an ``outside`` activity (a portal stay, #442).
      The gate is a road crossing at the region boundary, not a place where an activity happens,
      and a leisure activity at a portal facility is unproven in MATSim.

    Every other flagged loop is collapsible. The second reason needs only the activity right before
    the loop, although the place a loop copies is that of its ORIGIN, the last activity before it
    that is not a collapsed loop: if the activity before the loop is itself a loop, that loop is
    either kept (then it is the origin, and a leisure activity) or collapsed (then, by the same
    argument one step earlier, its origin is not ``outside``, and it is this loop's origin too).
    So only the first loop of a run after an outside stay is kept; the next one collapses onto it.
    The rule applies with the portal layer off as well, where it keeps nothing: the only writer of
    the ``outside`` purpose into the trips this stage reads is the portal rewrite
    (``braunschweig.synthesis.portal_trips.rewrite``); with the layer off its stage returns the
    trips unchanged, and the scenario cutter, which turns boundary-crossing trips (and the
    in-commuters' homes, ``braunschweig.synthesis.incommuters``) into outside activities, runs
    later, in ``braunschweig.matsim.simulation.prepare``.

    Returns ``(is_loop_to_collapse, counts)``: a Boolean Series on ``df_trips.index`` (True for
    the collapsible loops, False for every other trip) and ``{reason: count}``. ``is_loop`` must
    hold one Boolean per trip of ``df_trips`` (``ValueError`` otherwise). Pure; the inputs are not
    changed.
    """
    _require_columns(df_trips, ("person_id", "trip_index", "preceding_purpose"), "trips")
    _require_unique_index(df_trips, "select_collapsible_loops")
    flags = _aligned_loop_flags(is_loop, df_trips, "select_collapsible_loops")

    is_linked = pd.Series(False, index=df_trips.index)
    loop_trips = df_trips[flags]
    if len(loop_trips) and linked_activity_keys:
        loop_activities = pd.MultiIndex.from_arrays(
            [loop_trips["person_id"], loop_trips["trip_index"] + 1])
        is_linked.loc[loop_trips.index] = loop_activities.isin(list(linked_activity_keys))
    after_outside = flags & ~is_linked & (df_trips["preceding_purpose"] == OUTSIDE_PURPOSE)
    to_collapse = flags & ~is_linked & ~after_outside
    counts = {"kept_loop_is_linked_activity": int(is_linked.sum()),
              "kept_after_outside": int(after_outside.sum())}
    logger.info(
        "[secondary_loops] select_collapsible_loops: %d of %d flagged loops collapsible; kept on "
        "the solver: %d whose activity is anchored or linked itself, %d right after an outside "
        "stay", int(to_collapse.sum()), int(flags.sum()), counts["kept_loop_is_linked_activity"],
        counts["kept_after_outside"])
    return to_collapse.astype(bool), counts


def _origin_kind(purposes: pd.Series) -> np.ndarray:
    """``home``, ``work`` and ``education`` keep their name; any other purpose is ``secondary``."""
    is_primary = purposes.isin(PRIMARY_LOOP_ORIGINS)
    return np.where(is_primary, purposes, SECONDARY_LOOP_ORIGIN).astype(object)


def collapse_loops(df_trips: pd.DataFrame, is_loop: pd.Series,
                   eligible_persons: Optional[set] = None
                   ) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Take the loop activities of the eligible persons out of their trip chains.

    ASSUMPTION (owner's decision for #679, the eqasim-france Paris model; design spec section 1):
    a stroll or dog walk has no displacement. The loop activity therefore needs no place of its
    own: it is taken out of the chain here, the chain solver places the remaining activities, and
    :func:`restore_loops` puts the loop back at the place of the activity it starts from.

    Index convention (the one ``find_assignment_problems`` derives from ``trip_index``): the
    activity before trip ``k`` has activity index ``k``, the activity after it ``k + 1``. A trip
    flagged in ``is_loop`` (a Boolean Series on ``df_trips.index``, as :func:`flag_loops` returns
    it, or the collapsible subset of :func:`select_collapsible_loops`) leads INTO a loop activity.
    ``eligible_persons`` restricts the collapse to these persons; None (the stage's call) collapses
    every person with a flagged loop.

    For every eligible person with at least one loop trip:

    - every loop trip is dropped (also a run of consecutive loops, and a loop as the last
      activity of the day);
    - the trip after a run of loops now leaves the activity before the run: its
      ``preceding_purpose`` becomes that activity's purpose; all its other values (mode, times,
      ``travel_time``, the donor columns) stay;
    - ``trip_index`` is renumbered without gaps from the person's first index, and
      ``is_first_trip`` / ``is_last_trip`` are rebuilt. A person whose trips were all loops has
      no trip left.

    Every other row (persons not eligible, persons without a loop) comes back with the same index
    label and values; the row order of ``df_trips`` is kept; the input is not changed. The
    ``trip_index`` of a person to collapse must be contiguous from its first value (``ValueError``
    otherwise), and a loop trip must not lead into a home, work or education activity
    (``ValueError``).

    Returns ``(collapsed trips, index map, loop table)``:

    - index map (:data:`INDEX_MAP_COLUMNS`): for every activity left in a collapsed person's
      chain, ``local_activity_index`` (its index in the collapsed chain) and ``activity_index``
      (its index in ``df_trips``); only collapsed persons are listed;
    - loop table (:data:`LOOP_TABLE_COLUMNS`): one row per loop activity taken out, sorted by
      ``(person_id, activity_index)``: its original ``activity_index``, the
      ``origin_activity_index`` of the last activity before it that is not a loop (two loops in
      a row share it), and ``origin_purpose``, that activity's purpose as one of
      :data:`LOOP_ORIGINS` (home, work and education by name, every other purpose
      ``secondary``).
    """
    _require_columns(df_trips, _COLLAPSE_COLUMNS, "trips")
    _require_unique_index(df_trips, "collapse_loops")
    flags = _aligned_loop_flags(is_loop, df_trips, "collapse_loops")

    into_primary = flags & df_trips["following_purpose"].isin(PRIMARY_LOOP_ORIGINS)
    if bool(into_primary.any()):
        examples = list(zip(df_trips.loc[into_primary, "person_id"],
                            df_trips.loc[into_primary, "trip_index"]))[:5]
        raise ValueError(
            f"[secondary_loops] collapse_loops: {int(into_primary.sum())} trips flagged as loops "
            "lead into a home, work or education activity; a loop activity must be a secondary "
            f"one. First (person_id, trip_index): {_plain(examples)}.")

    eligible = flags if eligible_persons is None else (
        flags & df_trips["person_id"].isin(eligible_persons))
    loop_persons = df_trips.loc[eligible, "person_id"]
    collapsing = df_trips["person_id"].isin(loop_persons.unique())
    person_dtype = df_trips["person_id"].dtype
    index_dtype = df_trips["trip_index"].dtype
    if not bool(collapsing.any()):
        index_map = pd.DataFrame({
            "person_id": pd.Series(dtype=person_dtype),
            "local_activity_index": pd.Series(dtype=index_dtype),
            "activity_index": pd.Series(dtype=index_dtype)})
        loop_table = pd.DataFrame({
            "person_id": pd.Series(dtype=person_dtype),
            "activity_index": pd.Series(dtype=index_dtype),
            "origin_purpose": pd.Series(dtype=object),
            "origin_activity_index": pd.Series(dtype=index_dtype)})
        _log_collapse(0, 0, 0)
        return df_trips.copy(), index_map, loop_table

    # Only the persons with a loop to take out are worked on (a few percent of all persons).
    trips = df_trips.loc[collapsing, ["person_id", "trip_index", "preceding_purpose"]].assign(
        is_loop=flags[collapsing]).sort_values(["person_id", "trip_index"], kind="stable")
    person = trips["person_id"]
    position = trips.groupby(person, sort=False).cumcount()
    first_index = trips.groupby(person, sort=False)["trip_index"].transform("min")
    gaps = trips["trip_index"] != first_index + position
    if bool(gaps.any()):
        raise ValueError(
            "[secondary_loops] collapse_loops: the trip_index of a person must be contiguous (no "
            "gaps, no repeats) from its first value, because the activity indices are derived "
            "from it; person_ids "
            f"{_plain(trips.loc[gaps, 'person_id'].unique()[:5].tolist())} (first five) do not.")

    loop = trips["is_loop"].to_numpy(dtype=bool)
    previous_is_loop = trips.groupby(person, sort=False)["is_loop"].shift(
        1, fill_value=False).to_numpy(dtype=bool)
    run_start = loop & ~previous_is_loop
    # The activity a run of loops starts from is the one its first loop trip leaves (index =
    # that trip's index, purpose = its preceding_purpose). Carried forward within the person, it
    # reaches every loop of the run and the trip after the run; the next run sets a new one.
    origin_activity_index = trips["trip_index"].where(run_start).groupby(person).ffill()
    origin_purpose = trips["preceding_purpose"].where(run_start).groupby(person).ffill()
    resumes = ~loop & previous_is_loop
    kept = ~loop
    kept_rank = pd.Series(kept.astype(np.int64), index=trips.index).groupby(person).cumsum()
    kept_total = kept_rank.groupby(person).transform("max")

    loop_rows = trips[loop]
    loop_table = pd.DataFrame({
        "person_id": loop_rows["person_id"].to_numpy(),
        "activity_index": (loop_rows["trip_index"] + 1).to_numpy(),
        "origin_purpose": _origin_kind(origin_purpose[loop]),
        "origin_activity_index": origin_activity_index[loop].to_numpy().astype(index_dtype),
    })

    # Activity k + 1 stays in the chain exactly when trip k is kept; the first activity of the
    # chain always stays. A kept trip's destination is the (kept_rank)-th activity after it.
    starts = trips.drop_duplicates("person_id")
    kept_rows = trips[kept]
    index_map = pd.concat([
        pd.DataFrame({"person_id": starts["person_id"].to_numpy(),
                      "local_activity_index": starts["trip_index"].to_numpy(),
                      "activity_index": starts["trip_index"].to_numpy()}),
        pd.DataFrame({"person_id": kept_rows["person_id"].to_numpy(),
                      "local_activity_index": (first_index + kept_rank)[kept].to_numpy(),
                      "activity_index": (kept_rows["trip_index"] + 1).to_numpy()}),
    ]).sort_values(["person_id", "local_activity_index"]).reset_index(drop=True)

    collapsed = df_trips.copy()
    kept_labels = trips.index[kept]
    collapsed.loc[kept_labels, "trip_index"] = (first_index + kept_rank - 1)[kept].to_numpy()
    collapsed.loc[kept_labels, "is_first_trip"] = (kept_rank == 1)[kept].to_numpy()
    collapsed.loc[kept_labels, "is_last_trip"] = (kept_rank == kept_total)[kept].to_numpy()
    collapsed.loc[trips.index[resumes], "preceding_purpose"] = origin_purpose[resumes].to_numpy()
    collapsed = collapsed.drop(index=trips.index[loop])
    _log_collapse(n_persons=len(starts), n_loops=int(loop.sum()),
                  n_without_trips=int((kept_total.groupby(person).first() == 0).sum()))
    return collapsed, index_map, loop_table.sort_values(
        ["person_id", "activity_index"]).reset_index(drop=True)


def _log_collapse(n_persons: int, n_loops: int, n_without_trips: int) -> None:
    logger.info(
        "[secondary_loops] collapse_loops: %d persons collapsed, %d loop activities taken out of "
        "their chains, %d persons left with zero trips (all their trips led into loops)",
        n_persons, n_loops, n_without_trips)


def to_collapsed_activity_index(person_ids, activity_indices, index_map: pd.DataFrame, *,
                                what: str) -> np.ndarray:
    """Original activity indices -> indices in the collapsed chains (the inverse of ``index_map``).

    ``person_ids`` and ``activity_indices`` are aligned sequences of activities in the numbering of
    the trips before :func:`collapse_loops`; ``index_map`` is that call's index map. An activity of
    a person in the map gets its ``local_activity_index``; every other person keeps its index (its
    chain was not changed). Returns a new array with the dtype of ``activity_indices``.

    The stage moves every anchor and link (anchor keys, linked escort and portal rows, both sides
    of a passive joint link) with this one function, before the solve, so that the solver and the
    pass-2 anchor resolution see them in the numbering of the chains they solve; ``restore_loops``
    maps every row back afterwards. An anchored or linked activity is never a collapsed loop
    (:func:`select_collapsible_loops` keeps such loops), so an activity of a collapsed person that
    is not in the map means the selection and the collapse disagree: ``ValueError`` naming
    ``what`` and the first ``(person_id, activity_index)`` concerned; there is no stand-in index.
    """
    _require_columns(index_map, INDEX_MAP_COLUMNS, "index map")
    person_ids = np.asarray(person_ids)
    original = np.asarray(activity_indices)
    local = original.copy()
    if len(index_map) == 0 or len(original) == 0:
        return local
    of_collapsed = np.isin(person_ids, index_map["person_id"].unique())
    if not of_collapsed.any():
        return local
    lookup = index_map.set_index(["person_id", "activity_index"])["local_activity_index"]
    if not lookup.index.is_unique:
        raise ValueError(
            f"[secondary_loops] {what}: the index map repeats an original (person_id, "
            f"activity_index) {int(lookup.index.duplicated().sum())} times; it must come from one "
            "collapse_loops call.")
    persons, activities = person_ids[of_collapsed], original[of_collapsed]
    mapped = lookup.reindex(pd.MultiIndex.from_arrays([persons, activities]))
    unmapped = mapped.isna().to_numpy()
    if unmapped.any():
        examples = list(zip(persons[unmapped], activities[unmapped]))[:5]
        raise ValueError(
            f"[secondary_loops] {what}: {int(unmapped.sum())} anchored or linked activities of "
            "collapsed persons are no activity of the collapsed chain (they were taken out as "
            "loops), so they have no index there; an anchored or linked loop must stay on the "
            "solver (select_collapsible_loops). First (person_id, original activity_index): "
            f"{_plain(examples)}.")
    local[of_collapsed] = mapped.to_numpy().astype(local.dtype)
    return local


def to_collapsed_anchor_keys(activity_anchors, index_map: pd.DataFrame, *,
                             what: str = "activity anchors"):
    """``{(person_id, activity_index): point}`` with the keys moved to the collapsed numbering.

    :func:`to_collapsed_activity_index` per key; the points and the order of the entries are kept,
    and a key whose index does not change is kept as it is. None or an empty mapping, and an empty
    ``index_map`` (nobody collapsed), return ``activity_anchors`` itself.
    """
    if not activity_anchors or len(index_map) == 0:
        return activity_anchors
    keys = list(activity_anchors)
    local = to_collapsed_activity_index([person_id for person_id, _ in keys],
                                        [activity_index for _, activity_index in keys],
                                        index_map, what=what)
    moved = {}
    for (person_id, activity_index), local_index, point in zip(keys, local,
                                                               activity_anchors.values()):
        key = ((person_id, activity_index) if local_index == activity_index
               else (person_id, int(local_index)))
        moved[key] = point
    if len(moved) != len(activity_anchors):
        raise ValueError(
            f"[secondary_loops] {what}: {len(activity_anchors)} anchors became {len(moved)} keys "
            "in the collapsed numbering; two anchors of one person cannot share an activity.")
    return moved


def to_collapsed_activity_rows(rows: Optional[pd.DataFrame], index_map: pd.DataFrame, *,
                               what: str) -> Optional[pd.DataFrame]:
    """A copy of ``rows`` (``person_id``, ``activity_index``, ...) with ``activity_index`` in the
    collapsed numbering (:func:`to_collapsed_activity_index`); every other column and the row order
    are kept. None, an empty frame and an empty ``index_map`` return ``rows`` itself."""
    if rows is None or len(rows) == 0 or len(index_map) == 0:
        return rows
    _require_columns(rows, ("person_id", "activity_index"), what)
    moved = rows.copy()
    moved["activity_index"] = to_collapsed_activity_index(
        rows["person_id"].to_numpy(), rows["activity_index"].to_numpy(), index_map, what=what)
    return moved


def to_collapsed_links(links: Optional[pd.DataFrame], index_map: pd.DataFrame
                       ) -> Optional[pd.DataFrame]:
    """A copy of the passive joint link table (#385) with both sides in the collapsed numbering:
    ``child_activity_index`` with the child's map, ``adult_activity_index`` with the adult's
    (:func:`to_collapsed_activity_index`). None, an empty table and an empty ``index_map`` return
    ``links`` itself."""
    if links is None or len(links) == 0 or len(index_map) == 0:
        return links
    moved = links.copy()
    for side in ("child", "adult"):
        person_column, index_column = f"{side}_person_id", f"{side}_activity_index"
        _require_columns(links, (person_column, index_column), "passive joint links")
        moved[index_column] = to_collapsed_activity_index(
            links[person_column].to_numpy(), links[index_column].to_numpy(), index_map,
            what=f"passive joint links ({side} side)")
    return moved


def _plain(value):
    """``value`` with numpy scalars turned into Python ones, for readable error messages."""
    if isinstance(value, (list, tuple)):
        return type(value)(_plain(item) for item in value)
    return value.item() if isinstance(value, np.generic) else value


def _unique_lookup(frame: pd.DataFrame, keys, columns, what: str) -> pd.DataFrame:
    """``frame[columns]`` indexed by ``keys``; ``ValueError`` naming ``what`` when a key repeats."""
    table = frame.set_index(list(keys))[list(columns)]
    if not table.index.is_unique:
        raise ValueError(
            f"[secondary_loops] restore_loops: the {what} repeat a key {list(keys)} "
            f"({int(table.index.duplicated().sum())} times); a loop cannot pick one of them.")
    return table


def _raise_if_missing(loops: pd.DataFrame, missing: np.ndarray, origin: str, reason: str) -> None:
    if bool(missing.any()):
        examples = list(zip(loops.loc[missing, "person_id"], loops.loc[missing, "activity_index"],
                            loops.loc[missing, "origin_activity_index"]))[:5]
        raise ValueError(
            f"[secondary_loops] restore_loops: {int(missing.sum())} loop activities copy a "
            f"{origin} place, but {reason}. First (person_id, activity_index, "
            f"origin_activity_index): {_plain(examples)}.")


def _whole_activity_indices(values: pd.Series) -> pd.Series:
    """``values`` as int64; ``ValueError`` when one is missing or no whole number.

    The rows ``restore_loops`` receives can carry an object ``activity_index`` (a concat with an
    empty solver frame, whose columns have no dtype); read as int64, the remap and the duplicate
    check compare like with like.
    """
    if values.dtype.kind in "iu":
        return values.astype("int64")
    numbers = pd.to_numeric(values, errors="coerce").astype(float)
    whole = np.isfinite(numbers) & (numbers == np.round(numbers))
    if not bool(whole.all()):
        examples = values[~whole].tolist()[:5]
        raise ValueError(
            f"[secondary_loops] restore_loops: {int((~whole).sum())} location rows have an "
            f"activity_index that is no whole number, first {_plain(examples)}; the solver rows "
            "and the anchored rows must carry integer activity indices.")
    return numbers.astype("int64")


def restore_loops(df_locations: gpd.GeoDataFrame, index_map: pd.DataFrame, loops: pd.DataFrame,
                  *, df_persons: pd.DataFrame, df_home: gpd.GeoDataFrame,
                  df_work: gpd.GeoDataFrame, df_education: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Give the solver's location rows their original activity indices and add one row per loop.

    ``df_locations`` are the solver's rows for the COLLAPSED chains (``person_id``,
    ``activity_index``, ``location_id``, ``geometry``), in the stage together with the rows of the
    anchored activities (linked escort, portal and joint anchor rows) in the same numbering (the
    ``to_collapsed_*`` functions); ``index_map`` and ``loops`` come from the :func:`collapse_loops`
    call that built those chains. A row of a person in ``index_map`` gets its original
    ``activity_index`` back (``ValueError`` when the map has no entry for it); rows of other
    persons keep theirs.

    Then every loop activity gets one row at the place of its origin activity (ASSUMPTION: a loop
    has no displacement, see :func:`collapse_loops`), with ``loop_origin`` naming the kind:

    - ``home``: the household's home ``geometry`` (``df_persons`` gives the household,
      ``df_home`` its place) and ``location_id`` ``home_<household_id>``, the id of the home
      facility the MATSim writers build (``matsim/scenario/population.py::add_person`` and
      ``matsim/scenario/facilities.py::write_facilities``, both ``"home_%s" % household_id``);
    - ``work`` / ``education``: the person's ``location_id`` and ``geometry`` in ``df_work`` /
      ``df_education``;
    - ``secondary``: the ``location_id`` and ``geometry`` of the origin's row in the restored
      rows, an anchored origin included (a stroll after a linked escort drop-off copies the
      child's education place, one after a child's joint activity the adult's placed location).

    The origin is never itself a loop (``origin_activity_index`` is the last activity before the
    loop that is not one), so two loops in a row copy the same place and the order of the lookups
    does not matter. An origin without a place raises a ``ValueError`` naming the person, the loop
    and the origin activity; there is no stand-in place.

    Returns a GeoDataFrame in the CRS of ``df_locations``: the solver rows in their order, then
    the loop rows sorted by ``(person_id, activity_index)``, on a fresh RangeIndex, with the
    column ``loop_origin`` (one of :data:`LOOP_ORIGINS` on a loop row, None on every other row).
    The places must be in the CRS of ``df_locations`` where both CRSs are set (``ValueError``
    otherwise; a place frame without a CRS is read as being in it and named in an INFO line), and
    no ``(person_id, activity_index)`` may end up twice. ``activity_index`` is read as int64 (an
    object column of whole numbers is accepted, a missing or fractional value raises
    ``ValueError``). The inputs are not changed.
    """
    _require_columns(df_locations, _LOCATION_COLUMNS, "locations")
    _require_columns(index_map, INDEX_MAP_COLUMNS, "index map")
    _require_columns(loops, LOOP_TABLE_COLUMNS, "loop table")
    _require_columns(df_persons, ("person_id", "household_id"), "persons")
    _require_columns(df_home, ("household_id", "geometry"), "home places")
    _require_columns(df_work, ("person_id", "location_id", "geometry"), "work places")
    _require_columns(df_education, ("person_id", "location_id", "geometry"), "education places")
    crs = df_locations.crs
    without_crs = []
    for frame, what in ((df_home, "home"), (df_work, "work"), (df_education, "education")):
        frame_crs = getattr(frame, "crs", None)
        if crs is not None and frame_crs is None:
            without_crs.append(what)
        elif crs is not None and frame_crs != crs:
            raise ValueError(
                f"[secondary_loops] restore_loops: the {what} places are in CRS {frame_crs}, the "
                f"locations in CRS {crs}; a copied place would land elsewhere.")
    if without_crs:
        # ASSUMPTION: a source without a CRS is in the stage CRS (primary.locations builds its
        # frames without setting one). It cannot be checked, so the run goes on and says so.
        logger.info(
            "[secondary_loops] restore_loops: the %s places carry no CRS; their geometry is read "
            "as being in the CRS of the locations (%s).", ", ".join(without_crs), crs)
    unknown_origins = ~loops["origin_purpose"].isin(LOOP_ORIGINS)
    if bool(unknown_origins.any()):
        unknown = sorted(loops.loc[unknown_origins, "origin_purpose"].astype(str).unique())
        raise ValueError("[secondary_loops] restore_loops: origin_purpose must be one of "
                         f"{list(LOOP_ORIGINS)}; got {unknown}.")

    restored = df_locations.copy()
    restored["activity_index"] = _whole_activity_indices(restored["activity_index"])
    collapsed = restored["person_id"].isin(index_map["person_id"].unique()).to_numpy()
    if collapsed.any():
        original_index = _unique_lookup(
            index_map, ("person_id", "local_activity_index"), ("activity_index",), "index map")
        keys = pd.MultiIndex.from_arrays([restored.loc[collapsed, "person_id"],
                                          restored.loc[collapsed, "activity_index"]])
        mapped = original_index["activity_index"].reindex(keys)
        unmapped = mapped.isna().to_numpy()
        if unmapped.any():
            raise ValueError(
                f"[secondary_loops] restore_loops: {int(unmapped.sum())} solver rows of collapsed "
                "persons have an activity index the index map does not know; first (person_id, "
                f"activity_index): {_plain(list(keys[unmapped])[:5])}. The rows and the map must "
                "come from the same collapse_loops call.")
        restored.loc[collapsed, "activity_index"] = mapped.to_numpy()

    ordered = loops.sort_values(["person_id", "activity_index"]).reset_index(drop=True)
    location_id = np.full(len(ordered), None, dtype=object)
    geometry = np.full(len(ordered), None, dtype=object)
    for origin in LOOP_ORIGINS:
        of_origin = (ordered["origin_purpose"] == origin).to_numpy()
        if not of_origin.any():
            continue
        origin_loops = ordered[of_origin]
        if origin == "home":
            households = _unique_lookup(df_persons, ("person_id",), ("household_id",), "persons")[
                "household_id"].reindex(origin_loops["person_id"])
            _raise_if_missing(origin_loops, households.isna().to_numpy(), origin,
                              "the person has no household in df_persons")
            place = _unique_lookup(
                df_home, ("household_id",), ("geometry",), "home places").reindex(households)
            _raise_if_missing(origin_loops, place["geometry"].isna().to_numpy(), origin,
                              "the household has no home place in df_home")
            # The id the MATSim writers give the home facility (see the docstring).
            ids = ["home_%s" % household_id for household_id in households]
        elif origin in PRIMARY_LOOP_ORIGINS:
            frame = df_work if origin == "work" else df_education
            place = _unique_lookup(frame, ("person_id",), ("location_id", "geometry"),
                                   f"{origin} places").reindex(origin_loops["person_id"])
            _raise_if_missing(
                origin_loops, (place["location_id"].isna() | place["geometry"].isna()).to_numpy(),
                origin, f"the person has no {origin} place in df_{origin}")
            ids = place["location_id"].tolist()
        else:
            place = _unique_lookup(restored, ("person_id", "activity_index"),
                                   ("location_id", "geometry"), "solver rows").reindex(
                pd.MultiIndex.from_arrays([origin_loops["person_id"],
                                           origin_loops["origin_activity_index"]]))
            _raise_if_missing(
                origin_loops, (place["location_id"].isna() | place["geometry"].isna()).to_numpy(),
                origin, "the solver result has no row for the origin activity")
            ids = place["location_id"].tolist()
        location_id[of_origin] = np.asarray(ids, dtype=object)
        geometry[of_origin] = np.asarray(list(place["geometry"]), dtype=object)

    loop_rows = gpd.GeoDataFrame({
        "person_id": ordered["person_id"].to_numpy(),
        "activity_index": ordered["activity_index"].to_numpy(),
        "location_id": location_id,
        "geometry": geometry,
        "loop_origin": ordered["origin_purpose"].to_numpy(dtype=object),
    }, geometry="geometry", crs=crs)

    restored["loop_origin"] = None
    columns = list(restored.columns)
    # An empty frame takes no part in the concat, so its all-object dtypes (the solver's empty
    # result) cannot turn the loop rows' integer columns into objects.
    frames = [frame for frame in (restored, loop_rows) if len(frame)] or [restored]
    result = gpd.GeoDataFrame(pd.concat(frames, ignore_index=True).reindex(columns=columns),
                              geometry="geometry", crs=crs)
    repeated = result.duplicated(["person_id", "activity_index"])
    if bool(repeated.any()):
        examples = list(zip(result.loc[repeated, "person_id"],
                            result.loc[repeated, "activity_index"]))[:5]
        raise ValueError(
            f"[secondary_loops] restore_loops: {int(repeated.sum())} (person_id, activity_index) "
            f"occur twice after the restore, first {_plain(examples)}; a loop activity also has a "
            "solver row (were the solver rows remapped with the index map of the same collapse?).")
    by_origin = ordered["origin_purpose"].value_counts()
    logger.info(
        "[secondary_loops] restore_loops: %d solver rows of collapsed persons back on their "
        "original activity index; %d loop rows written, by origin: %s",
        int(collapsed.sum()), len(ordered),
        ", ".join(f"{origin} {int(by_origin.get(origin, 0))}" for origin in LOOP_ORIGINS))
    return result
