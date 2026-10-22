"""Resolution of pt portal stays without an admissible timetable-aware gate (eqasim-bs#465).

A pt stay whose ``choose_pt_gates`` result has no gate is resolved by a counted chain, in this order
(owner principle: fix plan generation realistically, never make the simulation more tolerant):

1. ``tour_to_car``: every leg of the stay's home-based tour becomes ``car`` (needs ``can_car``);
2. ``tour_to_car_passenger``: the same with ``car_passenger`` (needs ``can_car_passenger``);
3. ``departure_shifted``: the stay's own legs move to the first grid time ``t + k * shift_step_s``
   (``k >= 1``, at most ``t + shift_window_s``) at which the probe finds a gate. Variants per step, in
   preference order outbound only, return only, both (a design call: the "both" variant moves two legs of
   the stay, each within its own bounds with the stay and return durations unchanged, so it is tried
   only when neither leg alone yields a gate at that step). An outbound shift is allowed only while
   ``min_stay_s`` remains between the shifted outbound arrival and the return departure (without a
   return leg: while the arrival stays within ``bound_s``); a return shift only while the shifted return
   arrival stays within ``bound_s`` and before the departure of the person's next trip;
4. ``kept_no_connection``: the plan is kept, the stay takes its gate from ``fallback_gates`` and is
   counted (the stage warns above a configurable share).

``gate_found`` stays are untouched. A whole-tour switch also switches every other stay of the same
home-based tour (its modes row changes; ``choose_gates`` later places it at a road gate), whatever its
current mode, so that the modes table and the legs of the tour agree; such a stay is counted under the
switch label even when it had a gate, so ``gate_found`` counts only the stays whose tour was NOT switched.
Invalid shift parameters raise ``ValueError`` (a non-positive step would silently disable the shift path). The probe is called once per grid
step for all unresolved stays, never per stay. All actions are logged as rates over the pt stays.
"""
from __future__ import annotations

import logging
from typing import Callable

import numpy as np
import pandas as pd

from braunschweig.synthesis.portal_trips.gates import PT_GATE_COLUMNS
from braunschweig.synthesis.portal_trips.modes import MODE_COLUMNS

logger = logging.getLogger(__name__)

ACTION_GATE_FOUND = "gate_found"
ACTION_TOUR_TO_CAR = "tour_to_car"
ACTION_TOUR_TO_CAR_PASSENGER = "tour_to_car_passenger"
ACTION_DEPARTURE_SHIFTED = "departure_shifted"
ACTION_KEPT = "kept_no_connection"
ACTIONS = (ACTION_GATE_FOUND, ACTION_TOUR_TO_CAR, ACTION_TOUR_TO_CAR_PASSENGER, ACTION_DEPARTURE_SHIFTED,
           ACTION_KEPT)
REASON_NO_PT_CONNECTION = "no_pt_connection"
HOME = "home"


def _tour_of(own: pd.DataFrame, person_id, trip_index) -> np.ndarray:
    """``tour_trip_indices`` on the already sorted trips of ONE person."""
    index = own["trip_index"].to_numpy()
    if trip_index not in index:
        raise ValueError(f"[portal_trips] person {person_id} has no trip {trip_index}")
    starts = index[(index <= trip_index) & (own["preceding_purpose"].to_numpy() == HOME)]
    ends = index[(index >= trip_index) & (own["following_purpose"].to_numpy() == HOME)]
    start = starts.max() if len(starts) else index.min()
    end = ends.min() if len(ends) else index.max()
    return index[(index >= start) & (index <= end)]


def tour_trip_indices(trips: pd.DataFrame, person_id, trip_index) -> np.ndarray:
    """Trip indices of the home-based tour containing the trip.

    From the last trip with ``preceding_purpose == "home"`` at or before ``trip_index`` to the next trip
    with ``following_purpose == "home"`` at or after it (the first / last trip of the person when no such
    trip exists).
    """
    return _tour_of(trips[trips["person_id"] == person_id].sort_values("trip_index"), person_id, trip_index)


def resolve_no_connection(stays: pd.DataFrame, trips: pd.DataFrame, modes: pd.DataFrame,
                          availability: pd.DataFrame, pt_gates: pd.DataFrame,
                          probe: Callable[[np.ndarray, np.ndarray, np.ndarray], pd.DataFrame], *,
                          fallback_gates: pd.DataFrame, shift_window_s: float, shift_step_s: float,
                          min_stay_s: float, bound_s: float) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Resolve the pt stays without a gate; return ``(modes_out, trips_out, pt_gates_out)``.

    ``pt_gates`` and ``fallback_gates`` hold one row per pt stay (``modes["mode"] == "pt"``) in stays order
    (``fallback_gates`` needs ``gate_id``, ``x``, ``y``). ``probe(stay_positions, outbound_departure_s,
    return_departure_s)`` re-runs ``choose_pt_gates`` for the pt stays at the given positions (positions
    index the pt stays; duplicates allowed) with the given trip departures (NaN return departure for stays
    without a return leg) and returns ``PT_GATE_COLUMNS`` rows in that order. ``modes_out`` is ``modes``
    plus ``no_connection_action`` (None for stays that are not pt); ``pt_gates_out`` has one row per stay
    that is still pt.
    """
    if not shift_step_s > 0 or not shift_window_s >= 0 or not min_stay_s >= 0 or not np.isfinite(bound_s):
        raise ValueError(f"[portal_trips] invalid shift parameters: shift_step_s={shift_step_s} (must be > 0), "
                         f"shift_window_s={shift_window_s} (>= 0), min_stay_s={min_stay_s} (>= 0), "
                         f"bound_s={bound_s} (finite)")
    if len(modes) != len(stays):
        raise ValueError(f"[portal_trips] modes ({len(modes)} rows) is not aligned with stays ({len(stays)} rows)")
    stays = stays.reset_index(drop=True)
    modes_out = modes.reset_index(drop=True).copy()
    pt_rows = np.flatnonzero((modes_out["mode"] == "pt").to_numpy())
    n_pt = len(pt_rows)
    for name, frame in (("pt_gates", pt_gates), ("fallback_gates", fallback_gates)):
        if len(frame) != n_pt:
            raise ValueError(f"[portal_trips] {name} has {len(frame)} rows but there are {n_pt} pt stays")
    missing_cols = [c for c in ("gate_id", "x", "y") if c not in fallback_gates.columns]
    if missing_cols:
        raise ValueError(f"[portal_trips] fallback_gates lacks the columns {missing_cols}")
    persons = stays["person_id"].to_numpy()
    unknown = sorted(set(persons[pt_rows]) - set(availability.index))
    if unknown:
        raise ValueError(f"[portal_trips] {len(unknown)} pt stay persons have no availability row "
                         f"(first: {unknown[:5]})")
    pt_gates = pt_gates.reset_index(drop=True)
    fallback_gates = fallback_gates.reset_index(drop=True)
    trips_out = trips.copy()
    modes_out["no_connection_action"] = None
    trips_out["departure_time"] = trips_out["departure_time"].astype(float)
    trips_out["arrival_time"] = trips_out["arrival_time"].astype(float)
    trips_out["mode"] = trips_out["mode"].astype(object)
    for column in ("mode", "outbound_mode", "return_mode", "substituted_from", "substitution_reason"):
        modes_out[column] = modes_out[column].astype(object)
    if n_pt == 0:
        return modes_out[MODE_COLUMNS + ["no_connection_action"]], trips_out, pt_gates.copy()

    trip_pos = {(p, t): i for i, (p, t) in enumerate(zip(trips_out["person_id"], trips_out["trip_index"]))}
    dep = trips_out["departure_time"].to_numpy().copy()
    arr = trips_out["arrival_time"].to_numpy().copy()
    pt_persons = persons[pt_rows]
    out_idx = stays["outbound_trip_index"].to_numpy()[pt_rows]
    has_ret = stays["return_trip_index"].notna().to_numpy()[pt_rows]
    ret_idx = stays["return_trip_index"].to_numpy()[pt_rows]
    out_pos = np.array([trip_pos[p, int(t)] for p, t in zip(pt_persons, out_idx)])
    ret_pos = np.array([trip_pos[p, int(t)] if h else -1 for p, t, h in zip(pt_persons, ret_idx, has_ret)])
    # Per person, once: the sorted trips (tours) and the stay rows (tour switch).
    sorted_trips = {p: g.sort_values("trip_index") for p, g in trips.groupby("person_id", sort=False)}
    stay_rows = {}
    for row, p in enumerate(persons):
        stay_rows.setdefault(p, []).append(row)

    def next_departure(person, trip):
        later = sorted_trips[person]["trip_index"].to_numpy()
        later = later[later > trip]
        return dep[trip_pos[person, int(later.min())]] if len(later) else np.inf

    nxt_dep = np.array([next_departure(p, t) if h else np.inf for p, t, h in zip(pt_persons, ret_idx, has_ret)])

    action = np.full(n_pt, None, dtype=object)
    action[pt_gates["gate_id"].notna().to_numpy()] = ACTION_GATE_FOUND
    gates_out = pt_gates.copy()
    switched = set()  # stay rows (in stays) whose tour mode was switched

    def switch_tour(stay_row: int, new_mode: str, label: str) -> None:
        person = persons[stay_row]
        tour = {int(i) for i in _tour_of(sorted_trips[person], person,
                                         int(stays["outbound_trip_index"].iat[stay_row]))}
        for row in stay_rows[person]:
            if row in switched:
                continue
            if int(stays["outbound_trip_index"].iat[row]) not in tour:
                continue
            old = modes_out["mode"].iat[row]
            if old == new_mode:
                continue
            switched.add(row)
            modes_out.loc[row, "mode"] = new_mode
            modes_out.loc[row, "outbound_mode"] = new_mode
            if pd.notna(stays["return_trip_index"].iat[row]):
                modes_out.loc[row, "return_mode"] = new_mode
            modes_out.loc[row, "substituted_from"] = old
            modes_out.loc[row, "substitution_reason"] = REASON_NO_PT_CONNECTION
            if old == "pt":
                action[int(np.flatnonzero(pt_rows == row)[0])] = label
        for t in tour:
            trips_out.iloc[trip_pos[person, t], trips_out.columns.get_loc("mode")] = new_mode

    for new_mode, flag, label in (("car", "can_car", ACTION_TOUR_TO_CAR),
                                  ("car_passenger", "can_car_passenger", ACTION_TOUR_TO_CAR_PASSENGER)):
        for k in range(n_pt):
            if action[k] is None and bool(availability.at[pt_persons[k], flag]):
                switch_tour(int(pt_rows[k]), new_mode, label)

    # Time shifts: the grid is walked once, one probe call per step for all unresolved stays.
    shifted = {}  # pt stay position -> (variant, shift in s, probe row)
    unresolved = [k for k in range(n_pt) if action[k] is None]
    n_steps = int(np.floor(shift_window_s / shift_step_s + 1e-9)) if shift_step_s > 0 else 0
    for step in range(1, n_steps + 1):
        if not unresolved:
            break
        d = step * shift_step_s
        positions, out_d, ret_d, variants = [], [], [], []
        for k in unresolved:
            o_dep, o_arr = dep[out_pos[k]], arr[out_pos[k]]
            if has_ret[k]:
                r_dep, r_arr = dep[ret_pos[k]], arr[ret_pos[k]]
                out_ok = o_arr + d + min_stay_s <= r_dep
                ret_ok = r_arr + d <= min(bound_s, nxt_dep[k])
                options = [("out", out_ok), ("ret", ret_ok), ("both", out_ok and ret_ok)]
            else:
                r_dep = np.nan
                options = [("out", o_arr + d <= bound_s)]
            for variant, allowed in options:
                if allowed:
                    positions.append(k)
                    out_d.append(o_dep + d if variant in ("out", "both") else o_dep)
                    ret_d.append(r_dep + d if variant in ("ret", "both") else r_dep)
                    variants.append(variant)
        if not positions:
            continue
        result = probe(np.array(positions, dtype=int), np.array(out_d, dtype=float), np.array(ret_d, dtype=float))
        if len(result) != len(positions):
            raise ValueError(f"[portal_trips] probe returned {len(result)} rows for {len(positions)} requests")
        result = result.reset_index(drop=True)
        for row, (k, variant) in enumerate(zip(positions, variants)):
            if action[k] is None and pd.notna(result["gate_id"].iat[row]):
                action[k] = ACTION_DEPARTURE_SHIFTED
                shifted[k] = (variant, d, result.iloc[row])
        unresolved = [k for k in unresolved if action[k] is None]

    for k, (variant, d, row) in shifted.items():
        for column in PT_GATE_COLUMNS:
            gates_out.at[k, column] = row[column]
        legs = ([out_pos[k]] if variant in ("out", "both") else []) + \
               ([ret_pos[k]] if variant in ("ret", "both") else [])
        for leg in legs:
            trips_out.iloc[leg, trips_out.columns.get_loc("departure_time")] += d
            trips_out.iloc[leg, trips_out.columns.get_loc("arrival_time")] += d
    for k in unresolved:
        action[k] = ACTION_KEPT
        for column in ("gate_id", "x", "y"):
            gates_out.at[k, column] = fallback_gates[column].iat[k]

    modes_out.loc[pt_rows, "no_connection_action"] = action
    still_pt = np.array([int(pt_rows[k]) not in switched for k in range(n_pt)])
    gates_out = gates_out[still_pt].reset_index(drop=True)
    counts = pd.Series(action).value_counts()
    logger.info("[portal_trips.no_connection] %d pt stays (gate_found = tour not switched): %s", n_pt,
                ", ".join(f"{a} {int(counts.get(a, 0))}/{n_pt} ({100.0 * counts.get(a, 0) / n_pt:.1f}%)"
                          for a in ACTIONS))
    return modes_out[MODE_COLUMNS + ["no_connection_action"]], trips_out, gates_out
