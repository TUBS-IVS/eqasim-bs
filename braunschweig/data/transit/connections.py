"""Batch public transport router wrapper and the timetable-connection rule (#465, WP-C).

Runs the eqasim production pt router (``RunBatchPublicTransportRouter``, a Java tool) on a
table of origin/destination/departure tasks and decides, per routed task, whether the
result is a usable timetable connection. Later tasks use it to check which candidate gate
stops are reachable from a pt portal stay's in-region end at the planned time.

Connection rule (design decisions 2 and 4, builder-check resolution 4). A task is a
connection when

* the router found a pt trip (``is_only_walk == 0``) with at least one pt leg, every pt leg's
  access and egress stop is non-blank and lies in ``inside_stop_ids`` (the cut extent), and
  access, egress and transfer walking are each at most ``walk_max_m``; or
* the router returned only a walk whose total walk distance is at most
  ``plausible_walk_m`` (the origin is within plausible walking distance of the
  destination, so no timetable connection is needed).

Walk legs of a pt trip. The Java tool books the first walk leg as access, the last as egress and every
other walk leg as transfer walking. Up to eqasim-java-bs 863c09a ``Worker.run`` found the last leg by comparing
a leg counter with ``planElements.size()``, which would also count interaction activities; SwissRailRaptor
without intermodal access returns legs only, so the comparison was right in practice: every pt trip
(``is_only_walk == 0``) of the #465 router probe on felix has a positive egress distance (``trips_out.csv`` 8,838
and ``gate_out.csv`` 192 in ``~/wt-i453-ab/router_probe``; minimum 0.011 km). The
eqasim-java-bs fix "classify batch router walk legs by their position among the legs" makes the
classification independent of interaction activities (no change for legs-only router output).

A task for which the router finds no route gets NO trips row (``Worker.run`` adds a row only when
``calcRoute`` returns a result): that is a router outcome "no route", not a broken run, and counts as
not a connection (``connection_mask(..., task_ids=...)``, reason ``no_route``).

Coordinates are metric (the scenario CRS); distances in the router output are kilometres,
the input departure time is in seconds and the output times are in minutes.
"""
from __future__ import annotations

import logging
import os
from collections.abc import Callable
from xml.sax.saxutils import quoteattr

import numpy as np
import pandas as pd

import matsim.runtime.eqasim as eqasim
import matsim.runtime.java as java
from braunschweig.popsim.plan_validation import MAX_PLAN_TIME_SECONDS

logger = logging.getLogger(__name__)

TASK_COLUMNS = ["identifier", "origin_x", "origin_y", "destination_x", "destination_y", "departure_time"]
_NUMERIC_TASK_COLUMNS = TASK_COLUMNS[1:]

ROUTER_CLASS = "org.eqasim.core.tools.routing.RunBatchPublicTransportRouter"

#: Columns of the router outputs that ``connection_mask`` reads. The leg stop ids exist only in an eqasim
#: jar built from the fork change (eqasim-java-bs branch ``feat/batch-router-leg-stops``).
TRIPS_REQUIRED_COLUMNS = ["identifier", "is_only_walk", "access_distance_km", "egress_distance_km",
                          "transfer_distance_km", "total_walk_distance_km"]
LEGS_REQUIRED_COLUMNS = ["identifier", "access_stop_id", "egress_stop_id"]
_JAR_HINT = ("the eqasim jar probably lacks the fork change (eqasim-java-bs branch "
             "feat/batch-router-leg-stops, \"write access and egress stop ids per leg in the batch public "
             "transport router\"); rebuild the jar from that branch")

#: Share of tasks without a trips row above which the rate is logged as a WARNING.
NO_ROUTE_WARN_SHARE = 0.5

#: A router maps a task frame (``TASK_COLUMNS``) to ``(trips, legs)`` frames; the stage binds
#: ``functools.partial(route_batch, context, config_path=..., threads=..., work_dir=...)``.
Router = Callable[[pd.DataFrame], tuple[pd.DataFrame, pd.DataFrame]]

#: Walk teleportation parameters copied from ``org.eqasim.core.scenario.config.GenerateConfig``
#: (eqasim-java-bs): beeline distance factor 1.3, speed 1.2 m/s (4.32 km/h).
WALK_BEELINE_DISTANCE_FACTOR = 1.3
WALK_TELEPORTED_SPEED_M_S = 1.2

_CONFIG_TEMPLATE = """<?xml version="1.0" encoding="UTF-8" ?>
<!DOCTYPE config SYSTEM "http://www.matsim.org/files/dtd/config_v2.dtd">
<config>
	<module name="network">
		<param name="inputNetworkFile" value={network} />
	</module>
	<module name="transit">
		<param name="useTransit" value="true" />
		<param name="transitScheduleFile" value={schedule} />
		<param name="vehiclesFile" value={vehicles} />
	</module>
	<module name="routing">
		<parameterset type="teleportedModeParameters">
			<param name="mode" value="walk" />
			<param name="beelineDistanceFactor" value="{walk_factor}" />
			<param name="teleportedModeSpeed" value="{walk_speed}" />
		</parameterset>
	</module>
	<module name="qsim">
		<param name="personInitializedEvents" value="all" />
		<param name="endTime" value="{end_time}" />
	</module>
	<module name="transitRouter">
	</module>
</config>
"""


def configure(context) -> None:
    """Declare the config options that ``route_batch`` needs at execute.

    ``route_batch`` runs the router through ``eqasim.run`` -> ``java.run``, whose Java options (binary,
    memory, hang watchdog) are volatile and not passed downstream by synpp (#330). Every stage that calls
    ``route_batch`` must call this from its own ``configure``.
    """
    java.configure(context)


def _hhmmss(seconds: int) -> str:
    """MATSim time string ``HH:MM:SS`` (hours may exceed 24)."""
    return "%02d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)


def write_router_config(path: str, *, network_path: str, schedule_path: str, vehicles_path: str) -> str:
    """Write a minimal MATSim config for the batch pt router and return ``path``.

    Only network, transit schedule and transit vehicles are named; ``transitRouter`` keeps
    its MATSim defaults. An existing file at ``path`` is overwritten (the config is derived).

    The ``qsim`` values do not affect routing; the tool needs them because it builds the full eqasim
    injector: ``EqasimAnalysisModule`` refuses ``personInitializedEvents`` other than ``all``, and
    ``TravelTimeRecorder`` sizes its time bins by ``endTime`` (unset means ``Double.MAX_VALUE`` and an
    ``OutOfMemoryError``). ``endTime`` is the simulated day, ``MAX_PLAN_TIME_SECONDS`` (30 h).
    """
    for label, file in (("network", network_path), ("schedule", schedule_path), ("vehicles", vehicles_path)):
        if not file:
            raise ValueError(f"write_router_config: {label} path is empty")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # quoteattr escapes &, <, > and quotes and adds the surrounding quotes (paths may contain any of them).
    text = _CONFIG_TEMPLATE.format(network=quoteattr(network_path), schedule=quoteattr(schedule_path),
                                   vehicles=quoteattr(vehicles_path),
                                   walk_factor=WALK_BEELINE_DISTANCE_FACTOR,
                                   walk_speed=WALK_TELEPORTED_SPEED_M_S,
                                   end_time=_hhmmss(MAX_PLAN_TIME_SECONDS))
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def _require_columns(frame: pd.DataFrame, required: list[str], label: str, caller: str) -> None:
    """``ValueError`` naming the missing router-output columns (and the likely cause: a jar without the fork change)."""
    absent = [c for c in required if c not in frame.columns]
    if absent:
        raise ValueError(f"{caller}: the router {label} output lacks the columns {absent}; {_JAR_HINT}")


def route_batch(context, tasks: pd.DataFrame, *, config_path: str, threads: int,
                work_dir: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Route ``tasks`` with the Java batch pt router; return ``(trips, legs)`` frames.

    ``tasks`` must carry ``TASK_COLUMNS`` (metric coordinates, ``departure_time`` in seconds).
    Non-finite coordinates raise ``ValueError`` BEFORE Java is started. Input and outputs are
    written to ``work_dir`` (``router_tasks.csv``, ``router_trips.csv``, ``router_legs.csv``).
    Identifier and stop-id columns are read as strings (the router writes legs rows for pt legs
    only). ``ValueError`` for duplicate task identifiers (before Java runs), duplicate or UNEXPECTED trip
    identifiers, a router output without the columns ``connection_mask`` needs (the stop-id columns need the
    fork jar), and when the router returns no trips row at all.

    A task without a trips row is a router outcome, not an error: Java adds a trips row only when
    ``calcRoute`` returns a result, so "no route" leaves no row. Their count is logged as a rate (INFO,
    WARNING above ``NO_ROUTE_WARN_SHARE``); pass the task identifiers to ``connection_mask(task_ids=...)``
    to count them as not connected. If EVERY task lacks a row the run is broken and raises.

    Router settings. The batch router's ``transitRouter`` / SwissRailRaptor parameters are the MATSim
    defaults, equal to the Braunschweig simulation's (checked 2026-10-22: no transitRouter or
    SwissRailRaptor settings in ``braunschweig/`` or ``configs``; only the Zurich and Sao Paulo
    configurators of the eqasim-java-bs fork set them). The VRB fare routing-cost override changes
    WHICH connection is chosen, not WHETHER one exists, so it does not affect the connection rule.
    The tool calls ``EqasimConfigurator.updateConfig``, which adds the non-optional groups (including
    ``SwissRailRaptorConfigGroup``) itself, so the minimal config of ``write_router_config`` loads.
    """
    missing = [c for c in TASK_COLUMNS if c not in tasks.columns]
    if missing:
        raise ValueError(f"route_batch: task table lacks columns {missing}")
    for column in _NUMERIC_TASK_COLUMNS:
        values = pd.to_numeric(tasks[column], errors="coerce").to_numpy(dtype=float)
        bad = ~np.isfinite(values)
        if bad.any():
            ids = tasks.loc[bad, "identifier"].head(5).tolist()
            raise ValueError(f"route_batch: {int(bad.sum())} task(s) have a non-finite {column} "
                             f"(first identifiers {ids}); fix the input coordinates")
    task_ids = tasks["identifier"].astype(str)
    if task_ids.duplicated().any():
        dups = task_ids[task_ids.duplicated()].head(5).tolist()
        raise ValueError(f"route_batch: duplicate task identifiers (first {dups}); "
                         "each task needs a unique identifier")
    os.makedirs(work_dir, exist_ok=True)
    tasks_path = os.path.join(work_dir, "router_tasks.csv")
    trips_path = os.path.join(work_dir, "router_trips.csv")
    legs_path = os.path.join(work_dir, "router_legs.csv")
    tasks[TASK_COLUMNS].to_csv(tasks_path, index=False)
    logger.info("[pt-router] routing %d task(s) with %d thread(s), config %s", len(tasks), threads, config_path)
    eqasim.run(context, ROUTER_CLASS, [
        "--config-path", config_path, "--input-path", tasks_path,
        "--output-trips-path", trips_path, "--output-legs-path", legs_path,
        "--threads", str(threads)])
    # Identifiers and stop ids are read as strings so numeric-looking ids ("007") round-trip unchanged.
    trips = pd.read_csv(trips_path, dtype={"identifier": str})
    legs = pd.read_csv(legs_path, dtype={"identifier": str, "access_stop_id": str, "egress_stop_id": str},
                       keep_default_na=False, na_values=[])
    _require_columns(trips, TRIPS_REQUIRED_COLUMNS, "trips", "route_batch")
    _require_columns(legs, LEGS_REQUIRED_COLUMNS, "legs", "route_batch")
    if trips["identifier"].duplicated().any():
        raise ValueError(f"route_batch: duplicate trip identifiers in {trips_path} "
                         f"(first {trips.loc[trips['identifier'].duplicated(), 'identifier'].head(5).tolist()})")
    expected = set(task_ids)
    returned = set(trips["identifier"])
    missing, unexpected = expected - returned, returned - expected
    if unexpected:
        raise ValueError(f"route_batch: router output has unexpected {len(unexpected)} identifier(s) "
                         f"(e.g. {sorted(unexpected)[:5]}) that are not tasks; see {trips_path}")
    if missing:
        share = len(missing) / len(expected)
        message = (f"[pt-router] {len(missing)}/{len(expected)} task(s) ({100.0 * share:.1f}%) have no trips row "
                   f"(the router found no route; examples {sorted(missing)[:3]})")
        if len(missing) == len(expected):
            raise ValueError(f"route_batch: router output has missing {len(missing)} of {len(expected)} tasks, "
                             f"i.e. no trips row at all; the run is broken, see {trips_path}")
        (logger.warning if share > NO_ROUTE_WARN_SHARE else logger.info)(message)
    return trips, legs


def connection_mask(trips: pd.DataFrame, legs: pd.DataFrame, *, inside_stop_ids: set[str],
                    walk_max_m: float, plausible_walk_m: float = 600.0,
                    task_ids=None) -> pd.Series:
    """Boolean Series indexed by ``identifier``: does the routed result count as a connection?

    See the module docstring for the rule. A pt trip (``is_only_walk == 0``) needs at least one
    legs row (a pt trip has >= 1 pt leg by construction, so a missing leg is a failure signal),
    and every pt leg needs a non-blank access and egress stop id inside ``inside_stop_ids``.
    A missing or NaN ``is_only_walk`` (or any value other than 0/1), and missing or NaN
    distances, count as not a connection. Duplicate trip identifiers and trips or legs frames without
    the needed columns (``TRIPS_REQUIRED_COLUMNS``, ``LEGS_REQUIRED_COLUMNS``) raise ``ValueError``.

    ``task_ids`` (optional): the identifiers that were routed. The result is then indexed by exactly these,
    in this order; a task without a trips row (the router found no route) is not a connection and is counted
    under the rejection reason ``no_route``. A trips identifier that is not in ``task_ids`` raises
    ``ValueError``.

    Only-walk distance: for an only-walk trip the Java tool (``BatchPublicTransportRouter``,
    ``isFirstLeg`` branch) books the single walk leg as ACCESS and leaves egress at 0, so
    access + egress is not a stable reading; ``total_walk_distance_km`` (access + egress +
    transfer) is the walk distance used for the plausible-short-walk test.

    The shares of connections, accepted short walks and each rejection reason are logged
    (a share near 0 or 1 signals a broken router input).
    """
    _require_columns(trips, TRIPS_REQUIRED_COLUMNS, "trips", "connection_mask")
    _require_columns(legs, LEGS_REQUIRED_COLUMNS, "legs", "connection_mask")
    if trips["identifier"].duplicated().any():
        dups = trips.loc[trips["identifier"].duplicated(), "identifier"].head(5).tolist()
        raise ValueError(f"connection_mask: duplicate trip identifiers (first {dups}); "
                         f"each task needs exactly one trips row")
    trips = trips.set_index("identifier", drop=False)
    if task_ids is not None:
        task_ids = pd.Index(pd.Series(list(task_ids), dtype=str))
        stray = trips.index.difference(task_ids)
        if len(stray):
            raise ValueError(f"connection_mask: trips identifiers {list(stray[:5])} are not in task_ids")
    walk_max_km = walk_max_m / 1000.0
    plausible_walk_km = plausible_walk_m / 1000.0

    flag = pd.to_numeric(trips["is_only_walk"], errors="coerce")
    flag_missing = ~flag.isin([0, 1])  # NaN and anything but 0/1
    only_walk = flag == 1
    pt_trip = flag == 0
    access = trips["access_distance_km"].astype(float)
    egress = trips["egress_distance_km"].astype(float)
    transfer = trips["transfer_distance_km"].astype(float)
    total_walk = trips["total_walk_distance_km"].astype(float)

    short_walk = only_walk & (total_walk <= plausible_walk_km)
    walk_ok = (access <= walk_max_km) & (egress <= walk_max_km) & (transfer <= walk_max_km)

    # Legs rows exist only for pt legs: every pt trip needs one, and none may carry a blank or outside stop.
    leg_ids = legs["identifier"].astype(str)
    access_ids = legs["access_stop_id"].fillna("").astype(str)
    egress_ids = legs["egress_stop_id"].fillna("").astype(str)
    blank_leg = (access_ids.str.strip() == "") | (egress_ids.str.strip() == "")
    outside_leg = ~blank_leg & (~access_ids.isin(inside_stop_ids) | ~egress_ids.isin(inside_stop_ids))
    has_legs = trips.index.isin(set(leg_ids))
    has_blank = trips.index.isin(set(leg_ids[blank_leg]))
    has_outside = trips.index.isin(set(leg_ids[outside_leg]))

    no_pt_legs = pt_trip & ~has_legs
    blank_stop = pt_trip & has_legs & has_blank
    outside_stop = pt_trip & has_legs & ~has_blank & has_outside
    far_walk = pt_trip & has_legs & ~has_blank & ~has_outside & ~walk_ok
    pt_ok = pt_trip & has_legs & ~has_blank & ~has_outside & walk_ok
    mask = (pt_ok | short_walk).astype(bool)
    mask.name = "is_connection"
    n_no_route = 0
    if task_ids is not None:
        n_no_route = int((~task_ids.isin(trips.index)).sum())
        mask = mask.reindex(task_ids, fill_value=False).astype(bool)
        mask.name = "is_connection"

    n = len(mask)
    if n:
        logger.info("[pt-router] connections %d/%d (%.1f%%): pt %d, plausible short walk %d; rejected: "
                    "no_route %d, is_only_walk_missing %d, only-walk too long %d, no_pt_legs %d, "
                    "blank_stop_id %d, outside stop %d, walk distance %d",
                    mask.sum(), n, 100.0 * mask.sum() / n, pt_ok.sum(), short_walk.sum(), n_no_route,
                    flag_missing.sum(), (only_walk & ~short_walk).sum(), no_pt_legs.sum(),
                    blank_stop.sum(), outside_stop.sum(), far_walk.sum())
    return mask
