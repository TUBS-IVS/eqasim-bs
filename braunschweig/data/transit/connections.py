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

logger = logging.getLogger(__name__)

TASK_COLUMNS = ["identifier", "origin_x", "origin_y", "destination_x", "destination_y", "departure_time"]
_NUMERIC_TASK_COLUMNS = TASK_COLUMNS[1:]

ROUTER_CLASS = "org.eqasim.core.tools.routing.RunBatchPublicTransportRouter"

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
	<module name="transitRouter">
	</module>
</config>
"""


def write_router_config(path: str, *, network_path: str, schedule_path: str, vehicles_path: str) -> str:
    """Write a minimal MATSim config for the batch pt router and return ``path``.

    Only network, transit schedule and transit vehicles are named; ``transitRouter`` keeps
    its MATSim defaults. An existing file at ``path`` is overwritten (the config is derived).
    """
    for label, file in (("network", network_path), ("schedule", schedule_path), ("vehicles", vehicles_path)):
        if not file:
            raise ValueError(f"write_router_config: {label} path is empty")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    # quoteattr escapes &, <, > and quotes and adds the surrounding quotes (paths may contain any of them).
    text = _CONFIG_TEMPLATE.format(network=quoteattr(network_path), schedule=quoteattr(schedule_path),
                                   vehicles=quoteattr(vehicles_path),
                                   walk_factor=WALK_BEELINE_DISTANCE_FACTOR,
                                   walk_speed=WALK_TELEPORTED_SPEED_M_S)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(text)
    return path


def route_batch(context, tasks: pd.DataFrame, *, config_path: str, threads: int,
                work_dir: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Route ``tasks`` with the Java batch pt router; return ``(trips, legs)`` frames.

    ``tasks`` must carry ``TASK_COLUMNS`` (metric coordinates, ``departure_time`` in seconds).
    Non-finite coordinates raise ``ValueError`` BEFORE Java is started. Input and outputs are
    written to ``work_dir`` (``router_tasks.csv``, ``router_trips.csv``, ``router_legs.csv``).
    Identifier and stop-id columns are read as strings (the router writes legs rows for pt legs
    only). ``ValueError`` if the returned trips do not cover exactly the task identifiers.

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
    # A partial router output is a broken run, not data: every task needs exactly one trips row.
    expected = set(tasks["identifier"].astype(str))
    returned = set(trips["identifier"])
    missing, unexpected = expected - returned, returned - expected
    if missing or unexpected:
        raise ValueError(f"route_batch: router output does not cover the tasks: missing {len(missing)} "
                         f"(e.g. {sorted(missing)[:5]}), unexpected {len(unexpected)} "
                         f"(e.g. {sorted(unexpected)[:5]}); see {trips_path}")
    return trips, legs


def connection_mask(trips: pd.DataFrame, legs: pd.DataFrame, *, inside_stop_ids: set[str],
                    walk_max_m: float, plausible_walk_m: float = 600.0) -> pd.Series:
    """Boolean Series indexed by ``identifier``: does the routed result count as a connection?

    See the module docstring for the rule. A pt trip (``is_only_walk == 0``) needs at least one
    legs row (a pt trip has >= 1 pt leg by construction, so a missing leg is a failure signal),
    and every pt leg needs a non-blank access and egress stop id inside ``inside_stop_ids``.
    A missing or NaN ``is_only_walk`` (or any value other than 0/1), and missing or NaN
    distances, count as not a connection. Duplicate trip identifiers raise ``ValueError``.

    Only-walk distance: for an only-walk trip the Java tool (``BatchPublicTransportRouter``,
    ``isFirstLeg`` branch) books the single walk leg as ACCESS and leaves egress at 0, so
    access + egress is not a stable reading; ``total_walk_distance_km`` (access + egress +
    transfer) is the walk distance used for the plausible-short-walk test.

    The shares of connections, accepted short walks and each rejection reason are logged
    (a share near 0 or 1 signals a broken router input).
    """
    if trips["identifier"].duplicated().any():
        dups = trips.loc[trips["identifier"].duplicated(), "identifier"].head(5).tolist()
        raise ValueError(f"connection_mask: duplicate trip identifiers (first {dups}); "
                         f"each task needs exactly one trips row")
    trips = trips.set_index("identifier", drop=False)
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

    n = len(mask)
    if n:
        logger.info("[pt-router] connections %d/%d (%.1f%%): pt %d, plausible short walk %d; rejected: "
                    "is_only_walk_missing %d, only-walk too long %d, no_pt_legs %d, blank_stop_id %d, "
                    "outside stop %d, walk distance %d",
                    mask.sum(), n, 100.0 * mask.sum() / n, pt_ok.sum(), short_walk.sum(),
                    flag_missing.sum(), (only_walk & ~short_walk).sum(), no_pt_legs.sum(),
                    blank_stop.sum(), outside_stop.sum(), far_walk.sum())
    return mask
