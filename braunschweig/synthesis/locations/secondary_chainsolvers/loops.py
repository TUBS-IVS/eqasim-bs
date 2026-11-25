"""Loop flag: which synthetic leisure legs are strolls or dog walks (issue #679, task C2).

The MiD 2023 diary records a stroll (``W_ZWD`` 710) or a dog walk (711) as ONE trip whose length
is the whole loop: the person leaves the previous activity, walks (or rides) around, and comes
back to the same place. The secondary-location stage takes such a loop activity out of the chain
and places it at the activity before it (other tasks of #679). This module only decides WHICH
legs are loops; it is a pure function of its inputs and wires into no stage.

Two sources of the flag, in this order:

1. By label. A leg whose donor carries a leisure detail code (``W_ZWD`` in the group codes of
   ``purpose_subtype.leisure_spec``) is a loop exactly when the code is in :data:`LOOP_CODES`.
2. By draw. A leg whose donor carries no leisure detail code (the design sentinels 2202 "not asked
   in PAPI", 4402 "child under 14", 999, ... and, with ``purpose_subtype_codeplan_sentinels`` on,
   799 "Freizeit k.A.") draws ``Bernoulli(p)`` once, ``p`` being the weighted MiD share of loops
   among the labelled legs of its cell ``(mode, tt_band, starts_at_home, way_home_follows)``.

ASSUMPTION: the loop probability estimated on the legs whose detail was asked (CATI/CAWI) holds
for the legs where it was not asked (PAPI, children). The MiD cannot test this: the unasked legs
are exactly the ones without the label.

Scope of the flag: ``following_purpose == "leisure"``, mode in :data:`LOOP_MODES`, ``W_ZWECK`` in
``purpose_subtype.LEISURE_ZWECK`` and a donor detail code present. Every other leg is False and
consumes no random number. A leg with a ``W_ZWECK`` or ``W_ZWD`` that is NaN (closure trips,
in-commuters: no donor leg) is out of scope.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from braunschweig.popsim.distance_distributions import _build_preceding_purpose
from braunschweig.popsim.purpose_subtype import LEISURE_ZWECK, leisure_spec, tt_band
from braunschweig.popsim.shop_subtype import drop_legs_of_unknown_duration
from braunschweig.popsim.trips import (
    map_mode, map_purpose, mid_travel_time_seconds, restrict_to_weekday_diary_legs)

from .fallback import FALLBACK_WARN_RATE

logger = logging.getLogger(__name__)

#: MiD ``W_ZWD`` codes recorded as a loop: 710 Spaziergang, 711 Hund ausfuehren.
LOOP_CODES = frozenset({710, 711})

#: Modes a loop can have. A stroll by car is not a loop; a ride by bicycle without a destination
#: is (ASSUMPTION: same reading as the diary's "Spaziergang" by bicycle).
LOOP_MODES = frozenset({"walk", "bicycle"})

#: Offset added to ``random_seed`` for the loop draw's own stream; the next free one after
#: ``deciders.ESCORT_LOCATION_SEED_OFFSET`` (90214), so the loop draw cannot perturb the
#: subtype, escort or distance draws.
LOOP_SEED_OFFSET = 90215

#: Config key of the feature flag (declared by the stage wiring, not by this module).
KEY_ENABLED = "secondary_loop_zero_displacement"

#: Columns :func:`flag_loops` reads from the synthetic trips.
_TRIP_COLUMNS = ("person_id", "trip_index", "mode", "preceding_purpose", "following_purpose",
                 "travel_time", "W_ZWD", "W_ZWECK")

#: Columns :func:`estimate_loop_probabilities` reads from the MiD Wege table.
_MID_COLUMNS = ("H_ID", "P_ID", "W_ID", "W_ZWECK", "W_ZWD", "W_GEW", "hvm_imp")

_STAT_KEYS = ("in_scope", "by_label", "by_draw", "draws", "draws_from_marginal")


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


def _next_leg_is_home(frame: pd.DataFrame, group_columns: list, order_column: str) -> pd.Series:
    """True where the NEXT leg of the same person (by ``order_column``) ends at home.

    The last leg of a person has no next leg and gets False. Aligned to ``frame.index``, which must
    be unique.
    """
    if not frame.index.is_unique:
        raise ValueError("[secondary_loops] the trips frame needs a unique index.")
    ordered = frame.sort_values([*group_columns, order_column])
    next_purpose = ordered.groupby(group_columns, sort=False)["following_purpose"].shift(-1)
    return (next_purpose == "home").reindex(frame.index)


def way_home_follows(df_trips: pd.DataFrame) -> pd.Series:
    """Per synthetic trip: does the next trip of the same person (by ``trip_index``) go home?

    Needs ``person_id``, ``trip_index`` and ``following_purpose``. The last trip of a person is
    False. Aligned to ``df_trips.index``.
    """
    _require_columns(df_trips, ("person_id", "trip_index", "following_purpose"), "trips")
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
    ``by_draw``, ``draws`` and ``draws_from_marginal``, each a dict ``mode -> count`` over
    :data:`LOOP_MODES`:

    - ``in_scope``: legs of the scope in the module docstring;
    - ``by_label``: in-scope legs flagged True by their donor's label (710 or 711);
    - ``draws``: in-scope legs without a labelled code, each of which consumed one random number;
    - ``by_draw``: the ``draws`` legs flagged True;
    - ``draws_from_marginal``: the ``draws`` legs whose cell has no estimate of its own and took
      the mode's marginal.

    The draws are taken in ``(person_id, trip_index)`` order, one ``random.random_sample`` value
    per drawing leg, so the result depends on the seed and the legs, not on the frame's row order.
    Rows out of scope and labelled rows consume no draw. Raises ``ValueError`` when a drawing leg's
    mode has no marginal (no silent default probability).
    """
    _require_columns(df_trips, _TRIP_COLUMNS, "trips")

    in_scope = ((df_trips["following_purpose"] == "leisure")
                & df_trips["mode"].isin(LOOP_MODES)
                & df_trips["W_ZWECK"].isin(LEISURE_ZWECK)
                & df_trips["W_ZWD"].notna())
    labelled = in_scope & df_trips["W_ZWD"].isin(probabilities.labelled_codes)
    by_label = labelled & df_trips["W_ZWD"].isin(LOOP_CODES)
    to_draw = in_scope & ~labelled

    flags = by_label.copy()
    by_draw = pd.Series(False, index=df_trips.index)
    from_marginal = pd.Series(False, index=df_trips.index)

    if bool(to_draw.any()):
        drawing = df_trips[to_draw].sort_values(["person_id", "trip_index"])
        starts_at_home = (drawing["preceding_purpose"] == "home")
        way_home = way_home_follows(df_trips).loc[drawing.index]
        uniform = random.random_sample(len(drawing))

        probability = np.empty(len(drawing), dtype=float)
        used_marginal = np.zeros(len(drawing), dtype=bool)
        for position, (mode, travel_time, at_home, home_follows) in enumerate(zip(
                drawing["mode"], drawing["travel_time"], starts_at_home, way_home)):
            key = (mode, tt_band(travel_time), bool(at_home), bool(home_follows))
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
        drawn_flag = uniform < probability
        flags.loc[drawing.index] = drawn_flag
        by_draw.loc[drawing.index] = drawn_flag
        from_marginal.loc[drawing.index] = used_marginal

    stats = {
        "in_scope": _per_mode(df_trips, in_scope),
        "by_label": _per_mode(df_trips, by_label),
        "by_draw": _per_mode(df_trips, by_draw),
        "draws": _per_mode(df_trips, to_draw),
        "draws_from_marginal": _per_mode(df_trips, from_marginal),
    }
    _log_stats(stats)
    return flags.astype(bool), stats


def _per_mode(df_trips: pd.DataFrame, mask: pd.Series) -> dict:
    return {mode: int((mask & (df_trips["mode"] == mode)).sum()) for mode in sorted(LOOP_MODES)}


def _log_stats(stats: dict) -> None:
    """Log the flag sources per mode with rates; a high marginal share is a WARNING.

    Fallback transparency (CLAUDE.md): the primary path of a drawing leg is its own cell; the
    marginal is the fallback, and its rate is logged against ``FALLBACK_WARN_RATE``.
    """
    for mode in sorted(LOOP_MODES):
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
