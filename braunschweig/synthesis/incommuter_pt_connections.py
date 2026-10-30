"""Timetable check of the in-commuters' pt days and its counted resolution (eqasim-bs#488).

The in-commuters of ``braunschweig.synthesis.incommuters`` get their mode from the Mikrozensus reference and their
times from survey donors; nothing checked whether the timetable serves a pt in-commuter's trips at those times. In run
``i465-wpe-smoke-1pct-2026-10-23`` 22 of about 91 pt in-commuters had a trip that the router realised as a walk over
5 km (median 52 km, mostly the return from work at 16-23 h), and 10 of them were stuck walking at the end of the
simulated day.

:func:`resolve_pt_days` checks the two trips of every pt in-commuter's day (home -> work at the home departure, work ->
home at the work departure) with a ``probe`` (in the stage: the batch pt router of
``braunschweig.data.transit.connections`` plus its connection rule). A day whose trips are not both connections is
resolved by a counted chain, in this order (owner decision for #488: the departure first):

1. ``day_shifted``: the WHOLE day moves by the first offset of ``shift_offsets_s`` (earlier first, then later, in
   steps of ``shift_step_s`` up to ``shift_window_s``) at which both trips are connections; the work duration and both
   trip durations are kept. A shift never moves the home departure before 0 s or the return arrival after
   ``bound_s``. The probe is called once per offset for all unresolved days.
2. ``station_redrawn`` (#467): no offset works and the in-commuter boards at a drawn entry station (a far agent, not
   a real in-ring origin): the caller passes ``station_candidates``, the other eligible stations of its source Kreis
   in descending selection weight (``weight_entry_stations``, the weighting of the draw), each with the home
   departure recomputed for that station's access distance. Both trips of every candidate of every such day are
   probed in ONE call at the UNSHIFTED times (the candidate's home departure, the donor work departure); the first
   candidate in weight order with both trips connected wins, and the caller moves the home to it.
3. ``to_car``: neither an offset nor another station works: the in-commuter drives (``car_allowed``; the stage
   boards it at its road gate, the path the synthesis already uses for pt in-commuters without a station).
4. ``kept_no_connection``: only when car is not an allowed commute mode; the router will realise the trip as a walk,
   so this is counted and warned about.

``connection_found`` days are untouched. All actions are counted and logged as rates over the pt days.

:func:`check_pt_days` is the entry point of the stages: the employee in-commuters of
``braunschweig.synthesis.incommuters`` (#488, with the station re-draw) and the student in-commuters of
``braunschweig.synthesis.student_incommuters`` (#528; they board at no drawn station, so steps 1, 3 and 4 apply).
"""
from __future__ import annotations

import dataclasses
import logging
from collections.abc import Callable

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

_LOG_TAG = "[incommuters.pt_connections]"

ACTION_CONNECTION_FOUND = "connection_found"
ACTION_DAY_SHIFTED = "day_shifted"
ACTION_STATION_REDRAWN = "station_redrawn"
ACTION_TO_CAR = "to_car"
ACTION_KEPT = "kept_no_connection"
ACTIONS = (ACTION_CONNECTION_FOUND, ACTION_DAY_SHIFTED, ACTION_STATION_REDRAWN, ACTION_TO_CAR, ACTION_KEPT)

KEY_ENABLED = "braunschweig.incommuters.pt_connection_check.enabled"
DEFAULT_ENABLED = True
#: ASSUMPTION (#488): how far (s) a pt in-commuter's day may move to reach a timetable connection, and in which steps.
#: The same 2 h / 30 min as the departure shift of the portal pt stays (braunschweig.portal.shift_window_s /
#: shift_step_s, ADR-0152); not calibrated.
KEY_SHIFT_WINDOW_S = "braunschweig.incommuters.pt_shift_window_s"
DEFAULT_SHIFT_WINDOW_S = 7200.0
KEY_SHIFT_STEP_S = "braunschweig.incommuters.pt_shift_step_s"
DEFAULT_SHIFT_STEP_S = 1800.0
#: ASSUMPTION (#467): how many entry stations of its source Kreis a pt in-commuter may try in total (the drawn one
#: plus at most this number minus one alternatives in weight order) before it drives; 1 disables the re-draw. Not
#: calibrated: it bounds the router batch (two tasks per alternative) and keeps the station close to the weighting.
KEY_STATION_CANDIDATES = "braunschweig.incommuters.pt_station_candidates"
DEFAULT_STATION_CANDIDATES = 5
#: WARN when more than this share of the pt days is neither served as planned nor by a shift or another station
#: (to_car + kept), or when more than this share needed another station (a sign that the station weighting or the
#: schedule extent is off).
KEY_WARN_SHARE = "braunschweig.incommuters.pt_no_connection_warn_share"
DEFAULT_WARN_SHARE = 0.05

#: A probe maps a task frame (``braunschweig.data.transit.connections.TASK_COLUMNS``) to a boolean Series indexed by
#: the task identifier: is the routed trip a connection?
Probe = Callable[[pd.DataFrame], pd.Series]


def validate_station_candidates(value) -> int:
    """``KEY_STATION_CANDIDATES`` as an int: an integral number >= 1 (1 = no station re-draw), else ValueError.

    A non-integral value (1.5) is rejected instead of being truncated by ``int()``."""
    try:
        as_float = float(value)
    except (TypeError, ValueError):
        as_float = float("nan")
    if isinstance(value, bool) or not np.isfinite(as_float) or as_float != int(as_float) or int(as_float) < 1:
        raise ValueError(f"{_LOG_TAG} {KEY_STATION_CANDIDATES} must be an integer >= 1 (1 = no station re-draw), "
                         f"got {value!r}")
    return int(as_float)


@dataclasses.dataclass(frozen=True)
class PtCheck:
    """What ``build_incommuter_frames`` needs to check the pt days: the probe and the chain parameters.

    ``bound_s`` is the latest allowed return arrival (s): the end of the simulated day minus the day-end margin of
    the population writer, so that the writer does not move a checked day again.
    """

    probe: Probe
    shift_window_s: float = DEFAULT_SHIFT_WINDOW_S
    shift_step_s: float = DEFAULT_SHIFT_STEP_S
    bound_s: float = 29.0 * 3600.0
    car_allowed: bool = True
    warn_share: float = DEFAULT_WARN_SHARE
    #: entry stations a pt in-commuter may try in total (drawn + alternatives); see KEY_STATION_CANDIDATES.
    station_candidates: int = DEFAULT_STATION_CANDIDATES

    def __post_init__(self):
        validate_station_candidates(self.station_candidates)


@dataclasses.dataclass(frozen=True)
class StationCandidate:
    """An alternative entry station of a pt in-commuter: its stop, coordinates (m, EPSG:25832) and the home
    departure (s) recomputed for its access distance, as ``_agent_times`` seeds it."""

    stop_id: str
    x: float
    y: float
    depart_home_s: float


@dataclasses.dataclass
class Resolution:
    actions: np.ndarray       # per agent: one of ACTIONS for a pt agent, None otherwise
    offsets_s: np.ndarray     # per agent: the applied day shift (s), 0 unless day_shifted
    report: dict
    stations: np.ndarray | None = None   # per agent: the chosen StationCandidate when station_redrawn, None otherwise


def shift_offsets_s(shift_window_s: float, shift_step_s: float) -> list[float]:
    """The day offsets tried in order: -step, +step, -2 step, +2 step, ... up to the window (earlier first)."""
    if not shift_step_s > 0 or not shift_window_s >= 0 or not np.isfinite(shift_window_s):
        raise ValueError(f"{_LOG_TAG} invalid shift parameters: shift_step_s={shift_step_s} (must be > 0), "
                         f"shift_window_s={shift_window_s} (finite, >= 0)")
    n_steps = int(np.floor(shift_window_s / shift_step_s + 1e-9))
    offsets = []
    for step in range(1, n_steps + 1):
        offsets.extend([-step * shift_step_s, step * shift_step_s])
    return offsets


def _tasks(person_ids, positions, home_xy, work_xy, depart_home_s, depart_work_s, offset_s) -> pd.DataFrame:
    """Two router tasks per day: ``<person>:<i>:out`` (home -> work) and ``<person>:<i>:ret`` (work -> home)."""
    rows = []
    for i in positions:
        prefix = f"{person_ids[i]}:{i}"
        rows.append((f"{prefix}:out", home_xy[i, 0], home_xy[i, 1], work_xy[i, 0], work_xy[i, 1],
                     depart_home_s[i] + offset_s))
        rows.append((f"{prefix}:ret", work_xy[i, 0], work_xy[i, 1], home_xy[i, 0], home_xy[i, 1],
                     depart_work_s[i] + offset_s))
    return pd.DataFrame(rows, columns=["identifier", "origin_x", "origin_y", "destination_x", "destination_y",
                                       "departure_time"])


def _served(probe: Probe, tasks: pd.DataFrame, person_ids, positions) -> np.ndarray:
    connected = probe(tasks)
    missing = set(tasks["identifier"]) - set(connected.index)
    if missing:
        raise ValueError(f"{_LOG_TAG} the probe answered no result for {len(missing)} task(s), e.g. "
                         f"{sorted(missing)[:3]}")
    return np.array([bool(connected[f"{person_ids[i]}:{i}:out"]) and bool(connected[f"{person_ids[i]}:{i}:ret"])
                     for i in positions], dtype=bool)


def _redraw_stations(probe: Probe, person_ids, positions, work_xy, depart_work_s, station_candidates) -> dict:
    """Probe both trips of every candidate station of the days at ``positions`` in ONE call (identifiers
    ``<person>:<i>:<stop_id>:out`` / ``:ret``, at the candidate's home departure and the unshifted work departure);
    return the first connected candidate in the given (weight) order per position."""
    rows, keys = [], []
    for i in positions:
        for candidate in station_candidates[i]:
            prefix = f"{person_ids[i]}:{i}:{candidate.stop_id}"
            rows.append((f"{prefix}:out", candidate.x, candidate.y, work_xy[i, 0], work_xy[i, 1],
                         candidate.depart_home_s))
            rows.append((f"{prefix}:ret", work_xy[i, 0], work_xy[i, 1], candidate.x, candidate.y,
                         depart_work_s[i]))
            keys.append((i, candidate, prefix))
    tasks = pd.DataFrame(rows, columns=["identifier", "origin_x", "origin_y", "destination_x", "destination_y",
                                        "departure_time"])
    duplicated = tasks["identifier"].duplicated()
    if duplicated.any():
        raise ValueError(f"{_LOG_TAG} duplicate station candidate task {tasks.loc[duplicated, 'identifier'].iloc[0]}"
                         f": a candidate list names a stop twice")
    connected = probe(tasks)
    missing = set(tasks["identifier"]) - set(connected.index)
    if missing:
        raise ValueError(f"{_LOG_TAG} the probe answered no result for {len(missing)} station task(s), e.g. "
                         f"{sorted(missing)[:3]}")
    chosen = {}
    for i, candidate, prefix in keys:
        if i not in chosen and bool(connected[f"{prefix}:out"]) and bool(connected[f"{prefix}:ret"]):
            chosen[i] = candidate
    return chosen


def resolve_pt_days(*, person_ids, is_pt, home_xy, work_xy, depart_home_s, arrive_work_s, depart_work_s,
                    arrive_home_s, probe: Probe, shift_window_s: float, shift_step_s: float, bound_s: float,
                    car_allowed: bool = True, station_candidates: dict | None = None) -> Resolution:
    """Check and resolve the pt days; see the module docstring. Pure apart from the probe calls.

    ``station_candidates`` maps an agent position to its alternative stations (:class:`StationCandidate`, in
    descending selection weight); a position without an entry (an in-ring origin, a Kreis without another station,
    or ``None`` for all) skips the station step.
    """
    person_ids = np.asarray(person_ids)
    is_pt = np.asarray(is_pt, dtype=bool)
    home_xy, work_xy = np.asarray(home_xy, dtype=float), np.asarray(work_xy, dtype=float)
    depart_home_s, depart_work_s = np.asarray(depart_home_s, dtype=float), np.asarray(depart_work_s, dtype=float)
    arrive_home_s = np.asarray(arrive_home_s, dtype=float)
    offsets = shift_offsets_s(shift_window_s, shift_step_s)
    n = len(person_ids)
    actions = np.full(n, None, dtype=object)
    applied = np.zeros(n, dtype=float)

    pt_positions = np.flatnonzero(is_pt)
    if len(pt_positions):
        served = _served(probe, _tasks(person_ids, pt_positions, home_xy, work_xy, depart_home_s, depart_work_s,
                                       0.0), person_ids, pt_positions)
        actions[pt_positions[served]] = ACTION_CONNECTION_FOUND
    unresolved = [int(i) for i in pt_positions if actions[i] is None]
    for offset in offsets:
        if not unresolved:
            break
        candidates = [i for i in unresolved
                      if depart_home_s[i] + offset >= 0.0 and arrive_home_s[i] + offset <= bound_s]
        if not candidates:
            continue
        served = _served(probe, _tasks(person_ids, candidates, home_xy, work_xy, depart_home_s, depart_work_s,
                                       offset), person_ids, candidates)
        for i, ok in zip(candidates, served):
            if ok:
                actions[i] = ACTION_DAY_SHIFTED
                applied[i] = offset
        unresolved = [i for i in unresolved if actions[i] is None]
    stations = np.full(n, None, dtype=object)
    station_candidates = station_candidates or {}
    redraw_positions = [i for i in unresolved if station_candidates.get(i)]
    if redraw_positions:
        chosen = _redraw_stations(probe, person_ids, redraw_positions, work_xy, depart_work_s, station_candidates)
        for i, candidate in chosen.items():
            actions[i] = ACTION_STATION_REDRAWN
            stations[i] = candidate
        unresolved = [i for i in unresolved if actions[i] is None]
    for i in unresolved:
        actions[i] = ACTION_TO_CAR if car_allowed else ACTION_KEPT

    shifted = actions == ACTION_DAY_SHIFTED
    report = {"n_pt_days": int(len(pt_positions)),
              "actions": {action: int((actions == action).sum()) for action in ACTIONS},
              "shift_abs_s_median": float(np.median(np.abs(applied[shifted]))) if shifted.any() else None,
              "n_station_candidate_days": int(len(redraw_positions)),
              "shift_window_s": float(shift_window_s), "shift_step_s": float(shift_step_s), "bound_s": float(bound_s)}
    return Resolution(actions=actions, offsets_s=applied, report=report, stations=stations)


@dataclasses.dataclass
class CheckedDays:
    """The pt days after :func:`check_pt_days`: the resolution, the modes with ``to_car`` days switched to ``"car"``,
    the four day times (s) with each ``day_shifted`` offset applied, and the logged report."""

    resolution: Resolution
    modes: np.ndarray
    depart_home_s: np.ndarray
    arrive_mid_s: np.ndarray
    depart_mid_s: np.ndarray
    arrive_home_s: np.ndarray
    report: dict


def check_pt_days(pt_check: PtCheck, *, person_ids, modes, home_xy, mid_xy, depart_home_s, arrive_mid_s,
                  depart_mid_s, arrive_home_s, car_allowed: bool, probe: Probe | None = None,
                  station_candidates: dict | None = None, report_extra: dict | None = None,
                  subject: str = "in-commuter") -> CheckedDays:
    """Check the pt days of one in-commuter stage, apply the day shifts and the switch to car, and log the rates.

    The common part of every in-commuter stage (employees #488, students #528): :func:`resolve_pt_days` on the days
    whose mode is ``"pt"`` (the middle activity is work or education), every ``day_shifted`` day moved by its offset,
    every ``to_car`` day's mode set to ``"car"``, and :func:`log_report` over the report (``report_extra`` merged in
    first; ``subject`` names the stage's agents in the log line). What a switch or a station re-draw means for the
    home point stays with the caller (the employee stage boards a car at its road gate or the pt agent at the chosen
    station). ``probe`` defaults to ``pt_check.probe``.
    """
    modes = np.asarray(modes)
    resolution = resolve_pt_days(
        person_ids=person_ids, is_pt=modes == "pt", home_xy=home_xy, work_xy=mid_xy, depart_home_s=depart_home_s,
        arrive_work_s=arrive_mid_s, depart_work_s=depart_mid_s, arrive_home_s=arrive_home_s,
        probe=pt_check.probe if probe is None else probe, shift_window_s=pt_check.shift_window_s,
        shift_step_s=pt_check.shift_step_s, bound_s=pt_check.bound_s, car_allowed=car_allowed,
        station_candidates=station_candidates)
    offset = resolution.offsets_s
    report = dict(resolution.report, **(report_extra or {}))
    log_report(report, warn_share=pt_check.warn_share, subject=subject)
    return CheckedDays(
        resolution=resolution, modes=np.where(resolution.actions == ACTION_TO_CAR, "car", modes),
        depart_home_s=np.asarray(depart_home_s, dtype=float) + offset,
        arrive_mid_s=np.asarray(arrive_mid_s, dtype=float) + offset,
        depart_mid_s=np.asarray(depart_mid_s, dtype=float) + offset,
        arrive_home_s=np.asarray(arrive_home_s, dtype=float) + offset, report=report)


def log_report(report: dict, warn_share: float, subject: str = "in-commuter") -> None:
    """One line with the rates over the pt days of the ``subject`` agents ("in-commuter" for the employee stage,
    "student in-commuter" for the student stage, so the two lines stay apart). Two independent WARNINGs, each with
    the line: (a) to_car + kept exceed ``warn_share`` or any day is kept; (b) the station re-draws exceed
    ``warn_share``."""
    n = report["n_pt_days"]
    counts = report["actions"]
    unserved = counts[ACTION_TO_CAR] + counts[ACTION_KEPT]
    redrawn = counts[ACTION_STATION_REDRAWN]
    message = "%s %d pt %s days: %s; median shift %s s; %d days tried other stations" % (
        _LOG_TAG, n, subject, ", ".join("%s %d (%.1f%%)" % (a, counts[a], 100.0 * counts[a] / n if n else 0.0)
                               for a in ACTIONS),
        "n/a" if report["shift_abs_s_median"] is None else "%.0f" % report["shift_abs_s_median"],
        report.get("n_station_candidate_days", 0))
    warn_unserved = bool(counts[ACTION_KEPT] or (n and unserved / n > warn_share))
    warn_redrawn = bool(n and redrawn / n > warn_share)
    if warn_unserved:
        logger.warning("%s -- %.1f%% of the days have no timetable connection within the shift window (warn share "
                       "%.1f%%): %s %d drive instead, %s %d are routed as walks", message,
                       100.0 * unserved / n if n else 0.0, 100.0 * warn_share, ACTION_TO_CAR, counts[ACTION_TO_CAR],
                       ACTION_KEPT, counts[ACTION_KEPT])
    if warn_redrawn:
        logger.warning("%s -- %.1f%% of the days board at another station of their Kreis than drawn (warn share "
                       "%.1f%%): check the station weighting and the schedule extent", message,
                       100.0 * redrawn / n, 100.0 * warn_share)
    if not (warn_unserved or warn_redrawn):
        logger.info(message)
