"""synpp stage: build the portal trips of the reporting day (eqasim-bs#442).

Returns ``{"trips", "anchors", "report"}``; the two thin stages ``trips_final`` (aliased as
``synthesis.population.trips.final``) and ``anchors`` hand one frame each to their consumers.
With ``braunschweig.portal.enabled`` false the trips frame is the input frame itself and the
anchors frame is empty (byte-identical OFF path). The computation is ``build_portal_trips``, a
pure function over frames, so it is testable without synpp.

Fallback transparency (CLAUDE.md): the stage counts and logs every substitution it makes -- the
work/education legs classified by the donor's reported distance instead of the assigned location,
the external points drawn outside the distance band, the origins proxied by home, the mode
substitutions, the capped re-entry shares -- and warns when a rate exceeds the configured share.
"""
from __future__ import annotations

import copy
import functools
import json
import logging
import os

import numpy as np
import pandas as pd

from shapely.geometry import Point

from braunschweig import synpp_tokens
from braunschweig.matsim.simulation import plan_check as _plan_check
from braunschweig.popsim import plan_validation as _plan_validation
from braunschweig.synthesis.portal_trips import classification as _classification
from braunschweig.synthesis.portal_trips import config_keys as _config_keys
from braunschweig.synthesis.portal_trips import gates as _gates
from braunschweig.synthesis.portal_trips import modes as _modes
from braunschweig.synthesis.portal_trips import no_connection as _no_connection
from braunschweig.synthesis.portal_trips import rewrite
from braunschweig.synthesis.portal_trips import timing as _timing
from braunschweig.synthesis.portal_trips.config_keys import (
    DEFAULT_ENABLED, DEFAULT_EXTERNAL_POINT_DISTANCE_TOLERANCE, DEFAULT_FALLBACK_WARN_SHARE,
    DEFAULT_GATE_CANDIDATES, DEFAULT_GATE_SERVICE_WINDOW_S, DEFAULT_MAX_ROUTABLE_DISTANCE_M,
    DEFAULT_MIN_STAY_S, DEFAULT_MODE_SUBSTITUTION_WARN_SHARE, DEFAULT_NO_CONNECTION_WARN_SHARE,
    DEFAULT_NO_CONNECTION_LAST_RESORT, DEFAULT_PT_GATE_MODES, DEFAULT_SHIFT_RETURN_EARLIER, DEFAULT_SHIFT_STEP_S,
    DEFAULT_SHIFT_WINDOW_S, KEY_ENABLED, KEY_EXTERNAL_POINT_DISTANCE_TOLERANCE, KEY_FALLBACK_WARN_SHARE,
    KEY_GATE_CANDIDATES, KEY_GATE_SERVICE_WINDOW_S, KEY_MAX_ROUTABLE_DISTANCE_M, KEY_MIN_STAY_S,
    KEY_MODE_SUBSTITUTION_WARN_SHARE, KEY_NO_CONNECTION_LAST_RESORT, KEY_NO_CONNECTION_WARN_SHARE, KEY_PT_GATE_MODES,
    KEY_SHIFT_RETURN_EARLIER, KEY_SHIFT_STEP_S, KEY_SHIFT_WINDOW_S, RNG_OFFSET, validate_settings)

logger = logging.getLogger(__name__)

_LOG_TAG = "[portal_trips]"
REPORT_FILE_NAME = "portal_trips_report.json"
TRIPS_STAGE = "braunschweig.synthesis.commute_day.trips_day_stage"
PERSONS_STAGE = "synthesis.population.enriched"
REQUIRED_TRIP_COLUMNS = ("person_id", "trip_index", "preceding_purpose", "following_purpose", "mode",
                         "euclidean_distance", "departure_time", "arrival_time")

#: Own-package helpers whose source shapes this stage's output (hashed by validate()).
_HELPER_MODULES = (_classification, _config_keys, _gates, _modes, _no_connection, rewrite, _timing,
                   _plan_check, _plan_validation)
#: Cross-package modules imported inside execute() (hashed by dotted name).
_DEFERRED_HELPER_MODULE_NAMES = ("braunschweig.data.spatial.cordon", "braunschweig.data.cordon.network",
                                 "braunschweig.data.transit.schedule", "braunschweig.data.transit.connections",
                                 "braunschweig.popsim.closure_dwell", "data.hts.hts")

#: Router work files live in a subdirectory of the stage directory.
ROUTER_WORK_DIR_NAME = "pt_router"
ROUTER_CONFIG_NAME = "pt_router_config.xml"

#: Report entries of a run without any outside stay (the stay-level counts are all zero).
_EMPTY_STAY_REPORT = {
    "n_persons_with_stay": 0, "n_stays_without_return": 0, "n_stays_by_kind": {}, "n_stays_by_mode": {},
    "gate_usage": {}, "n_mode_substituted": 0, "mode_substitution_reasons": {}, "n_return_mode_differs": 0,
    "n_external_point_drawn": 0, "n_external_band_miss": 0, "n_origin_proxied_by_home": 0,
    "n_reentry_clamped": 0, "n_return_share_degenerate": 0, "n_outbound_share_degenerate": 0,
    "return_share_out_median": None,
    "n_stay_persons_missing_license": 0, "n_unknown_donor_mode": 0, "unknown_donor_modes": {},
    "n_pt_stays": 0, "pt_gate_reasons": {}, "no_connection_actions": {}, "n_departure_shifted_s_median": None,
    "n_kept_fallback_all_stops": 0,
}


def validate(context):
    """synpp validation token over the helper sources (same mechanism as trips_day_stage.validate).

    Returns the structured token of ``braunschweig.synpp_tokens.source_token``: one semantic
    digest per helper module (docstrings, comments and formatting do not change it).
    """
    return synpp_tokens.source_token(
        _HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES,
        stage_label=__name__ + ".validate()")


def configure(context):
    enabled = context.config(KEY_ENABLED, DEFAULT_ENABLED)
    context.stage(TRIPS_STAGE)
    if not enabled:
        return
    context.config("random_seed")
    # The gates ARE the cordon: a portal layer without a cordon has nowhere to send a trip.
    if not context.config("cordon_enabled", False):
        raise ValueError(f"{KEY_ENABLED}=True requires cordon_enabled=True (the portal stays sit at the cordon gates)")
    context.config("cordon_network_buffer_fraction", 0.10)
    # Timetable-aware pt gates (#465): the settings, and the runtime of the batch pt router (the router is a
    # Java tool of the eqasim jar, run through eqasim.run -> java.run like in matsim.simulation.prepare).
    pt_gate_settings = _configure_gate_settings(context)
    validate_settings(float(context.config(KEY_MAX_ROUTABLE_DISTANCE_M, DEFAULT_MAX_ROUTABLE_DISTANCE_M)),
                      float(context.config(KEY_EXTERNAL_POINT_DISTANCE_TOLERANCE,
                                           DEFAULT_EXTERNAL_POINT_DISTANCE_TOLERANCE)),
                      float(context.config(KEY_MODE_SUBSTITUTION_WARN_SHARE, DEFAULT_MODE_SUBSTITUTION_WARN_SHARE)),
                      float(context.config(KEY_FALLBACK_WARN_SHARE, DEFAULT_FALLBACK_WARN_SHARE)),
                      **pt_gate_settings)
    context.config(KEY_PT_GATE_MODES, DEFAULT_PT_GATE_MODES)
    # The batch pt router runs eqasim.run -> java.run; connections.configure declares the Java options.
    from braunschweig.data.transit import connections
    connections.configure(context)
    context.stage("matsim.runtime.eqasim")
    context.stage("matsim.runtime.java")
    # Execution details, not scientific config: the thread count must not devalidate cached stages.
    context.config("processes", volatile=True)
    context.config("matsim_threads", None, volatile=True)
    context.stage("matsim.scenario.supply.gtfs")
    # The enriched frame carries car_availability / has_license / bicycle_availability, which the
    # mode check needs; the sampled frame does not.
    context.stage(PERSONS_STAGE)
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("synthesis.population.spatial.primary.locations")
    context.stage("braunschweig.synthesis.cordon_gates")
    context.stage("braunschweig.data.cordon_network")
    context.stage("braunschweig.data.external_secondary_points")
    context.stage("data.spatial.municipalities")
    context.stage("matsim.scenario.supply.processed")


def _configure_gate_settings(context) -> dict:
    """Declare the timetable-aware gate settings and return them as the keyword arguments of ``validate_settings``.

    Called from ``configure`` only: the two-argument ``context.config(key, default)`` form is invalid at execute.
    """
    return {"gate_candidates": int(context.config(KEY_GATE_CANDIDATES, DEFAULT_GATE_CANDIDATES)),
            "gate_service_window_s": float(context.config(KEY_GATE_SERVICE_WINDOW_S,
                                                          DEFAULT_GATE_SERVICE_WINDOW_S)),
            "shift_window_s": float(context.config(KEY_SHIFT_WINDOW_S, DEFAULT_SHIFT_WINDOW_S)),
            "shift_step_s": float(context.config(KEY_SHIFT_STEP_S, DEFAULT_SHIFT_STEP_S)),
            "min_stay_s": float(context.config(KEY_MIN_STAY_S, DEFAULT_MIN_STAY_S)),
            "no_connection_warn_share": float(context.config(KEY_NO_CONNECTION_WARN_SHARE,
                                                             DEFAULT_NO_CONNECTION_WARN_SHARE)),
            "shift_return_earlier": context.config(KEY_SHIFT_RETURN_EARLIER, DEFAULT_SHIFT_RETURN_EARLIER),
            "no_connection_last_resort": context.config(KEY_NO_CONNECTION_LAST_RESORT,
                                                        DEFAULT_NO_CONNECTION_LAST_RESORT)}


def assert_consistent_crs(frames: dict) -> None:
    """Raise ``ValueError`` naming every frame and its CRS unless all frames share one CRS.

    ``frames`` maps a display name to an object with a ``crs`` attribute (a GeoDataFrame). The gates, the links,
    the external points, the homes and the municipalities (from which the cordon polygon is built) are combined
    by coordinates, so a frame in another CRS, or without one, would shift every distance silently.
    """
    crs_by_name = {name: frame.crs for name, frame in frames.items()}
    reference = next(iter(crs_by_name.values()))
    if reference is not None and all(crs == reference for crs in crs_by_name.values()):
        return
    described = ", ".join(f"{name}={'None' if crs is None else crs.to_string()}"
                          for name, crs in crs_by_name.items())
    raise ValueError(f"{_LOG_TAG} the spatial inputs are not in one CRS ({described}); the portal stage combines "
                     "them by metric coordinates. Reproject them to the CRS of the home locations, or set the "
                     "missing CRS, before the portal stage.")


def _json_default(value):
    """JSON encoder hook for numpy scalars and arrays in the report."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def _trip_row_positions(trips, person_ids, trip_indices):
    """Row position in ``trips`` of each (person_id, trip_index) pair; ValueError when a pair is absent.

    ``trips`` must carry a unique (person_id, trip_index) key, which ``rewrite.recompute_chain_columns``
    guarantees by construction.
    """
    key = pd.MultiIndex.from_frame(trips[["person_id", "trip_index"]])
    positions = key.get_indexer(pd.MultiIndex.from_arrays([np.asarray(person_ids), np.asarray(trip_indices)]))
    absent = positions < 0
    if absent.any():
        raise ValueError(f"{_LOG_TAG} {int(absent.sum())} stay legs are not in the trips table "
                         f"(first: person_id {np.asarray(person_ids)[absent][0]}); the stay table and the trips "
                         "table are inconsistent")
    return positions


def _origin_xy_for_stays(trips, stays, home_xy, primary, outbound_positions):
    """The chain anchor the outbound leg of each stay departs from, and how many had to be proxied.

    The origin is the ASSIGNED work/education location when the outbound leg leaves a work/education
    activity and that location exists (finite coordinates); otherwise home. ``n_proxied`` counts every
    origin that is neither home itself (the leg leaves home) nor an assigned primary location, i.e.
    every stay whose real origin (a secondary activity, or a primary activity without an assigned
    location) is replaced by home. ``outbound_positions`` are the row positions of the outbound legs in
    ``trips`` (see ``_trip_row_positions``). Returns ``(origins, n_proxied)``, ``origins`` of shape (n, 2).
    """
    person_ids = stays["person_id"].to_numpy()
    preceding = trips["preceding_purpose"].to_numpy()[outbound_positions]
    origins = home_xy.reindex(person_ids)[["x", "y"]].to_numpy(dtype=float)
    at_home_or_assigned = preceding == _classification.HOME_PURPOSE
    for purpose in _classification.PRIMARY_PURPOSES:
        mask = preceding == purpose
        if not mask.any():
            continue
        located = primary[purpose].reindex(person_ids[mask])[["x", "y"]].to_numpy(dtype=float)
        finite = np.isfinite(located).all(axis=1)
        rows = np.flatnonzero(mask)[finite]
        origins[rows] = located[finite]
        at_home_or_assigned[rows] = True
    return origins, int((~at_home_or_assigned).sum())


def _primary_destination_xy(trips, stays, primary, outbound_positions):
    """Assigned work/education coordinates of stays whose outbound leg goes to work/education.

    Returns ``(xy, assigned)``: ``xy`` is (n, 2) with NaN where there is no such location and ``assigned``
    flags the stays that have one. Stays towards a primary activity WITHOUT a finite assigned
    location are not flagged (they keep the externally drawn point).
    """
    person_ids = stays["person_id"].to_numpy()
    following = trips["following_purpose"].to_numpy()[outbound_positions]
    xy = np.full((len(stays), 2), np.nan)
    assigned = np.zeros(len(stays), dtype=bool)
    for purpose in _classification.PRIMARY_PURPOSES:
        mask = following == purpose
        if not mask.any():
            continue
        located = primary[purpose].reindex(person_ids[mask])[["x", "y"]].to_numpy(dtype=float)
        finite = np.isfinite(located).all(axis=1)
        rows = np.flatnonzero(mask)[finite]
        xy[rows] = located[finite]
        assigned[rows] = True
    return xy, assigned


def _classify(trips, home_xy, primary, threshold_m, fallback_warn_share):
    """Portal flags of all legs plus the primary-path coverage of the work/education legs."""
    frame = _classification.classification_distance_frame(trips, home_xy, primary)
    distance = frame["classification_distance_m"]
    is_portal = _classification.portal_flags(distance, trips["following_purpose"], threshold_m)
    n_primary = int(trips["following_purpose"].isin(_classification.PRIMARY_PURPOSES).sum())
    n_fallback = int(frame["used_reported_distance"].sum())
    rate = n_fallback / n_primary if n_primary else 0.0
    log = logger.warning if rate > fallback_warn_share else logger.info
    log("%s primary legs assigned %d/%d (%.1f%%), reported-distance fallback %d (%.1f%%; warn above %.0f%%)",
        _LOG_TAG, n_primary - n_fallback, n_primary, 100.0 * (1.0 - rate) if n_primary else 0.0, n_fallback,
        100.0 * rate, 100.0 * fallback_warn_share)
    if n_primary > 0 and n_fallback == n_primary:
        raise ValueError(f"{_LOG_TAG} all {n_primary} work/education legs were classified by the reported-distance "
                         "fallback; the join of the assigned primary locations to the trips is broken "
                         "(person_id mismatch or empty location tables)")
    # Displacement bound of the non-primary legs (ADR-0141): the diary's own estimate (way out + way back)
    # caps the reported distance. Information rates, not a failure signal: a missing bound only keeps the
    # reported distance (a chain without a home start/end, or a leg in the sums without a reported distance).
    # Legs whose OWN reported distance is missing are counted separately: nothing to tighten, never portal.
    n_non_primary = int(frame["non_primary"].sum())
    n_bounded = int(frame["bound_applied"].sum())
    n_missing_own = int(frame["reported_distance_missing"].sum())
    n_unbounded = int((frame["bound_unknown"] & ~frame["reported_distance_missing"]).sum())
    reported_portal = _classification.portal_flags(trips["euclidean_distance"].astype(float),
                                                   trips["following_purpose"], threshold_m)
    n_lost = int((frame["bound_applied"] & reported_portal & ~is_portal).sum())
    logger.info("%s displacement bound (non-primary legs, %d): bounded %d/%d (%.1f%%), lose portal status through the "
                "bound %d, without a usable bound %d/%d (%.1f%%; those keep the reported distance), without a "
                "reported distance %d/%d (%.1f%%; never portal)", _LOG_TAG, n_non_primary, n_bounded, n_non_primary,
                100.0 * n_bounded / max(n_non_primary, 1), n_lost, n_unbounded, n_non_primary,
                100.0 * n_unbounded / max(n_non_primary, 1), n_missing_own, n_non_primary,
                100.0 * n_missing_own / max(n_non_primary, 1))
    counts = {"n_primary_legs": n_primary, "n_primary_legs_assigned": n_primary - n_fallback,
              "n_primary_legs_reported_distance_fallback": n_fallback,
              "n_nonprimary_legs": n_non_primary, "n_nonprimary_legs_bounded": n_bounded,
              "n_portal_legs_lost_through_bound": n_lost, "n_nonprimary_legs_without_bound": n_unbounded,
              "n_nonprimary_legs_without_reported_distance": n_missing_own}
    return is_portal, distance, counts


def _stay_fallback_gates(origin_xy, point_xy, exits, needed):
    """The gate a KEPT pt stay without a connection takes, for the stays flagged in ``needed``.

    Per needed stay the nearest-by-detour exit stop among its DIRECTIONAL exit stops (outside stop closer to
    the external point than the exit stop); when the stay has none, among all exit stops. Returns
    ``(frame with gate_id, x, y, all_stops)``: one row per stay (rows of stays that are not ``needed`` stay
    None/NaN and are never read by ``resolve_no_connection``, which only reads the rows of the stays it
    keeps) and ``all_stops``, the bool flags of the needed stays that had no directional exit stop and
    took the all-stops fallback. Without any exit stop every row is None/NaN (``choose_gates`` then raises
    for a kept stay).
    """
    n = len(origin_xy)
    frame = pd.DataFrame({"gate_id": [None] * n, "x": np.full(n, np.nan), "y": np.full(n, np.nan)})
    all_stops = np.zeros(n, dtype=bool)
    if n == 0 or len(exits) == 0 or not np.any(needed):
        return frame, all_stops
    points = _gates.exit_stop_points(exits)
    point_ids = points["stop_id"].astype(str).to_numpy()
    all_xy = np.column_stack([points.geometry.x.to_numpy(), points.geometry.y.to_numpy()])
    id_row = {stop: row for row, stop in enumerate(point_ids)}
    exit_ids = exits["stop_id"].astype(str).to_numpy()
    gate_ids = np.full(n, None, dtype=object)
    xy = np.full((n, 2), np.nan)
    for i in np.flatnonzero(needed):
        toward = _gates._toward_mask(point_xy[i], exits)
        rows = sorted({id_row[stop] for stop in exit_ids[toward]})
        if not rows:
            rows = list(range(len(point_ids)))
            all_stops[i] = True
        best = rows[int(_gates._nearest_by_detour(origin_xy[i:i + 1], point_xy[i:i + 1], all_xy[rows])[0])]
        gate_ids[i], xy[i] = point_ids[best], all_xy[best]
    frame["gate_id"] = gate_ids
    frame["x"], frame["y"] = xy[:, 0], xy[:, 1]
    return frame, all_stops


def _inside_stop_ids(schedule, cordon_polygon) -> set:
    """Ids of the schedule stops that lie inside the cut extent (a connection may only use these)."""
    return {stop for stop, (x, y) in schedule.stops.items() if cordon_polygon.contains(Point(x, y))}


def build_portal_trips(*, trips, persons, df_home, df_work, df_education, gates, links, external_points,
                       schedule, router, pt_gate_modes, gate_settings, cordon_polygon, threshold_m, tolerance,
                       warn_share, fallback_warn_share, seed):
    """Pure entry point: classify, merge stays, draw points, choose gates, time, fix modes, rewrite.

    Coordinates are metres in the CRS of ``df_home``; times are seconds. ``warn_share`` bounds the
    mode-substitution rate and ``fallback_warn_share`` the rates of the two classification/draw
    fallbacks; exceeding either only changes the log level (the counts are always in the report).

    The pt stays take a timetable-aware gate (#465): ``schedule`` is the pre-cut transit schedule
    (``braunschweig.data.transit.schedule.Schedule``), ``router`` the batch pt router
    (``braunschweig.data.transit.connections.Router``: tasks frame -> (trips, legs)), ``pt_gate_modes``
    ``"all"`` or the transit modes allowed as gates, and ``gate_settings`` the dict with ``n_candidates``,
    ``service_window_s``, ``shift_window_s``, ``shift_step_s``, ``min_stay_s`` and ``no_connection_warn_share``.
    A pt stay without an admissible gate is resolved by ``no_connection.resolve_no_connection`` (whole-tour
    mode switch, time shift, kept with its nearest directional exit stop); every outcome is counted in the
    report and WARNed above ``no_connection_warn_share``.
    """
    # A direct caller gets the same range checks as configure() (a zero shift step would silently disable the shift path).
    validate_settings(threshold_m, tolerance, warn_share, fallback_warn_share,
                      gate_candidates=gate_settings["n_candidates"],
                      gate_service_window_s=gate_settings["service_window_s"],
                      shift_window_s=gate_settings["shift_window_s"], shift_step_s=gate_settings["shift_step_s"],
                      min_stay_s=gate_settings["min_stay_s"],
                      no_connection_warn_share=gate_settings["no_connection_warn_share"])
    missing = [column for column in REQUIRED_TRIP_COLUMNS if column not in trips.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} trips frame lacks {missing}")
    assert_consistent_crs({"df_home": df_home, "gates": gates, "links": links, "external_points": external_points})
    trips = rewrite.recompute_chain_columns(trips)
    home_xy = _classification.person_home_xy(persons, df_home)
    primary = _classification.primary_xy(df_work, df_education)
    is_portal, distance_m, classification_counts = _classify(trips, home_xy, primary, threshold_m,
                                                             fallback_warn_share)
    stays = _classification.find_outside_stays(trips, is_portal)
    report = {"n_persons": int(trips["person_id"].nunique()), "n_portal_legs": int(is_portal.sum()),
              "n_stays": int(len(stays)), "n_removed_legs": int(stays["n_removed_legs"].sum()) if len(stays) else 0,
              **classification_counts, **copy.deepcopy(_EMPTY_STAY_REPORT)}
    if len(stays) == 0:
        logger.info("%s no portal legs beyond %.0f m: the trips are unchanged", _LOG_TAG, threshold_m)
        out, anchors = rewrite.rewrite_trips(trips, stays, pd.DataFrame(columns=_gates.GATE_COLUMNS),
                                             pd.DataFrame(columns=_timing.TIMING_COLUMNS),
                                             pd.DataFrame(columns=_modes.MODE_COLUMNS), np.zeros((0, 2)),
                                             df_home.crs)
        return {"trips": out, "anchors": anchors, "report": report}

    person_ids = stays["person_id"].to_numpy()
    outbound_positions = _trip_row_positions(trips, person_ids, stays["outbound_trip_index"].to_numpy())
    origin_xy, n_proxied = _origin_xy_for_stays(trips, stays, home_xy, primary, outbound_positions)
    # One source of truth: the classification distance (assigned location for work/education, the reported
    # distance capped by the diary's displacement bound otherwise) also decides where the point is drawn.
    classification_m = distance_m.to_numpy()[outbound_positions]

    # Work/education stays point at their assigned location; every other stay (and a work/education
    # stay whose person has no assigned location) draws an external point at the reported distance.
    point_xy, assigned = _primary_destination_xy(trips, stays, primary, outbound_positions)
    band_miss = np.zeros(len(stays), dtype=bool)
    draw = ~assigned
    if draw.any():
        rng = np.random.default_rng(int(seed) + RNG_OFFSET)
        drawn_xy, _, drawn_band_miss = _gates.draw_external_points(
            classification_m[draw], origin_xy[draw], external_points, tolerance, rng)
        point_xy[draw] = drawn_xy
        band_miss[draw] = drawn_band_miss

    availability = _modes.mode_availability(persons)
    modes = _modes.portal_modes(stays, trips, availability)
    road_gates = _gates.gate_inside_points(gates, links, cordon_polygon)
    modes, trips, pt_gates, pt_report = _resolve_pt_stays(
        stays, trips, modes, availability, origin_xy, point_xy, outbound_positions, schedule, router,
        pt_gate_modes, gate_settings, cordon_polygon, df_home.crs)
    gate_rows = _gates.choose_gates(origin_xy, point_xy, modes["mode"].to_numpy(), road_gates, pt_gates)
    gate_xy = gate_rows[["x", "y"]].to_numpy(dtype=float)
    outbound = _timing.outbound_arrivals(stays, trips, origin_xy, gate_xy, point_xy)
    times = pd.concat([_timing.reentry_times(stays, trips, gate_xy, point_xy, origin_xy,
                                             outbound_arrival_time=outbound["outbound_arrival_time"].to_numpy()),
                       outbound], axis=1)
    out, anchors = rewrite.rewrite_trips(trips, stays, gate_rows, times, modes[_modes.MODE_COLUMNS], origin_xy,
                                         df_home.crs)

    unknown_mode = ~modes["outbound_mode"].isin(list(_modes.MODE_FALLBACKS)).to_numpy()
    licence = persons.set_index("person_id")["has_license"].reindex(np.unique(person_ids))
    report.update({
        "n_persons_with_stay": int(len(np.unique(person_ids))),
        "n_stays_without_return": int((~times["has_return"]).sum()),
        "n_stays_by_kind": _counts(gate_rows["kind"]),
        "n_stays_by_mode": _counts(modes["mode"]),
        "gate_usage": _counts(gate_rows["gate_id"]),
        "n_mode_substituted": int(modes["substituted_from"].notna().sum()),
        "mode_substitution_reasons": _counts(modes["substitution_reason"].dropna()),
        "n_return_mode_differs": int(modes["return_mode_differs"].sum()),
        "n_external_point_drawn": int(draw.sum()),
        "n_external_band_miss": int(band_miss.sum()),
        "n_origin_proxied_by_home": n_proxied,
        "n_reentry_clamped": int(times["clamped"].sum()),
        "n_return_share_degenerate": int(times["share_degenerate"].sum()),
        "n_outbound_share_degenerate": int(outbound["outbound_share_degenerate"].sum()),
        "return_share_out_median": (float(times["share_out"].median()) if times["has_return"].any() else None),
        "n_stay_persons_missing_license": int(licence.isna().sum()),
        "n_unknown_donor_mode": int(unknown_mode.sum()),
        "unknown_donor_modes": _counts(modes["outbound_mode"][unknown_mode]),
        **pt_report,
    })
    _log_report(report, warn_share, fallback_warn_share, gate_settings["no_connection_warn_share"])
    return {"trips": out, "anchors": anchors, "report": report}


def _resolve_pt_stays(stays, trips, modes, availability, origin_xy, point_xy, outbound_positions, schedule, router,
                      pt_gate_modes, gate_settings, cordon_polygon, crs):
    """Timetable-aware gates of the pt stays and the no-connection fallback chain (#465).

    Returns ``(modes, trips, pt_gates, report_entries)``: ``modes`` carries the added column
    ``no_connection_action``, ``trips`` the mode switches and departure shifts of the chain, ``pt_gates`` one
    gate row per stay that is still pt (kept stays hold their nearest directional exit stop).
    """
    pt_rows = np.flatnonzero((modes["mode"] == "pt").to_numpy())
    n_pt = len(pt_rows)
    walk_max_m = float(_plan_check.DEFAULT_WALK_MAX_DISTANCE_M)
    bound_s = float(_plan_validation.MAX_PLAN_TIME_SECONDS)
    chain = dict(shift_window_s=gate_settings["shift_window_s"], shift_step_s=gate_settings["shift_step_s"],
                 min_stay_s=gate_settings["min_stay_s"], bound_s=bound_s,
                 return_earlier=gate_settings.get("shift_return_earlier", False),
                 last_resort=gate_settings.get("no_connection_last_resort", _no_connection.LAST_RESORT_KEEP))
    if n_pt == 0:
        empty = pd.DataFrame(columns=_gates.PT_GATE_COLUMNS)
        out_modes, out_trips, out_gates = _no_connection.resolve_no_connection(
            stays, trips, modes, availability, empty, lambda *args: empty,
            fallback_gates=pd.DataFrame(columns=["gate_id", "x", "y"]), **chain)
        return out_modes, out_trips, out_gates, {
            "n_pt_stays": 0, "pt_gate_reasons": {}, "no_connection_actions": {},
            "n_departure_shifted_s_median": None, "n_kept_fallback_all_stops": 0}
    if router is None:
        raise ValueError(f"{_LOG_TAG} {n_pt} pt stays need the batch pt router but none was given")
    exits = _gates.exit_stops(schedule, cordon_polygon, pt_gate_modes, crs=crs)
    inside_stop_ids = _inside_stop_ids(schedule, cordon_polygon)

    person_ids = stays["person_id"].to_numpy()[pt_rows]
    has_return = stays["return_trip_index"].notna().to_numpy()[pt_rows]
    dep_all = trips["departure_time"].to_numpy(dtype=float)
    arr_all = trips["arrival_time"].to_numpy(dtype=float)
    out_pos = outbound_positions[pt_rows]
    ret_pos = np.zeros(n_pt, dtype=int)
    if has_return.any():
        ret_pos[has_return] = _trip_row_positions(
            trips, person_ids[has_return], stays["return_trip_index"].to_numpy()[pt_rows][has_return].astype(int))
    out_dep, out_arr = dep_all[out_pos], arr_all[out_pos]
    ret_dep = np.where(has_return, dep_all[ret_pos], np.nan)
    ret_arr = np.where(has_return, arr_all[ret_pos], np.nan)
    pt_origin, pt_point = origin_xy[pt_rows], point_xy[pt_rows]

    def choose(positions, o_dep, o_arr, r_dep, r_arr):
        return _gates.choose_pt_gates(
            pt_origin[positions], pt_point[positions], o_dep, o_arr, r_dep, r_arr, has_return[positions], exits,
            schedule, router, n_candidates=gate_settings["n_candidates"],
            service_window_s=gate_settings["service_window_s"], inside_stop_ids=inside_stop_ids,
            walk_max_m=walk_max_m)

    pt_gates = choose(np.arange(n_pt), out_dep, out_arr, ret_dep, ret_arr)

    def probe(positions, shifted_out_dep, shifted_ret_dep):
        # The arrivals move by the same amount as the departures (the trip durations are unchanged).
        positions = np.asarray(positions, dtype=int)
        ret_here = has_return[positions]
        return choose(positions, shifted_out_dep, out_arr[positions] + (shifted_out_dep - out_dep[positions]),
                      np.where(ret_here, shifted_ret_dep, np.nan),
                      np.where(ret_here, ret_arr[positions] + (shifted_ret_dep - ret_dep[positions]), np.nan))

    # Only stays without a gate at the planned times can end up kept, so only they need a fallback gate.
    fallback_gates, all_stops = _stay_fallback_gates(pt_origin, pt_point, exits,
                                                     pt_gates["gate_id"].isna().to_numpy())
    out_modes, out_trips, out_gates = _no_connection.resolve_no_connection(
        stays, trips, modes, availability, pt_gates, probe, fallback_gates=fallback_gates, **chain)

    actions = out_modes["no_connection_action"].to_numpy()[pt_rows]
    kept = actions == _no_connection.ACTION_KEPT
    n_kept = int(kept.sum())
    n_kept_all_stops = int(all_stops[kept].sum())
    logger.info("%s kept pt stays %d: nearest directional exit stop %d/%d (primary), all-stops fallback %d/%d "
                "(%.1f%%)", _LOG_TAG, n_kept, n_kept - n_kept_all_stops, n_kept, n_kept_all_stops, n_kept,
                100.0 * n_kept_all_stops / max(n_kept, 1))
    shifted = actions == _no_connection.ACTION_DEPARTURE_SHIFTED
    new_dep = out_trips["departure_time"].to_numpy(dtype=float)
    shift_s = np.maximum(np.abs(new_dep[out_pos] - out_dep), np.where(has_return, np.abs(new_dep[ret_pos] - ret_dep), 0.0))
    return out_modes, out_trips, out_gates, {
        "n_pt_stays": int(n_pt), "pt_gate_reasons": _counts(pt_gates["reason"]),
        "no_connection_actions": _counts(actions),
        "n_departure_shifted_s_median": float(np.median(shift_s[shifted])) if shifted.any() else None,
        "n_kept_fallback_all_stops": int(n_kept_all_stops)}


def _counts(values) -> dict:
    """Value counts as a plain ``{str: int}`` dict (JSON-safe, order of first appearance by size)."""
    return {str(key): int(count) for key, count in pd.Series(values).value_counts().items()}


def _log_report(report, warn_share, fallback_warn_share, no_connection_warn_share):
    n_stays = max(report["n_stays"], 1)
    n_drawn = max(report["n_external_point_drawn"], 1)
    logger.info("%s %d portal legs -> %d outside stays for %d/%d persons (%.2f%%); %d inner legs removed; "
                "stays without return %d; by kind %s; by mode %s", _LOG_TAG, report["n_portal_legs"],
                report["n_stays"], report["n_persons_with_stay"], report["n_persons"],
                100.0 * report["n_persons_with_stay"] / max(report["n_persons"], 1), report["n_removed_legs"],
                report["n_stays_without_return"], report["n_stays_by_kind"], report["n_stays_by_mode"])
    top = sorted(report["gate_usage"].items(), key=lambda item: -item[1])[:10]
    logger.info("%s gate usage (top 10 of %d): %s", _LOG_TAG, len(report["gate_usage"]), top)
    band_share = report["n_external_band_miss"] / n_drawn
    log = logger.warning if band_share > fallback_warn_share else logger.info
    log("%s external point drawn outside the distance band (nearest point used) %d/%d (%.2f%%; warn above %.0f%%); "
        "primary path: %d stays use the assigned work/education location", _LOG_TAG, report["n_external_band_miss"],
        report["n_external_point_drawn"], 100.0 * band_share, 100.0 * fallback_warn_share,
        report["n_stays"] - report["n_external_point_drawn"])
    proxied_share = report["n_origin_proxied_by_home"] / n_stays
    log = logger.warning if proxied_share > fallback_warn_share else logger.info
    log("%s origin proxied by home %d/%d (%.2f%%; warn above %.0f%%)", _LOG_TAG, report["n_origin_proxied_by_home"],
        report["n_stays"], 100.0 * proxied_share, 100.0 * fallback_warn_share)
    logger.info("%s re-entry clamped %d/%d (%.2f%%); donor return mode differs %d", _LOG_TAG,
                report["n_reentry_clamped"], report["n_stays"], 100.0 * report["n_reentry_clamped"] / n_stays,
                report["n_return_mode_differs"])
    n_with_return = max(report["n_stays"] - report["n_stays_without_return"], 1)
    return_degenerate_share = report["n_return_share_degenerate"] / n_with_return
    log = logger.warning if return_degenerate_share > fallback_warn_share else logger.info
    median = report["return_share_out_median"]
    log("%s return share degenerate (share 0.5, all distances zero) %d/%d (%.2f%%; warn above %.0f%%); median share "
        "outside %s", _LOG_TAG, report["n_return_share_degenerate"], n_with_return, 100.0 * return_degenerate_share,
        100.0 * fallback_warn_share, "n/a" if median is None else "%.2f" % median)
    outbound_degenerate_share = report["n_outbound_share_degenerate"] / n_stays
    log = logger.warning if outbound_degenerate_share > fallback_warn_share else logger.info
    log("%s outbound share degenerate (share 0.5, all distances zero) %d/%d (%.2f%%; warn above %.0f%%)", _LOG_TAG,
        report["n_outbound_share_degenerate"], report["n_stays"], 100.0 * outbound_degenerate_share,
        100.0 * fallback_warn_share)
    if report["n_stay_persons_missing_license"]:
        logger.warning("%s %d stay persons have no has_license value: treated as unlicensed for the car check",
                       _LOG_TAG, report["n_stay_persons_missing_license"])
    if report["n_unknown_donor_mode"]:
        logger.warning("%s %d stays have a donor outbound mode the mode check does not know (%s); kept unchecked",
                       _LOG_TAG, report["n_unknown_donor_mode"], report["unknown_donor_modes"])
    n_pt = report["n_pt_stays"]
    if n_pt:
        found = report["no_connection_actions"].get(_no_connection.ACTION_GATE_FOUND, 0)
        share_unresolved = (n_pt - found) / n_pt
        log = logger.warning if share_unresolved > no_connection_warn_share else logger.info
        log("%s pt stays without a timetable-aware gate %d/%d (%.2f%%; warn above %.0f%%); primary path: gate_found "
            "%d; gate reasons %s; actions %s", _LOG_TAG, n_pt - found, n_pt, 100.0 * share_unresolved,
            100.0 * no_connection_warn_share, found,
            {k: f"{v}/{n_pt} ({100.0 * v / n_pt:.1f}%)" for k, v in report["pt_gate_reasons"].items()},
            {k: f"{v}/{n_pt} ({100.0 * v / n_pt:.1f}%)" for k, v in report["no_connection_actions"].items()})
    share = report["n_mode_substituted"] / n_stays
    log = logger.warning if share > warn_share else logger.info
    log("%s mode substituted for %d/%d stays (%.2f%%; warn above %.0f%%): %s", _LOG_TAG,
        report["n_mode_substituted"], n_stays, 100.0 * share, 100.0 * warn_share,
        report["mode_substitution_reasons"])


def execute(context):
    trips = context.stage(TRIPS_STAGE)
    if not context.config(KEY_ENABLED):
        logger.info("%s disabled: trips.final passed through unchanged", _LOG_TAG)
        return {"trips": trips, "anchors": rewrite.empty_anchors(None), "report": {"enabled": False}}
    from braunschweig.data.transit.connections import route_batch, write_router_config
    from braunschweig.data.transit.schedule import read_schedule
    from braunschweig.data.spatial.cordon import build_cordon_polygon, buffer_m_from_fraction

    df_muni = context.stage("data.spatial.municipalities")
    # Before the first spatial step (the cordon polygon is built from the municipalities).
    df_home = context.stage("synthesis.population.spatial.home.locations")
    gates = context.stage("braunschweig.synthesis.cordon_gates")["gates"]
    links = context.stage("braunschweig.data.cordon_network")
    external_points = context.stage("braunschweig.data.external_secondary_points")
    assert_consistent_crs({"municipalities": df_muni, "df_home": df_home, "gates": gates, "links": links,
                           "external_points": external_points})
    cordon_polygon = build_cordon_polygon(
        df_muni, buffer_m_from_fraction(df_muni, float(context.config("cordon_network_buffer_fraction"))))
    supply = context.stage("matsim.scenario.supply.processed")
    schedule_path = os.path.join(context.path("matsim.scenario.supply.processed"), supply["schedule_path"])
    schedule = read_schedule(schedule_path)
    # The batch pt router runs on the pre-cut network and schedule (the stay's in-region end may lie in the
    # buffer ring) with the vehicles of the gtfs stage; its work files live in the stage directory.
    supply_dir = context.path("matsim.scenario.supply.processed")
    gtfs_dir = context.path("matsim.scenario.supply.gtfs")
    vehicles_path = os.path.join(gtfs_dir, context.stage("matsim.scenario.supply.gtfs")["vehicles_path"])
    config_path = write_router_config(
        os.path.join(context.path(), ROUTER_CONFIG_NAME), network_path=os.path.join(supply_dir, supply["network_path"]),
        schedule_path=schedule_path, vehicles_path=vehicles_path)
    router = functools.partial(
        route_batch, context, config_path=config_path,
        threads=int(context.config("matsim_threads") or context.config("processes")),
        work_dir=os.path.join(context.path(), ROUTER_WORK_DIR_NAME))
    df_work, df_education = context.stage("synthesis.population.spatial.primary.locations")
    out = build_portal_trips(
        trips=trips, persons=context.stage(PERSONS_STAGE), df_home=df_home, df_work=df_work,
        df_education=df_education, gates=gates, links=links, external_points=external_points, schedule=schedule,
        router=router, pt_gate_modes=context.config(KEY_PT_GATE_MODES),
        gate_settings={"n_candidates": int(context.config(KEY_GATE_CANDIDATES)),
                       "service_window_s": float(context.config(KEY_GATE_SERVICE_WINDOW_S)),
                       "shift_window_s": float(context.config(KEY_SHIFT_WINDOW_S)),
                       "shift_step_s": float(context.config(KEY_SHIFT_STEP_S)),
                       "min_stay_s": float(context.config(KEY_MIN_STAY_S)),
                       "no_connection_warn_share": float(context.config(KEY_NO_CONNECTION_WARN_SHARE)),
                       "shift_return_earlier": bool(context.config(KEY_SHIFT_RETURN_EARLIER)),
                       "no_connection_last_resort": context.config(KEY_NO_CONNECTION_LAST_RESORT)},
        cordon_polygon=cordon_polygon,
        threshold_m=float(context.config(KEY_MAX_ROUTABLE_DISTANCE_M)),
        tolerance=float(context.config(KEY_EXTERNAL_POINT_DISTANCE_TOLERANCE)),
        warn_share=float(context.config(KEY_MODE_SUBSTITUTION_WARN_SHARE)),
        fallback_warn_share=float(context.config(KEY_FALLBACK_WARN_SHARE)), seed=int(context.config("random_seed")))
    out["report"]["enabled"] = True
    # The report is also a file in the stage directory so the A/B run manifest can cite it without the log.
    report_path = os.path.join(context.path(), REPORT_FILE_NAME)
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(out["report"], handle, indent=2, default=_json_default)
    logger.info("%s report written to %s", _LOG_TAG, report_path)
    return out
