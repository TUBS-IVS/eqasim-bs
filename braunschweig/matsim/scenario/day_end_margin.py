"""Leave a margin between the last planned arrival and the end of the simulated day (eqasim-bs#465, mechanism 2).

The synthesis bounds every plan by the simulated day (``braunschweig.popsim.plan_validation.MAX_PLAN_TIME_SECONDS``,
30 h), but it measures the last arrival with the survey (or constructed) travel times and allows it to end exactly at
the bound. MATSim routes the trips and they meet congestion and transfer waits, so a day that ends just before 30 h by
its survey times is still on the road when the simulation ends: in run ``i465-wpe-smoke-1pct-2026-10-23`` all 20
residents stuck at the end in a car or as car passenger were night-shift-like plans whose routed last arrival was
29.96-30.54 h.

:func:`shift_late_days` moves every day whose last planned arrival is later than ``bound_s - margin_s`` earlier AS A
WHOLE: every activity start and end and every trip departure and arrival of the person by the same whole number of
seconds, so every activity and trip duration is kept (the departure time model treats the durations as its hold-out
dimension; shortening the last activity would change them). A day cannot move before 0 s: when its first departure
does not leave room for the full shift it moves as far as it can and is counted as unresolved (WARNING). The function
is applied by the population writer (``braunschweig.matsim.scenario.population``) after the in-commuter merge, so
residents, portal trips and both in-commuter sources are all covered by one rule.

The margin is an ASSUMPTION (``KEY_LAST_ARRIVAL_MARGIN_S``, default 3600 s): the largest excess of a routed last
arrival over 30 h among the stuck car residents of the run above was 0.54 h, and congestion comes on top. It is a
technical margin, not a behavioural reference value.
"""
from __future__ import annotations

import logging
import math

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_LOG_TAG = "[day end margin]"

KEY_ENABLED = "braunschweig.day_end_margin.enabled"
DEFAULT_ENABLED = True
#: ASSUMPTION (see the module docstring): seconds the last planned arrival must stay before the end of the simulated
#: day. Valid range [0, bound).
KEY_LAST_ARRIVAL_MARGIN_S = "braunschweig.day_end_margin.last_arrival_margin_s"
DEFAULT_LAST_ARRIVAL_MARGIN_S = 3600.0

_TRIP_TIME_COLUMNS = ("departure_time", "arrival_time")
_ACTIVITY_TIME_COLUMNS = ("start_time", "end_time")


def check_margin(margin_s, bound_s) -> float:
    """Return the margin as a float; raise ValueError unless it is a finite number of seconds in [0, bound_s)."""
    if isinstance(margin_s, bool) or not isinstance(margin_s, (int, float)) or not math.isfinite(margin_s) \
            or not 0.0 <= margin_s < bound_s:
        raise ValueError(f"{_LOG_TAG} {KEY_LAST_ARRIVAL_MARGIN_S} must be a finite number of seconds in "
                         f"[0, {bound_s:g}) (the end of the simulated day), got {margin_s!r}")
    return float(margin_s)


def late_day_shift_s(first_departure_s, last_arrival_s, *, target_s: float):
    """Per day: the whole-second shift (s, >= 0) that moves it earlier so it ends by ``target_s``, limited by its
    first departure (no time before 0 s); also whether it was late and whether the shift falls short (unresolved).

    The rule of :func:`shift_late_days`, on arrays, so that a synthesis stage that must check its days at their
    written times (``braunschweig.synthesis.incommuters``, eqasim-bs#488) applies exactly the same move.
    """
    first_departure_s = np.asarray(first_departure_s, dtype=float)
    excess = np.ceil(np.asarray(last_arrival_s, dtype=float) - target_s)
    late = excess > 0
    room = np.floor(np.clip(first_departure_s, 0.0, None))
    shift = np.where(late, np.minimum(excess, room), 0.0)
    return shift, late, late & (shift < excess)


def shift_late_days(activities: pd.DataFrame, trips: pd.DataFrame, *, bound_s: float,
                    margin_s: float) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Move every day whose last arrival is after ``bound_s - margin_s`` earlier as a whole; return the report.

    ``trips`` needs ``person_id``, ``departure_time`` and ``arrival_time`` (s), ``activities`` ``person_id``,
    ``start_time`` and ``end_time`` (s, NaN where undefined). The shift of a person is the excess of its last arrival
    rounded up to a whole second, limited by its first departure (no time before 0 s); persons without trips are left
    alone. Returns copies of both frames and a report: persons, late persons, shifted persons, unresolved persons
    (moved less than their excess), mean and maximum applied shift (s), the bound and the margin.
    """
    margin_s = check_margin(margin_s, bound_s)
    per_person = trips.groupby("person_id").agg(first_departure=("departure_time", "min"),
                                                last_arrival=("arrival_time", "max"))
    shift_s, late, unresolved = late_day_shift_s(per_person["first_departure"].to_numpy(dtype=float),
                                                 per_person["last_arrival"].to_numpy(dtype=float),
                                                 target_s=float(bound_s) - margin_s)
    shift = pd.Series(shift_s, index=per_person.index)
    late, unresolved = pd.Series(late, index=per_person.index), pd.Series(unresolved, index=per_person.index)

    activities = activities.copy()
    trips = trips.copy()
    shifted = shift[shift > 0]
    if len(shifted):
        trip_shift = trips["person_id"].map(shifted).fillna(0.0).to_numpy()
        for column in _TRIP_TIME_COLUMNS:
            trips[column] = trips[column] - trip_shift
        activity_shift = activities["person_id"].map(shifted).fillna(0.0).to_numpy()
        for column in _ACTIVITY_TIME_COLUMNS:
            activities[column] = activities[column] - activity_shift   # NaN stays NaN

    report = {"n_persons": int(len(per_person)), "n_late": int(late.sum()), "n_shifted": int(len(shifted)),
              "n_unresolved": int(unresolved.sum()),
              "shift_mean_s": float(shifted.mean()) if len(shifted) else 0.0,
              "shift_max_s": float(shifted.max()) if len(shifted) else 0.0,
              "bound_s": float(bound_s), "margin_s": margin_s}
    return activities, trips, report


def log_report(report: dict) -> None:
    """One line with the rates; WARNING when a day could not move far enough (unresolved)."""
    n = report["n_persons"]
    message = ("%s last arrival after %.2f h (end of the simulated day %.2f h minus the margin %.0f s): %d of %d persons "
               "(%.2f%%); moved earlier as a whole day (durations kept): %d, mean shift %.0f s, max %.0f s; unresolved "
               "(the first departure leaves no room for the full shift): %d")
    arguments = (_LOG_TAG, (report["bound_s"] - report["margin_s"]) / 3600.0, report["bound_s"] / 3600.0,
                 report["margin_s"], report["n_late"], n, 100.0 * report["n_late"] / n if n else 0.0,
                 report["n_shifted"], report["shift_mean_s"], report["shift_max_s"], report["n_unresolved"])
    if report["n_unresolved"]:
        logger.warning(message, *arguments)
    else:
        logger.info(message, *arguments)
