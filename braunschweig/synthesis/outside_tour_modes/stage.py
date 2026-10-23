"""synpp stage: plausible modes for the tours frozen at the cordon (eqasim-bs#453, ADR-0145).

Reads the reporting day after the portal rewrite (``synthesis.population.trips.final``), the realised activity
locations, the enriched persons and the municipalities; returns the trips frame with the re-moded tours.
Aliased as ``synthesis.population.trips.simulated`` for the population writer and the synthesis output; the
chain solver keeps reading ``trips.final``, so there is no cycle. With the flag off the input frame is
returned as is (byte-identical OFF path, no counters, no warning). The computation is
``remode_outside_tours``, a pure function over frames.
"""
from __future__ import annotations

import collections
import hashlib
import json
import logging
import os

import numpy as np
import pandas as pd

from braunschweig import synpp_tokens
from braunschweig.calibration import srv_mode_by_distance_band_table as _srv_table_module
from braunschweig.synthesis.outside_tour_modes import config_keys as _config_keys
from braunschweig.synthesis.outside_tour_modes import pt_connections as _pt_connections
from braunschweig.synthesis.outside_tour_modes import rule as _rule
from braunschweig.synthesis.outside_tour_modes import srv_table as _srv_table
from braunschweig.synthesis.outside_tour_modes import tours as _tours
from braunschweig.synthesis.outside_tour_modes.config_keys import (
    DEFAULT_ENABLED, DEFAULT_MIN_SHARE, DEFAULT_WARN_SHARE, FINAL_TRIPS_STAGE, KEY_ENABLED, KEY_MIN_SHARE,
    KEY_WARN_SHARE, LOCATIONS_STAGE, MUNICIPALITIES_STAGE, PERSONS_STAGE, REPORT_FILE_NAME, RNG_OFFSET,
    validate_settings)
from braunschweig.synthesis.outside_tour_modes.srv_table import ModeShareTable
from braunschweig.matsim.scenario import day_end_margin as _day_end_margin
from braunschweig.synthesis import incommuter_pt_connections as _incommuter_pt_connections
from braunschweig.synthesis.portal_trips import config_keys as _portal_config_keys
from braunschweig.synthesis.portal_trips import modes as _portal_modes
from braunschweig.synthesis.portal_trips import rewrite as _portal_rewrite
from braunschweig.synthesis.portal_trips.rewrite import PORTAL_LEG_COLUMN

logger = logging.getLogger(__name__)

_LOG_TAG = "[outside_tour_modes]"
SRV_SUBDIR = ("braunschweig", "srv")
REQUIRED_TRIP_COLUMNS = ("person_id", "trip_index", "preceding_purpose", "following_purpose", "mode")
#: The pre-cut supply the timetable check routes on (the prepare routing uses the same network and schedule).
SUPPLY_STAGE = "matsim.scenario.supply.processed"
GTFS_STAGE = "matsim.scenario.supply.gtfs"
#: Work files of the batch pt router in the stage directory.
ROUTER_CONFIG_NAME = "outside_tour_pt_router_config.xml"
ROUTER_WORK_DIR_NAME = "outside_tour_pt_router"

#: Own-package helpers and the shared modules whose source shapes this stage's output (hashed by validate()).
_HELPER_MODULES = (_config_keys, _rule, _srv_table, _tours, _srv_table_module, _portal_modes, _portal_rewrite,
                   _portal_config_keys, _pt_connections, _incommuter_pt_connections, _day_end_margin)
#: Cross-package modules imported inside execute() (hashed by dotted name).
_DEFERRED_HELPER_MODULE_NAMES = ("braunschweig.data.spatial.cordon", "braunschweig.data.transit.connections",
                                 "braunschweig.data.transit.schedule", "braunschweig.matsim.simulation.plan_check",
                                 "braunschweig.popsim.plan_validation",
                                 # reached through the plan check's imports (the closure gate of ADR-0136)
                                 "braunschweig.popsim.closure_dwell", "data.hts.hts")


def validate(context):
    """synpp validation token: the structured source token of the helpers and, with the flag on, ``files``,
    the sha256 of the committed SrV table's bytes keyed by its file name.

    Same source mechanism as ``portal_trips.stage.validate``. The table is data, which no source hash sees: a
    regenerated ``srv2023_mode_by_distance_band.csv`` must recompute this stage (and everything downstream)
    instead of serving modes judged against the old table. synpp's ValidateContext only answers declared
    options, so ``data_path`` is read only when the flag is on (configure declares it only then).
    """
    token = synpp_tokens.source_token(
        _HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES,
        stage_label=__name__ + ".validate()")
    if context.config(KEY_ENABLED):
        table_path = os.path.join(context.config("data_path"), *SRV_SUBDIR, _srv_table_module.TABLE_FILE_NAME)
        if not os.path.exists(table_path):
            raise FileNotFoundError(f"{_LOG_TAG} validate(): committed table {_srv_table_module.TABLE_FILE_NAME} "
                                    f"not found at {table_path}")
        with open(table_path, "rb") as handle:
            token["files"] = {_srv_table_module.TABLE_FILE_NAME: hashlib.sha256(handle.read()).hexdigest()}
    return token


def configure(context):
    enabled = context.config(KEY_ENABLED, DEFAULT_ENABLED)
    context.stage(FINAL_TRIPS_STAGE)
    if not enabled:
        return
    validate_settings(float(context.config(KEY_MIN_SHARE, DEFAULT_MIN_SHARE)),
                      float(context.config(KEY_WARN_SHARE, DEFAULT_WARN_SHARE)),
                      context.config(_config_keys.KEY_LAST_RESORT, _config_keys.DEFAULT_LAST_RESORT),
                      float(context.config(_config_keys.KEY_PT_NO_CONNECTION_WARN_SHARE,
                                           _config_keys.DEFAULT_PT_NO_CONNECTION_WARN_SHARE)))
    context.config("random_seed")
    context.config("data_path")
    # The cut extent IS the cordon: without one there is no outside tour to judge.
    if not context.config("cordon_enabled", False):
        raise ValueError(f"{KEY_ENABLED} is true but cordon_enabled is false: without a cut extent there is no "
                         "outside tour to judge; set the flag to false in this config")
    context.config("cordon_network_buffer_fraction", 0.10)
    context.stage(LOCATIONS_STAGE)
    context.stage(PERSONS_STAGE)
    context.stage(MUNICIPALITIES_STAGE)
    # Timetable check of the pt legs of outside tours (#465, ADR-0159): the batch pt router of
    # braunschweig.data.transit.connections on the pre-cut supply (the network and schedule the prepare routing uses).
    if context.config(_config_keys.KEY_PT_CHECK_ENABLED, _config_keys.DEFAULT_PT_CHECK_ENABLED):
        _incommuter_pt_connections.shift_offsets_s(
            float(context.config(_config_keys.KEY_PT_SHIFT_WINDOW_S, _config_keys.DEFAULT_PT_SHIFT_WINDOW_S)),
            float(context.config(_config_keys.KEY_PT_SHIFT_STEP_S, _config_keys.DEFAULT_PT_SHIFT_STEP_S)))
        context.config(_day_end_margin.KEY_ENABLED, _day_end_margin.DEFAULT_ENABLED)
        context.config(_day_end_margin.KEY_LAST_ARRIVAL_MARGIN_S, _day_end_margin.DEFAULT_LAST_ARRIVAL_MARGIN_S)
        from braunschweig.data.transit import connections  # noqa: PLC0415
        connections.configure(context)
        context.stage("matsim.runtime.eqasim")
        context.stage("matsim.runtime.java")
        # Execution details, not scientific config: the thread count must not devalidate cached stages.
        context.config("processes", volatile=True)
        context.config("matsim_threads", None, volatile=True)
        context.stage(SUPPLY_STAGE)
        context.stage(GTFS_STAGE)


def _pt_check(context) -> _pt_connections.PtCheck:
    """The timetable check of ADR-0159: the batch pt router on the pre-cut network and schedule, its connection rule
    over ALL stops of the schedule (the destination of an outside tour lies outside the cut extent), the walk limit
    of the plan check, and the shift bound = end of the simulated day minus the day-end margin (ADR-0154)."""
    import functools  # noqa: PLC0415
    from braunschweig.data.transit import connections  # noqa: PLC0415
    from braunschweig.data.transit.schedule import read_schedule  # noqa: PLC0415
    from braunschweig.matsim.simulation import plan_check  # noqa: PLC0415
    from braunschweig.popsim import plan_validation  # noqa: PLC0415

    supply = context.stage(SUPPLY_STAGE)
    supply_dir = context.path(SUPPLY_STAGE)
    schedule_path = os.path.join(supply_dir, supply["schedule_path"])
    vehicles_path = os.path.join(context.path(GTFS_STAGE), context.stage(GTFS_STAGE)["vehicles_path"])
    config_path = connections.write_router_config(
        os.path.join(context.path(), ROUTER_CONFIG_NAME), network_path=os.path.join(supply_dir, supply["network_path"]),
        schedule_path=schedule_path, vehicles_path=vehicles_path)
    router = functools.partial(
        connections.route_batch, context, config_path=config_path,
        threads=int(context.config("matsim_threads") or context.config("processes")),
        work_dir=os.path.join(context.path(), ROUTER_WORK_DIR_NAME))
    all_stop_ids = set(read_schedule(schedule_path).stops)
    walk_max_m = float(plan_check.DEFAULT_WALK_MAX_DISTANCE_M)

    def probe(tasks):
        trips, legs = router(tasks)
        return connections.connection_mask(trips, legs, inside_stop_ids=all_stop_ids, walk_max_m=walk_max_m,
                                           task_ids=tasks["identifier"].astype(str))

    bound_s = float(plan_validation.MAX_PLAN_TIME_SECONDS)
    if context.config(_day_end_margin.KEY_ENABLED):
        bound_s -= _day_end_margin.check_margin(context.config(_day_end_margin.KEY_LAST_ARRIVAL_MARGIN_S), bound_s)
    return _pt_connections.PtCheck(
        probe=probe, shift_window_s=float(context.config(_config_keys.KEY_PT_SHIFT_WINDOW_S)),
        shift_step_s=float(context.config(_config_keys.KEY_PT_SHIFT_STEP_S)), bound_s=bound_s,
        warn_share=float(context.config(_config_keys.KEY_PT_NO_CONNECTION_WARN_SHARE)))


def _load_table(srv_dir) -> ModeShareTable:
    return ModeShareTable.from_directory(srv_dir)


def _json_default(value):
    """JSON encoder hook for numpy scalars and arrays in the report."""
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, np.ndarray):
        return value.tolist()
    raise TypeError(f"{type(value).__name__} is not JSON serialisable")


def remode_outside_tours(*, trips: pd.DataFrame, locations, persons: pd.DataFrame, polygon, table: ModeShareTable,
                         min_share: float, warn_share: float, seed: int, municipalities_crs=None,
                         last_resort: str = _pt_connections.LAST_RESORT_KEEP,
                         pt_check: _pt_connections.PtCheck | None = None) -> tuple:
    """The re-moded trips frame and the report (pure apart from the ``pt_check`` probe; see the module docstring).

    The returned frame keeps the input's row order, index and columns; only ``mode`` values of re-moded tours
    change, and with ``pt_check`` the modes and times of the outside tours whose pt legs have no timetable
    connection (``pt_connections.resolve_pt_tours``, ADR-0159). The computation runs on a ``person_id,
    trip_index``-sorted view (stable sort) and writes back by index. ``municipalities_crs``, when given, must equal
    the CRS of ``locations`` (pyproj equality). ``last_resort`` (``keep`` in this function, the stage passes the
    configured value) decides what happens to a road-gate portal tour without an admissible mode and to a pt tour
    without a connection: ``car_passenger`` makes it a lift, ``keep`` keeps it.
    """
    if last_resort not in _pt_connections.LAST_RESORTS:
        raise ValueError(f"{_LOG_TAG} invalid last_resort {last_resort!r}; allowed: {list(_pt_connections.LAST_RESORTS)}")
    missing = [column for column in REQUIRED_TRIP_COLUMNS if column not in trips.columns]
    if missing:
        raise ValueError(f"{_LOG_TAG} trips frame lacks the columns {missing}")
    if not trips.index.is_unique:
        raise ValueError(f"{_LOG_TAG} the trips frame index is not unique; modes could not be written back")
    # pyproj CRS equality (never a string or set comparison: the same EPSG code can print differently).
    if municipalities_crs is not None and locations.crs != municipalities_crs:
        raise ValueError(f"{_LOG_TAG} CRS mismatch: locations {locations.crs} versus municipalities "
                         f"{municipalities_crs}; both must be in the pipeline CRS")
    _rule.require_plain_mode_column(trips)
    table.assert_always_available_mode_plausible(min_share)
    ordered = trips.sort_values(["person_id", "trip_index"], kind="mergesort")
    tour_ids = _tours.home_based_tour_ids(ordered)
    endpoints = _tours.leg_endpoints(ordered, locations)
    leg_km = _tours.leg_distance_m(endpoints) / 1000.0
    dest_outside = _tours.destination_outside(endpoints, polygon)
    outside = _tours.outside_tour_flags(ordered, tour_ids, dest_outside)
    groups = _rule.purpose_groups(ordered)
    cells = _rule.leg_cells(groups, leg_km, table, mask=outside)
    implausible = _rule.implausible_legs(ordered, cells, min_share)
    # Availability is only needed for the persons that get a substitute; the per-person dict build is a Python
    # loop, so it is restricted to them (the result is identical to building it for everybody).
    affected_persons = ordered["person_id"].to_numpy()[outside & implausible]
    availability = _portal_modes.mode_availability(persons)
    allowed = _rule.allowed_modes(availability.loc[availability.index.isin(np.unique(affected_persons))])
    rng = np.random.RandomState(int(seed) + RNG_OFFSET)
    # A trips frame without the portal flag column is treated as having no portal legs; the report states which
    # case applied and the log warns when outside activities exist although the flag is absent.
    portal_column_present = PORTAL_LEG_COLUMN in ordered.columns
    portal_leg = (ordered[PORTAL_LEG_COLUMN].eq(True).to_numpy(dtype=bool) if portal_column_present
                  else np.zeros(len(ordered), dtype=bool))
    substitutes = _rule.tour_substitutes(ordered, tour_ids, outside, implausible, leg_km, cells, allowed, min_share,
                                         rng, portal_leg=portal_leg,
                                         lift_without_candidate=last_resort == _pt_connections.LAST_RESORT_LIFT)
    unchanged = substitutes[substitutes["unchanged_reason"] != ""]
    substitutes = substitutes[substitutes["unchanged_reason"] == ""]
    remoded, n_remoded, n_swept = _rule.apply_tour_modes(ordered, tour_ids, substitutes, implausible)
    donor_modes = ordered["mode"].astype(str)
    unknown_donor = outside & ~donor_modes.isin(_srv_table_module.MODES).to_numpy()
    pt_report = None
    if pt_check is not None:
        # After the re-moding, so a drawn pt substitute is checked too; portal tours keep their own check.
        portal_tour = pd.Series(portal_leg).groupby([ordered["person_id"].to_numpy(), tour_ids]).transform("any")
        resolution = _pt_connections.resolve_pt_tours(
            trips=remoded, tour_ids=tour_ids, outside=outside, portal_tour=portal_tour.to_numpy(dtype=bool),
            endpoints=endpoints, availability=_portal_modes.mode_availability(persons), probe=pt_check.probe,
            shift_window_s=pt_check.shift_window_s, shift_step_s=pt_check.shift_step_s, bound_s=pt_check.bound_s,
            last_resort=last_resort)
        remoded = resolution.trips
        pt_report = resolution.report
        _pt_connections.log_report(pt_report, warn_share=pt_check.warn_share)
    # Post-condition: outside legs whose FINAL mode (after the draw and the pt check) is still below min_share in
    # their cell (only the relaxed draws, the tours the draw could not make plausible everywhere and the tours the
    # pt check switched to a mode the cell rarely uses can remain).
    implausible_after = _rule.implausible_legs(remoded, cells, min_share)
    out = trips.copy()
    out.loc[remoded.index, "mode"] = remoded["mode"]
    if pt_report is not None and (pt_report["actions"][_pt_connections.ACTION_TOUR_SHIFTED]
                                  or pt_report["n_persons_day_end_shifted"]):
        out.loc[remoded.index, "departure_time"] = remoded["departure_time"]
        out.loc[remoded.index, "arrival_time"] = remoded["arrival_time"]

    legs = pd.DataFrame({"person_id": ordered["person_id"].to_numpy(), "tour": tour_ids, "outside": outside,
                         "group": groups.to_numpy(), "km": leg_km})
    n_tours = int(len(legs[["person_id", "tour"]].drop_duplicates()))
    longest = (legs[legs["outside"]].sort_values("km", ascending=False, kind="mergesort")
               .drop_duplicates(["person_id", "tour"]))
    n_outside_tours = int(len(longest))
    matrix = collections.Counter(f"{row.donor_modes}->{row.substitute}" for row in substitutes.itertuples())
    removed_by_mode = collections.Counter(mode for removed in substitutes["removed_modes"]
                                          for mode in str(removed).split(",") if mode)
    n_relaxed = int(substitutes["relaxed_to_longest_leg"].sum())
    report = {
        "enabled": True, "min_share": float(min_share), "warn_share": float(warn_share),
        "n_persons": int(ordered["person_id"].nunique()), "n_legs": int(len(ordered)), "n_tours": n_tours,
        "n_outside_tours": n_outside_tours,
        "n_outside_tours_by_group": {str(k): int(v) for k, v in longest["group"].value_counts().items()},
        "n_outside_tours_remoded": int(len(substitutes)),
        "share_outside_tours_remoded": float(len(substitutes) / n_outside_tours) if n_outside_tours else 0.0,
        "substitution_matrix": dict(matrix), "n_legs_remoded": int(n_remoded), "n_legs_swept": int(n_swept),
        "n_portal_legs_as_all_group": int((groups.to_numpy() == _srv_table_module.ALL_GROUP).sum()),
        "n_draws_restricted_by_availability": int((substitutes["n_removed_by_availability"] > 0).sum()),
        "n_removed_by_availability_by_mode": dict(removed_by_mode),
        "n_outside_legs": int(outside.sum()),
        "n_draws_relaxed_to_longest_leg": n_relaxed,
        "share_draws_relaxed_to_longest_leg": float(n_relaxed / len(substitutes)) if len(substitutes) else 0.0,
        "n_outside_legs_implausible_after": int(implausible_after.sum()),
        "portal_leg_column_present": bool(portal_column_present),
        "n_portal_tours_remoded": int(substitutes["portal_tour"].sum()),
        "n_portal_tours_class_restricted": int((substitutes["portal_tour"]
                                                & (substitutes["n_removed_by_portal_class"] > 0)).sum()),
        "n_portal_tours_left_unchanged": int(len(unchanged)),
        "n_portal_tours_lifted": int(substitutes["lifted_without_availability"].sum()),
        "last_resort": last_resort,
        "pt_connection": pt_report,
        "portal_tours_left_unchanged_by_reason": {
            reason: int((unchanged["unchanged_reason"] == reason).sum()) for reason in _rule.UNCHANGED_REASONS},
        "share_portal_tours_left_unchanged": float(
            len(unchanged) / (len(unchanged) + int(substitutes["portal_tour"].sum()))) if len(unchanged) else 0.0,
        "n_unknown_donor_mode": int(unknown_donor.sum()),
        "unknown_donor_modes": {str(k): int(v) for k, v in donor_modes[unknown_donor].value_counts().items()},
        "cells_used": {str(k): int(v) for k, v in substitutes["cell"].value_counts().items()},
    }
    _log_report(report)
    return out, report


def _log_report(report: dict) -> None:
    share = report["share_outside_tours_remoded"]
    log = logger.warning if share > report["warn_share"] else logger.info
    log("%s outside tours re-moded %d/%d (%.2f%%; warn above %.0f%%) of %d tours, %d persons; legs re-moded %d, "
        "swept along %d; substitutions %s; cells %s", _LOG_TAG, report["n_outside_tours_remoded"],
        report["n_outside_tours"], 100.0 * share, 100.0 * report["warn_share"], report["n_tours"],
        report["n_persons"], report["n_legs_remoded"], report["n_legs_swept"], report["substitution_matrix"],
        report["cells_used"])
    logger.info("%s outside tours by purpose group %s; portal legs judged in group all/top band %d; draws "
                "restricted by availability %d (removed by mode %s)", _LOG_TAG, report["n_outside_tours_by_group"],
                report["n_portal_legs_as_all_group"], report["n_draws_restricted_by_availability"],
                report["n_removed_by_availability_by_mode"])
    logger.info("%s portal tours re-moded %d (gate class restricted the candidates in %d); draws relaxed to the cell "
                "of the longest leg %d (%.2f%% of the draws)", _LOG_TAG, report["n_portal_tours_remoded"],
                report["n_portal_tours_class_restricted"], report["n_draws_relaxed_to_longest_leg"],
                100.0 * report["share_draws_relaxed_to_longest_leg"])
    if report["n_portal_tours_lifted"]:
        logger.warning("%s %d road-gate portal tours had no admissible mode and became a lift (car_passenger without "
                       "the passenger availability, last resort %s)", _LOG_TAG, report["n_portal_tours_lifted"],
                       report["last_resort"])
    if report["n_portal_tours_left_unchanged"]:
        logger.warning("%s %d portal tours left unchanged (%.2f%% of the portal tours with an implausible leg; the "
                       "gate class left no admissible mode or the tour has portal legs at both gate classes): %s",
                       _LOG_TAG, report["n_portal_tours_left_unchanged"],
                       100.0 * report["share_portal_tours_left_unchanged"],
                       report["portal_tours_left_unchanged_by_reason"])
    if report["n_outside_legs_implausible_after"]:
        logger.warning("%s %d of %d outside legs are still implausible after the draw (relaxed draws judged in the "
                       "cell of the longest leg only)", _LOG_TAG, report["n_outside_legs_implausible_after"],
                       report["n_outside_legs"])
    if report["n_unknown_donor_mode"]:
        logger.warning("%s %d outside legs carry a donor mode unknown to the SrV table (judged implausible and "
                       "re-moded): %s", _LOG_TAG, report["n_unknown_donor_mode"], report["unknown_donor_modes"])
    if not report["portal_leg_column_present"] and report["n_portal_legs_as_all_group"]:
        logger.warning("%s the trips frame has no %s column although %d legs touch an outside activity: the portal "
                       "gate class cannot be respected", _LOG_TAG, PORTAL_LEG_COLUMN,
                       report["n_portal_legs_as_all_group"])


def execute(context):
    trips = context.stage(FINAL_TRIPS_STAGE)
    if not context.config(KEY_ENABLED):
        logger.info("%s disabled: trips.final passed through unchanged", _LOG_TAG)
        return trips
    from braunschweig.data.spatial.cordon import build_cordon_polygon, buffer_m_from_fraction

    municipalities = context.stage(MUNICIPALITIES_STAGE)
    locations = context.stage(LOCATIONS_STAGE)
    polygon = build_cordon_polygon(
        municipalities, buffer_m_from_fraction(municipalities, float(context.config("cordon_network_buffer_fraction"))))
    table = _load_table(os.path.join(context.config("data_path"), *SRV_SUBDIR))
    out, report = remode_outside_tours(
        trips=trips, locations=locations, persons=context.stage(PERSONS_STAGE), polygon=polygon, table=table,
        min_share=float(context.config(KEY_MIN_SHARE)), warn_share=float(context.config(KEY_WARN_SHARE)),
        seed=int(context.config("random_seed")), municipalities_crs=municipalities.crs,
        last_resort=context.config(_config_keys.KEY_LAST_RESORT),
        pt_check=_pt_check(context) if context.config(_config_keys.KEY_PT_CHECK_ENABLED) else None)
    report_path = os.path.join(context.path(), REPORT_FILE_NAME)
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=_json_default)
    logger.info("%s report written to %s", _LOG_TAG, report_path)
    return out
