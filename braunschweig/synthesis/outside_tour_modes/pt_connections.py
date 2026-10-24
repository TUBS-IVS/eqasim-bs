"""Timetable check of the pt legs of outside tours and its counted resolution (eqasim-bs#465, ADR-0159).

An outside tour keeps its modes for the whole run (eqasim ``OutsideFilter``). Its pt legs, donor modes or SrV
substitutes (ADR-0145), were never checked against the timetable: a pt leg without a connection is replaced by a
direct walk in the prepare routing (``PlanRouter.fixOnlyWalk``), and the cutter cuts that walk at the extent. In the
1 % rerun of the #465 fixes 15 such legs of 14 residents walked 8-30 km, two of the residents stuck at the end of
the run among them.

:func:`resolve_pt_tours` probes every pt leg of an outside tour WITHOUT a portal leg (portal stays have their own
check, ADR-0152/0156) at its departure time. A tour whose pt legs are not all connections is resolved by a counted
chain, in this order (owner order of ADR-0153 and ADR-0156). First, as for the in-commuters (ADR-0153), the day
of every person with a checked tour that ends after ``bound_s`` moves earlier as a whole by the population writer's
rule (only while that rule is on, ``day_end_shift``) (``day_end_margin.late_day_shift_s``), so the timetable is asked at the times that are finally written and the
writer does not move a checked day again (counted as ``n_persons_day_end_shifted``).

1. ``tour_shifted``: the WHOLE tour moves by the first offset of
   ``incommuter_pt_connections.shift_offsets_s`` (earlier first) at which every pt leg is a connection. The tour's
   first departure stays strictly after the arrival of the person's previous trip (at or after 0 s for the first
   trip), its last arrival strictly before the departure of the person's next trip and at or before ``bound_s``,
   so a shift never leaves a zero-duration activity next to the tour. One probe call per offset.
2. ``tour_to_car`` (needs ``can_car``), then ``tour_to_car_passenger`` (needs ``can_car_passenger``): every leg of
   the tour switches.
3. ``tour_to_car_passenger_lift`` (``last_resort`` ``LAST_RESORT_LIFT``): the tour becomes ``car_passenger`` WITHOUT
   the passenger availability (ASSUMPTION, as ADR-0156: a trip no timetable serves is made as a lift or a taxi, not
   as a walk of tens of kilometres); ``kept_no_connection`` (``LAST_RESORT_KEEP``): kept, counted and warned about.

``connection_found`` tours are untouched. All actions are counted and logged as rates over the checked tours.
"""
from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable

import numpy as np
import pandas as pd

from braunschweig.matsim.scenario.day_end_margin import late_day_shift_s
from braunschweig.synthesis.incommuter_pt_connections import shift_offsets_s

logger = logging.getLogger(__name__)

_LOG_TAG = "[outside_tour_modes.pt_connections]"

ACTION_CONNECTION_FOUND = "connection_found"
ACTION_TOUR_SHIFTED = "tour_shifted"
ACTION_TOUR_TO_CAR = "tour_to_car"
ACTION_TOUR_TO_CAR_PASSENGER = "tour_to_car_passenger"
ACTION_TOUR_TO_CAR_PASSENGER_LIFT = "tour_to_car_passenger_lift"
ACTION_KEPT = "kept_no_connection"
ACTIONS = (ACTION_CONNECTION_FOUND, ACTION_TOUR_SHIFTED, ACTION_TOUR_TO_CAR, ACTION_TOUR_TO_CAR_PASSENGER,
           ACTION_TOUR_TO_CAR_PASSENGER_LIFT, ACTION_KEPT)
#: The actions that resolve a tour without a timetable connection by a mode switch or by keeping it.
UNSERVED_ACTIONS = (ACTION_TOUR_TO_CAR, ACTION_TOUR_TO_CAR_PASSENGER, ACTION_TOUR_TO_CAR_PASSENGER_LIFT, ACTION_KEPT)
#: What happens to a tour that nothing else resolves (``last_resort``); the same values as the portal stage.
LAST_RESORT_KEEP = "keep"
LAST_RESORT_LIFT = "car_passenger"
LAST_RESORTS = (LAST_RESORT_KEEP, LAST_RESORT_LIFT)
PT_MODE = "pt"
TOUR_ACTION_COLUMNS = ["person_id", "tour", "action", "offset_s", "n_pt_legs"]

#: A probe maps a task frame (``braunschweig.data.transit.connections.TASK_COLUMNS``) to a boolean Series indexed by
#: the task identifier: is the routed trip a connection?
Probe = Callable[[pd.DataFrame], pd.Series]


@dataclasses.dataclass(frozen=True)
class PtCheck:
    """What ``stage.remode_outside_tours`` needs to check the pt legs: the probe and the chain parameters.

    ``bound_s`` is the latest allowed last arrival (s): the end of the simulated day minus the day-end margin of the
    population writer (ADR-0154). A checked person's day that ends later first moves earlier by the writer's rule,
    and a shifted tour stays within it, so the writer does not move a checked day again.
    """

    probe: Probe
    shift_window_s: float = 7200.0
    shift_step_s: float = 1800.0
    bound_s: float = 29.0 * 3600.0
    warn_share: float = 0.05
    #: Apply the writer's day-end rule before probing; False when ``braunschweig.day_end_margin.enabled`` is false,
    #: because the writer then moves no day either.
    day_end_shift: bool = True


@dataclasses.dataclass
class Resolution:
    trips: pd.DataFrame          # the input frame (same index and order) with the resolved modes and times
    tour_actions: pd.DataFrame   # one row per checked tour (TOUR_ACTION_COLUMNS), in person_id, tour order
    report: dict


def _tasks(positions, trips_xy, departure_s, offset_s) -> pd.DataFrame:
    """One router task per pt leg; the identifier is the leg's row position in the sorted frame."""
    return pd.DataFrame({"identifier": [str(int(p)) for p in positions],
                         "origin_x": trips_xy[positions, 0], "origin_y": trips_xy[positions, 1],
                         "destination_x": trips_xy[positions, 2], "destination_y": trips_xy[positions, 3],
                         "departure_time": departure_s[positions] + offset_s})


def _connected(probe: Probe, tasks: pd.DataFrame) -> dict:
    answer = probe(tasks)
    missing = set(tasks["identifier"]) - set(answer.index.astype(str))
    if missing:
        raise ValueError(f"{_LOG_TAG} the probe answered no result for {len(missing)} task(s), e.g. "
                         f"{sorted(missing)[:3]}")
    answer.index = answer.index.astype(str)
    return {int(identifier): bool(answer[identifier]) for identifier in tasks["identifier"]}


def resolve_pt_tours(*, trips: pd.DataFrame, tour_ids: np.ndarray, outside: np.ndarray, portal_tour: np.ndarray,
                     endpoints: pd.DataFrame, availability: pd.DataFrame, probe: Probe, shift_window_s: float,
                     shift_step_s: float, bound_s: float, last_resort: str = LAST_RESORT_LIFT,
                     day_end_shift: bool = True) -> Resolution:
    """Check and resolve the pt legs of the outside tours; see the module docstring. Pure apart from the probe.

    ``trips`` must be sorted by ``person_id, trip_index`` (``tours.require_sorted``); ``tour_ids``, ``outside``
    (leg belongs to an outside tour), ``portal_tour`` (leg belongs to a tour with a portal leg) and ``endpoints``
    (``ox, oy, dx, dy`` in the metric pipeline CRS) are aligned with its rows. ``availability`` is indexed by
    ``person_id`` with the bool columns ``can_car`` and ``can_car_passenger``. Times are seconds.
    """
    if last_resort not in LAST_RESORTS:
        raise ValueError(f"{_LOG_TAG} invalid last_resort {last_resort!r}; allowed: {list(LAST_RESORTS)}")
    if not np.isfinite(bound_s):
        raise ValueError(f"{_LOG_TAG} bound_s must be finite, got {bound_s!r}")
    offsets = shift_offsets_s(shift_window_s, shift_step_s)
    n = len(trips)
    for name, values in (("tour_ids", tour_ids), ("outside", outside), ("portal_tour", portal_tour),
                         ("endpoints", endpoints)):
        if len(values) != n:
            raise ValueError(f"{_LOG_TAG} {name} has {len(values)} rows for {n} legs")
    persons = trips["person_id"].to_numpy()
    tours = np.asarray(tour_ids)
    modes = trips["mode"].astype(object).to_numpy().copy()
    departure = trips["departure_time"].to_numpy(dtype=float).copy()
    arrival = trips["arrival_time"].to_numpy(dtype=float).copy()
    xy = endpoints[["ox", "oy", "dx", "dy"]].to_numpy(dtype=float)

    candidate = np.asarray(outside, dtype=bool) & ~np.asarray(portal_tour, dtype=bool)
    is_pt = modes == PT_MODE
    frame = pd.DataFrame({"person_id": persons, "tour": tours, "position": np.arange(n), "pt": is_pt})
    frame = frame[candidate]
    has_pt = frame.groupby(["person_id", "tour"], sort=False)["pt"].transform("any").to_numpy(dtype=bool)
    frame = frame[has_pt]
    # Per checked tour: all leg positions (contiguous in the sorted frame) and the pt leg positions.
    checked = []
    for (person_id, tour), legs in frame.groupby(["person_id", "tour"], sort=True):
        positions = legs["position"].to_numpy()
        checked.append({"person_id": person_id, "tour": int(tour), "legs": positions,
                        "pt_legs": positions[legs["pt"].to_numpy(dtype=bool)]})
    unknown = sorted({t["person_id"] for t in checked} - set(availability.index))
    if unknown:
        raise ValueError(f"{_LOG_TAG} {len(unknown)} persons of checked tours have no availability row "
                         f"(first: {unknown[:5]})")

    # The writer's day-end rule first (ADR-0153 decision 2): a checked person's whole day moves earlier when it ends
    # after bound_s, so every probe below asks the timetable at the times that are finally written. Without the
    # writer's rule (day_end_shift False) no day is moved here either.
    n_day_end_shifted = 0
    if checked and day_end_shift:
        checked_persons = {t["person_id"] for t in checked}
        rows = np.flatnonzero(np.isin(persons, list(checked_persons)))
        days = pd.DataFrame({"person_id": persons[rows], "departure": departure[rows], "arrival": arrival[rows]})
        per_person = days.groupby("person_id", sort=False).agg(first=("departure", "min"), last=("arrival", "max"))
        move_s, _late, _unresolved = late_day_shift_s(per_person["first"].to_numpy(), per_person["last"].to_numpy(),
                                                      target_s=float(bound_s))
        move_by_person = dict(zip(per_person.index, move_s))
        moves = np.array([move_by_person[p] for p in persons[rows]], dtype=float)
        departure[rows] -= moves
        arrival[rows] -= moves
        n_day_end_shifted = int((move_s > 0).sum())

    actions = [None] * len(checked)
    applied = np.zeros(len(checked), dtype=float)
    n_legs_probed = 0
    if checked:
        pt_positions = np.concatenate([t["pt_legs"] for t in checked])
        n_legs_probed = len(pt_positions)
        served = _connected(probe, _tasks(pt_positions, xy, departure, 0.0))
        for k, tour in enumerate(checked):
            if all(served[int(p)] for p in tour["pt_legs"]):
                actions[k] = ACTION_CONNECTION_FOUND

    def fits(tour, offset) -> bool:
        """Does the tour, moved by ``offset``, keep a positive stay before and after it (CURRENT neighbour times)?"""
        first, last = int(tour["legs"][0]), int(tour["legs"][-1])
        new_departure, new_arrival = departure[first] + offset, arrival[last] + offset
        if first > 0 and persons[first - 1] == persons[first]:
            if not new_departure > arrival[first - 1]:
                return False
        elif new_departure < 0.0:
            return False
        if last + 1 < n and persons[last + 1] == persons[last] and not new_arrival < departure[last + 1]:
            return False
        return new_arrival <= bound_s

    unresolved = [k for k in range(len(checked)) if actions[k] is None]
    for offset in offsets:
        if not unresolved:
            break
        candidates = [k for k in unresolved if fits(checked[k], offset)]
        if not candidates:
            continue
        positions = np.concatenate([checked[k]["pt_legs"] for k in candidates])
        served = _connected(probe, _tasks(positions, xy, departure, offset))
        for k in candidates:
            if all(served[int(p)] for p in checked[k]["pt_legs"]):
                actions[k] = ACTION_TOUR_SHIFTED
                applied[k] = offset
                legs = checked[k]["legs"]
                departure[legs] += offset  # later offsets bound the person's other tours against the moved tour
                arrival[legs] += offset
        unresolved = [k for k in unresolved if actions[k] is None]

    for k in unresolved:
        person_id = checked[k]["person_id"]
        if bool(availability.at[person_id, "can_car"]):
            actions[k], new_mode = ACTION_TOUR_TO_CAR, "car"
        elif bool(availability.at[person_id, "can_car_passenger"]):
            actions[k], new_mode = ACTION_TOUR_TO_CAR_PASSENGER, "car_passenger"
        elif last_resort == LAST_RESORT_LIFT:
            actions[k], new_mode = ACTION_TOUR_TO_CAR_PASSENGER_LIFT, "car_passenger"
        else:
            actions[k], new_mode = ACTION_KEPT, None
        if new_mode is not None:
            modes[checked[k]["legs"]] = new_mode

    out = trips.copy()
    out["mode"] = modes
    if (departure != trips["departure_time"].to_numpy(dtype=float)).any():
        out["departure_time"] = departure
        out["arrival_time"] = arrival
    tour_actions = pd.DataFrame({"person_id": [t["person_id"] for t in checked],
                                 "tour": [t["tour"] for t in checked], "action": actions, "offset_s": applied,
                                 "n_pt_legs": [len(t["pt_legs"]) for t in checked]}, columns=TOUR_ACTION_COLUMNS)
    actions_array = np.array(actions, dtype=object)
    shifted = actions_array == ACTION_TOUR_SHIFTED
    report = {"n_tours_checked": len(checked), "n_legs_probed": int(n_legs_probed),
              "n_persons_day_end_shifted": n_day_end_shifted,
              "actions": {action: int((actions_array == action).sum()) for action in ACTIONS},
              "shift_abs_s_median": float(np.median(np.abs(applied[shifted]))) if shifted.any() else None,
              "shift_window_s": float(shift_window_s), "shift_step_s": float(shift_step_s),
              "bound_s": float(bound_s), "last_resort": last_resort}
    return Resolution(trips=out, tour_actions=tour_actions, report=report)


def log_report(report: dict, warn_share: float) -> None:
    """One line with the rates over the checked tours; WARNING when a tour is kept or the switched, lifted and kept
    tours together exceed ``warn_share``."""
    n = report["n_tours_checked"]
    counts = report["actions"]
    unserved = sum(counts[action] for action in UNSERVED_ACTIONS)
    message = "%s %d outside tours with pt legs (%d legs probed): %s; median shift %s s" % (
        _LOG_TAG, n, report["n_legs_probed"],
        ", ".join("%s %d (%.1f%%)" % (a, counts[a], 100.0 * counts[a] / n if n else 0.0) for a in ACTIONS),
        "n/a" if report["shift_abs_s_median"] is None else "%.0f" % report["shift_abs_s_median"])
    if counts[ACTION_KEPT] or (n and unserved / n > warn_share):
        logger.warning("%s -- %.1f%% of the tours have no timetable connection within the shift window (warn share "
                       "%.1f%%); %s %d are routed as walks", message, 100.0 * unserved / n if n else 0.0,
                       100.0 * warn_share, ACTION_KEPT, counts[ACTION_KEPT])
    else:
        logger.info(message)
