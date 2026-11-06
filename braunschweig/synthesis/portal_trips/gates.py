"""Where a portal trip leaves and re-enters the supplied area (eqasim-bs#442).

Road gates come from ``braunschweig.synthesis.cordon_gates`` as the POINT where a network link
crosses the cordon boundary (= the cutter's extent). A portal stay must sit INSIDE that extent,
or the cutter would cut it again, so the stay is placed at the gate link's inside endpoint
(``gate_inside_points``). pt portals are the stops of the CUT timetable where a kept route leaves it, plus the
configured hub stations (``exit_stops``, #574), chosen timetable-aware by ``choose_pt_gates`` and checked by the
router on the cut timetable. The external point only fixes the direction and
the outside distance of a stay; it is drawn among the external Gemeinde points at the donor's
reported distance (ADR-0027's distance proxy; direction has no survey source). The gate is the
one with the smallest detour origin -> gate -> point (ASSUMPTION: no preference for motorways).
"""
from __future__ import annotations

import logging
import math

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

logger = logging.getLogger(__name__)

KIND_ROAD = "road"
KIND_PT = "pt"
GATE_COLUMNS = ["gate_id", "kind", "x", "y"]


def gate_inside_points(gates, links, cordon_polygon) -> gpd.GeoDataFrame:
    """One point per road gate: the endpoint of the gate's link that lies inside the extent.

    ``cordon_polygon`` may be a Polygon or MultiPolygon (holes allowed). Every gate whose link is
    missing from the network or has no endpoint inside the extent is collected and reported in ONE
    ValueError, so a broken gate table is diagnosed in a single run.
    """
    link_geometry = links.set_index("link_id")["geometry"]
    rows, points, offending, both_inside = [], [], [], 0
    for gate in gates.itertuples(index=False):
        line = link_geometry.get(gate.link_id)
        if line is None:
            offending.append(f"{gate.gate_id} (link {gate.link_id} is not in the cordon network)")
            continue
        candidates = [Point(line.coords[0]), Point(line.coords[-1])]
        inside = [point for point in candidates if cordon_polygon.contains(point)]
        if not inside:
            offending.append(f"{gate.gate_id} (no endpoint of link {gate.link_id} lies inside the extent)")
            continue
        if len(inside) == 2:
            # Both endpoints inside (a short link entirely inside): take the one deeper in the extent,
            # measured to the whole boundary (shell and holes, every part of a MultiPolygon).
            both_inside += 1
            chosen = max(inside, key=lambda point: cordon_polygon.boundary.distance(point))
        else:
            chosen = inside[0]
        rows.append((gate.gate_id, gate.link_id, gate.road_class, float(gate.capacity)))
        points.append(chosen)
    if offending:
        raise ValueError(f"[portal_trips] {len(offending)} of {len(gates)} road gates cannot be placed inside "
                         "the cordon extent (the stay would be cut again): " + "; ".join(offending))
    logger.info("[portal_trips.gates] gates with both link endpoints inside: %d/%d", both_inside, len(gates))
    frame = pd.DataFrame(rows, columns=["gate_id", "link_id", "road_class", "capacity"])
    return gpd.GeoDataFrame(frame, geometry=points, crs=gates.crs)


ALL_MODES = "all"
EXIT_COLUMNS = ["stop_id", "line_id", "route_id", "mode", "outside_x", "outside_y", "is_hub"]


def _normalise_modes(modes):
    """``None`` for all modes, else the set of lower-cased modes (a single string is one mode)."""
    if isinstance(modes, str):
        return None if modes.strip().lower() == ALL_MODES else {modes.strip().lower()}
    try:
        items = list(modes)
    except TypeError:
        items = None
    if items is None or not all(isinstance(item, str) for item in items):
        raise ValueError(f"[portal_trips] exit stops: modes must be '{ALL_MODES}', a mode string or an iterable "
                         f"of mode strings, got {modes!r}")
    return {item.strip().lower() for item in items}


def _align_cut_route(cut_ids: tuple, pre_cut_ids: tuple) -> int:
    """Start position of the cut stop sequence inside the pre-cut one, or -1 when it is not a contiguous part of it.

    The WHOLE cut sequence is matched (not its last stop), so a stop that the route visits twice is placed right.
    The match is unique for a real cut (design R2): the cut keeps the stops from the FIRST to the LAST anchor of the
    route, and an anchor is a property of the stop, so a second match would start before the first anchor or end
    after the last one. Two matches therefore mean the timetables do not belong together and return -2.
    """
    n = len(cut_ids)
    starts = [k for k in range(len(pre_cut_ids) - n + 1) if tuple(pre_cut_ids[k:k + n]) == tuple(cut_ids)]
    if not starts:
        return -1
    return starts[0] if len(starts) == 1 else -2


def exit_stops(schedule, cut_schedule, hub_stop_ids, modes="all", crs="EPSG:25832") -> gpd.GeoDataFrame:
    """Portal pt gate candidates of all (or the given) transit modes, read from the CUT timetable (#574, design R10).

    ``schedule`` is the pre-cut transit schedule and ``cut_schedule`` the timetable MATSim runs on (eqasim's cutter
    applied with the hubs, ``braunschweig.data.transit.cut_schedule``), both ``read_schedule`` results. Two kinds of
    rows (``EXIT_COLUMNS``):

    - an EXIT row (``is_hub`` False) per cut route whose last stop is not the last stop of the same route
      ``(line_id, route_id)`` in the pre-cut timetable: the route continues beyond the kept timetable, and
      ``outside_x/outside_y`` is the next pre-cut stop (it fixes the direction the route leaves in). The cut stop
      sequence is aligned inside the pre-cut one (``_align_cut_route``). A cut route that ends where its pre-cut
      route ends gives no exit row; a pre-cut route that the cut dropped gives none.
    - a HUB row (``is_hub`` True, ``outside_x/outside_y`` NaN) per (hub stop of ``hub_stop_ids``, cut route serving
      it). A hub that is also an exit stop has both kinds of rows. A configured hub that no cut route of the wanted
      modes serves gives no row; the count is logged.

    ``modes`` is ``ALL_MODES`` or a set of modes (compared lower-cased with the cut route's mode), for both kinds
    of rows. ``crs`` is the CRS of the schedule coordinates (EPSG:25832 in the Braunschweig pipeline); it is
    attached unchanged. A cut route missing from the pre-cut timetable, or whose stop sequence is not exactly one
    contiguous part of its pre-cut route, raises ``ValueError``: the two timetables do not belong together. A stop
    id missing from the stops of its timetable raises ``ValueError`` too.
    """
    wanted = _normalise_modes(modes)
    hub_stop_ids = {str(stop) for stop in hub_stop_ids}
    pre_cut_routes = {}
    for route in schedule.routes:
        key = (route.line_id, route.route_id)
        if key in pre_cut_routes:
            raise ValueError(f"[portal_trips] exit stops: route '{route.route_id}' (line '{route.line_id}') occurs more "
                             "than once in the pre-cut timetable; a cut route cannot be matched to its pre-cut route")
        pre_cut_routes[key] = route

    def coordinates(stops, stop_id, route, label):
        if stop_id not in stops:
            raise ValueError(f"[portal_trips] exit stops: stop '{stop_id}' of route '{route.route_id}' "
                             f"(line '{route.line_id}', mode '{route.mode}') is not in the {label} transit stops")
        return stops[stop_id]

    rows, scanned, n_exit_rows = [], 0, 0
    for route in cut_schedule.routes:
        if wanted is not None and str(route.mode).strip().lower() not in wanted:
            continue
        scanned += 1
        pre_cut = pre_cut_routes.get((route.line_id, route.route_id))
        if pre_cut is None:
            raise ValueError(f"[portal_trips] exit stops: route '{route.route_id}' (line '{route.line_id}') of the cut "
                             "timetable is not in the pre-cut timetable; the two timetables do not belong together")
        start = _align_cut_route(route.stop_ids, pre_cut.stop_ids)
        if start < 0:
            raise ValueError(f"[portal_trips] exit stops: the stops of route '{route.route_id}' (line "
                             f"'{route.line_id}') in the cut timetable are {'not' if start == -1 else 'more than once'}"
                             " a contiguous part of the same route in the pre-cut timetable; the two timetables do "
                             "not belong together")
        successor = start + len(route.stop_ids)
        if successor < len(pre_cut.stop_ids):
            coordinates(cut_schedule.stops, route.stop_ids[-1], route, "cut")
            outside_x, outside_y = coordinates(schedule.stops, pre_cut.stop_ids[successor], pre_cut, "pre-cut")
            rows.append((route.stop_ids[-1], route.line_id, route.route_id, route.mode, float(outside_x),
                         float(outside_y), False))
            n_exit_rows += 1
        for stop_id in dict.fromkeys(route.stop_ids):  # one row per (hub, route), also for a hub visited twice
            if stop_id in hub_stop_ids:
                coordinates(cut_schedule.stops, stop_id, route, "cut")
                rows.append((stop_id, route.line_id, route.route_id, route.mode, np.nan, np.nan, True))
    requested = "all" if wanted is None else sorted(wanted)
    served_hubs = {row[0] for row in rows if row[6]}
    logger.info("[portal_trips.gates] exit stops: %d cut routes scanned (modes: %s), %d exits found at %d stops; "
                "hubs served by a cut route %d/%d, %d hub rows", scanned, requested, n_exit_rows,
                len({row[0] for row in rows if not row[6]}), len(served_hubs), len(hub_stop_ids),
                len(rows) - n_exit_rows)
    if len(served_hubs) < len(hub_stop_ids):
        # Some unserved hubs are expected (a hub facility that no kept route of these modes stops at); a TOTAL miss
        # almost always means the hub ids do not match the timetable's stop ids, so it is a warning.
        log = logger.warning if not served_hubs else logger.info
        log("[portal_trips.gates] exit stops: %d/%d configured hubs are served by no cut route of the modes %s and "
            "are no gate (first: %s)", len(hub_stop_ids) - len(served_hubs), len(hub_stop_ids), requested,
            sorted(hub_stop_ids - served_hubs)[:5])
    if scanned == 0 or not rows:
        logger.warning("[portal_trips.gates] exit stops: %d cut routes scanned and %d exits found for modes %s; "
                       "the timetable offers no exit for these modes", scanned, len(rows), requested)
    frame = pd.DataFrame(rows, columns=EXIT_COLUMNS).astype({"outside_x": float, "outside_y": float, "is_hub": bool})
    return gpd.GeoDataFrame(frame, geometry=[Point(*cut_schedule.stops[s]) for s in frame["stop_id"]], crs=crs)


def exit_stop_points(exits: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """One row per distinct exit stop with its geometry (input of the detour computation)."""
    return exits[["stop_id", "geometry"]].drop_duplicates("stop_id").reset_index(drop=True)


def draw_external_points(reported_m, origin_xy, external_points, tolerance, rng):
    """Draw one external point per stay at the reported distance (band ``reported * (1 +- tolerance)``).

    Population-weighted inside the band; the point nearest to the reported distance when the
    band is empty (counted in ``band_miss``). Returns ``(point_xy, commune_id, band_miss)``.
    """
    reported_m = np.asarray(reported_m, dtype=float)
    origin_xy = np.asarray(origin_xy, dtype=float)
    invalid = ~np.isfinite(reported_m) | (reported_m <= 0.0)
    if invalid.any():
        raise ValueError(f"[portal_trips] {int(invalid.sum())} stays have a reported distance that is not finite "
                         f"and > 0 (first at stay index {int(np.flatnonzero(invalid)[0])}); the external point "
                         "cannot be drawn")
    if not (np.isfinite(tolerance) and 0.0 < tolerance < 1.0):
        raise ValueError(f"[portal_trips] tolerance must lie in the open interval (0, 1), got {tolerance}")
    if not np.isfinite(origin_xy).all():
        raise ValueError("[portal_trips] origin coordinates contain non-finite values; "
                         "the external point cannot be drawn")
    if len(external_points) == 0:
        raise ValueError("[portal_trips] no external points to draw from")
    ext_xy = np.column_stack([external_points.geometry.x.to_numpy(), external_points.geometry.y.to_numpy()])
    ewz = external_points["ewz"].astype(float).to_numpy()
    communes = external_points["commune_id"].astype(str).to_numpy()
    n = len(reported_m)
    point_xy = np.empty((n, 2))
    commune_id = np.empty(n, dtype=object)
    band_miss = np.zeros(n, dtype=bool)
    for i in range(n):
        distance = np.hypot(ext_xy[:, 0] - origin_xy[i, 0], ext_xy[:, 1] - origin_xy[i, 1])
        in_band = np.abs(distance - reported_m[i]) <= tolerance * reported_m[i]
        if in_band.any():
            weights = ewz[in_band]
            if not weights.sum() > 0.0:
                raise ValueError(f"[portal_trips] stay index {i}: the external points in the distance band have "
                                 "a population weight sum of 0; give the external points a positive population")
            index = np.flatnonzero(in_band)[rng.choice(int(in_band.sum()), p=weights / weights.sum())]
        else:
            index = int(np.argmin(np.abs(distance - reported_m[i])))
            band_miss[i] = True
        point_xy[i] = ext_xy[index]
        commune_id[i] = communes[index]
    return point_xy, commune_id, band_miss


# ---- timetable-aware pt gates (#465, WP-C) ----

PT_GATE_COLUMNS = ["gate_id", "x", "y", "reason", "n_in_direction", "n_served", "n_connected"]
REASON_GATE_FOUND = "gate_found"
REASON_NO_GATE_IN_DIRECTION = "no_gate_in_direction"
REASON_NO_GATE_SERVED = "no_gate_served"
REASON_NO_CONNECTION = "no_connection"
_REASONS = (REASON_GATE_FOUND, REASON_NO_GATE_IN_DIRECTION, REASON_NO_GATE_SERVED, REASON_NO_CONNECTION)
_OUT, _RET = "out", "ret"


def _stop_xy(exits: gpd.GeoDataFrame) -> dict:
    """``stop_id -> (x, y)`` of the exit stops."""
    points = exit_stop_points(exits)
    return {str(s): (float(p.x), float(p.y)) for s, p in zip(points["stop_id"], points.geometry)}


def _toward_mask(point, exits: gpd.GeoDataFrame) -> np.ndarray:
    """Per exit row: does the outward route head toward ``point`` (outside stop closer than the exit stop)?

    A hub row is always True (design R10): a hub has no outward direction, it is a station to travel to.
    """
    heading = (np.hypot(exits["outside_x"].to_numpy(dtype=float) - point[0],
                        exits["outside_y"].to_numpy(dtype=float) - point[1])
               < np.hypot(exits.geometry.x.to_numpy() - point[0], exits.geometry.y.to_numpy() - point[1]))
    return heading | exits["is_hub"].to_numpy(dtype=bool)


def pt_gate_candidates(origin_xy, point_xy, exits: gpd.GeoDataFrame, max_extra_detour_m: float,
                       toward=None) -> list[list[tuple[str, float]]]:
    """Per stay the directional exit stops within the extra-detour cap, as ``(stop_id, detour_m)`` by detour.

    A route heads toward the point when its outside stop is closer to the point than the exit stop
    itself (``hypot(outside - point) < hypot(stop - point)``); a hub row always counts as directional
    (``_toward_mask``, #574), so a hub is a candidate for every stay. A stop is listed once however many routes
    qualify. Its detour is the straight line origin -> stop -> point. Only stops with a detour of at most the
    smallest directional detour of the stay plus ``max_extra_detour_m`` are kept (``math.inf`` keeps all),
    ordered by detour (ties keep the order of ``exits``). No ``n_candidates`` cut happens here: since #572
    ``choose_pt_gates`` cuts to the ``n_candidates`` smallest detours AFTER the service check (ADR-0152
    decision 2, amended for #572). ``toward`` optionally gives the per-stay ``_toward_mask`` (computed once by
    the caller when it needs the masks again).
    """
    if not max_extra_detour_m >= 0.0:  # also rejects NaN
        raise ValueError(f"[portal_trips] pt gates: max_extra_detour_m must be >= 0, got {max_extra_detour_m!r}")
    origin_xy = np.asarray(origin_xy, dtype=float)
    point_xy = np.asarray(point_xy, dtype=float)
    stop_ids = exits["stop_id"].astype(str).to_numpy()
    stop_x, stop_y = exits.geometry.x.to_numpy(), exits.geometry.y.to_numpy()
    result = []
    for i, (origin, point) in enumerate(zip(origin_xy, point_xy)):
        toward_i = _toward_mask(point, exits) if toward is None else toward[i]
        seen, rows = set(), []
        for k in np.flatnonzero(toward_i):
            if stop_ids[k] in seen:
                continue
            seen.add(stop_ids[k])
            detour = np.hypot(origin[0] - stop_x[k], origin[1] - stop_y[k]) + \
                np.hypot(point[0] - stop_x[k], point[1] - stop_y[k])
            rows.append((stop_ids[k], float(detour)))
        rows.sort(key=lambda row: row[1])  # stable: ties keep the order of ``exits``
        result.append([row for row in rows if row[1] <= rows[0][1] + max_extra_detour_m])
    return result


def _departure_table(schedule, exits: gpd.GeoDataFrame) -> dict:
    """``(stop_id, (line_id, route_id)) -> sorted departure times (s)`` for the exit routes of ``exits``.

    One pass over the (pre-cut) schedule routes (built once per call, not per stay). A stop visited twice by one
    route contributes both times, as in ``schedule.departures_at_stop``. Hub rows are skipped: a hub is served
    without a departure check (design R10).
    """
    exit_rows = ~exits["is_hub"].to_numpy(dtype=bool)
    pairs = {(str(s), (line, route)) for s, line, route in zip(exits["stop_id"].to_numpy()[exit_rows],
                                                                 exits["line_id"].to_numpy()[exit_rows],
                                                                 exits["route_id"].to_numpy()[exit_rows])}
    keys = {key for _, key in pairs}
    times: dict = {}
    for route in schedule.routes:
        key = (route.line_id, route.route_id)
        if key not in keys:
            continue
        for stop, offset in zip(route.stop_ids, route.departure_offsets_s):
            if (str(stop), key) in pairs:
                times.setdefault((str(stop), key), []).extend(d + offset for d in route.departures_s)
    return {pair: np.sort(np.asarray(values, dtype=float)) for pair, values in times.items()}


def _has_departure(table: dict, stop: str, keys, t_s: float, window_s: float) -> bool:
    """Does one of the routes ``keys`` depart at ``stop`` within ``[t_s, t_s + window_s]``?"""
    for key in keys:
        departures = table.get((stop, key))
        if departures is None:
            continue
        index = int(np.searchsorted(departures, t_s, side="left"))
        if index < len(departures) and departures[index] <= t_s + window_s:
            return True
    return False


def _connected(router, tasks, *, inside_stop_ids, walk_max_m, latest_arrival_s=None) -> set:
    """Identifiers of ``tasks`` that the router connects (one router call; empty tasks make no call).

    A task without a trips row (the router found no route) is not connected and is counted as ``no_route``.
    ``inside_stop_ids`` are the stops of the cut timetable the router runs on (#574, design R10): a routed leg that
    boards or alights at another stop means the router did NOT run on the cut timetable, a wiring defect that raises
    ``RuntimeError`` before any connection is judged (it is never a rejection). Blank stop ids are left to
    ``connection_mask``, which rejects them.
    """
    from braunschweig.data.transit.connections import (LEGS_REQUIRED_COLUMNS, TASK_COLUMNS, _require_columns,
                                                       connection_mask)

    if not tasks:
        return set()
    frame = pd.DataFrame(tasks, columns=TASK_COLUMNS)
    trips, legs = router(frame)
    # the same explicit error as connection_mask for router output without the stop columns, before the guard reads them
    _require_columns(legs, LEGS_REQUIRED_COLUMNS, "legs", "connection_mask")
    access, egress = (legs[column].fillna("").astype(str) for column in ("access_stop_id", "egress_stop_id"))

    def is_foreign(stops):
        return (stops.str.strip() != "") & ~stops.isin(inside_stop_ids)

    foreign_legs = is_foreign(access) | is_foreign(egress)
    if foreign_legs.any():
        foreign = sorted(set(access[is_foreign(access)]) | set(egress[is_foreign(egress)]))
        raise RuntimeError(f"[portal_trips] pt gates: {int(foreign_legs.sum())} of {len(legs)} routed legs board or "
                           f"alight at {len(foreign)} stops that are not in the cut timetable (first {foreign[:5]}); "
                           "the router did not route on the cut timetable (check the schedule path of its config)")
    mask = connection_mask(trips, legs, inside_stop_ids=inside_stop_ids, walk_max_m=walk_max_m,
                           task_ids=frame["identifier"], latest_arrival_s=latest_arrival_s,
                           label="portal pt gates")
    return set(mask.index[mask.to_numpy()].astype(str))


def choose_pt_gates(origin_xy, point_xy, outbound_departure_s, outbound_arrival_s, return_departure_s,
                    return_arrival_s, has_return, exits: gpd.GeoDataFrame, schedule, router, *,
                    n_candidates: int, max_extra_detour_m: float, service_window_s: float, inside_stop_ids: set,
                    walk_max_m: float, latest_arrival_s: float | None = None) -> pd.DataFrame:
    """The pt gate of each pt stay: direction -> extra-detour cap -> served -> n smallest detours -> router.

    All arguments are per pt stay (row ``i`` of the arrays is stay ``i``; coordinates metric, times in
    seconds). Per stay (ADR-0152 decision 2, amended for #572): the directional exit stops within
    ``max_extra_detour_m`` of the smallest directional detour are taken (``pt_gate_candidates``); of those the
    SERVED ones are kept, i.e. a route heading toward the stay's external point departs at the stop in
    ``[t, t + service_window_s]`` at the planned gate arrival (outbound) and, when the stay has a return leg, at
    the planned re-entry (the outward departures near the re-entry time are a PROXY for the return service: the
    inbound service is not read from the timetable here, the router checks it); a hub stop (a hub row in ``exits``)
    is always served (design R10); the ``n_candidates`` served
    stops with the smallest detour are routed exactly: the router must connect each from the in-region
    end to the gate at the planned trip departure (outbound) and from the gate to the in-region end at
    the re-entry (return; ``connection_mask`` rule with ``inside_stop_ids`` and ``walk_max_m``).
    The gate arrival and the re-entry depend on the candidate and use the distance split of
    ``timing._split_share``. The routed candidate with the smallest detour that connects wins.

    The router gets ONE batch per direction (identifiers ``"<stay>:<stop_id>:out"`` / ``":ret"``). The
    return batch holds only the candidates already connected outbound, and is not sent for
    stays without a return leg. Result: ``PT_GATE_COLUMNS``, one row per stay, ``gate_id`` None with a
    ``reason`` when no candidate is admissible (``no_gate_in_direction``: no directional stop;
    ``no_gate_served``: none within the cap is served; ``no_connection``: the router connects none).
    ``n_in_direction`` is the number of directional stops within the cap; ``n_served`` those among them served
    in EVERY checked direction (outbound, plus return when ``has_return``), BEFORE the ``n_candidates`` cut;
    ``n_connected`` the routed ones that also connect in every checked direction. Non-finite coordinates or
    times raise ``ValueError`` (return times are only checked for stays with a return leg), as does a
    ``max_extra_detour_m`` that is negative or not finite.

    Logged as rates (info): the reasons (the stage surfaces a high ``no_connection`` share) and the two
    #572 effects, the stays whose gate lies beyond the ``n_candidates`` smallest-detour directional stops
    (rank among ALL directional stops, cap ignored) and the stays with no served stop within the cap although
    one is served beyond it (the service of the beyond-cap stops is only evaluated for stays without a served
    stop within the cap). Neither has a warn threshold: no share bound is defined for them. Both counts are
    also returned in ``frame.attrs`` (``n_gate_beyond_top_n``, ``n_no_served_within_cap``) so that the stage
    can put them in its per-run report.
    """
    from braunschweig.synthesis.portal_trips.timing import _split_share

    if not (math.isfinite(max_extra_detour_m) and max_extra_detour_m >= 0.0):
        raise ValueError(f"[portal_trips] pt gates: max_extra_detour_m must be finite and >= 0, "
                         f"got {max_extra_detour_m!r}")
    if n_candidates < 1:
        raise ValueError(f"[portal_trips] pt gates: n_candidates must be >= 1, got {n_candidates}")
    origin_xy = np.asarray(origin_xy, dtype=float)
    point_xy = np.asarray(point_xy, dtype=float)
    out_dep, out_arr = (np.asarray(v, dtype=float) for v in (outbound_departure_s, outbound_arrival_s))
    ret_dep, ret_arr = (np.asarray(v, dtype=float) for v in (return_departure_s, return_arrival_s))
    has_return = np.asarray(has_return, dtype=bool)
    n = len(origin_xy)
    for label, values in (("origin", origin_xy), ("external point", point_xy)):
        if not np.isfinite(values).all():
            raise ValueError(f"[portal_trips] pt gates: {label} coordinates contain non-finite values")
    if not (np.isfinite(out_dep).all() and np.isfinite(out_arr).all()):
        raise ValueError("[portal_trips] pt gates: outbound departure/arrival times contain non-finite values")
    if not (np.isfinite(ret_dep[has_return]).all() and np.isfinite(ret_arr[has_return]).all()):
        raise ValueError("[portal_trips] pt gates: return departure/arrival times of stays with a return leg "
                         "contain non-finite values")
    # The "route heads toward the point" mask is computed once per stay and used for the candidates and the service check.
    toward = [_toward_mask(point, exits) for point in point_xy] if len(exits) else []
    # ALL directional stops by detour (no cap): ``within`` is the capped prefix, ``beyond`` the rest.
    directional = (pt_gate_candidates(origin_xy, point_xy, exits, math.inf, toward=toward)
                   if len(exits) else [[] for _ in range(n)])
    within, beyond = [], []
    for stops in directional:
        limit = stops[0][1] + max_extra_detour_m if stops else math.inf
        cut = sum(1 for _, detour in stops if detour <= limit)  # sorted by detour: the cap keeps a prefix
        within.append([stop for stop, _ in stops[:cut]])
        beyond.append([stop for stop, _ in stops[cut:]])
    xy = _stop_xy(exits)
    table = _departure_table(schedule, exits)
    stop_rows = {}
    for r, stop in enumerate(exits["stop_id"].astype(str)):
        stop_rows.setdefault(stop, []).append(r)
    route_keys = list(zip(exits["line_id"], exits["route_id"]))
    # A stop with a hub row is always served (design R10, ASSUMPTION: a hub station has onward service; the router
    # decides whether it is reachable at the time); its exit rows are not consulted.
    hub_stops = set(exits["stop_id"].astype(str).to_numpy()[exits["is_hub"].to_numpy(dtype=bool)])

    def service(candidates):
        """Service of all (stay, stop) pairs of ``candidates`` at once: one vectorised _split_share call.

        Returns ``(out_time, ret_time, served)`` dicts keyed ``(stay, stop)``.
        """
        pair_keys = [(i, stop) for i in range(n) for stop in candidates[i]]
        out_time, ret_time, served = {}, {}, {}
        if not pair_keys:
            return out_time, ret_time, served
        stay = np.array([i for i, _ in pair_keys])
        gate = np.array([xy[stop] for _, stop in pair_keys])
        d_origin = np.hypot(origin_xy[stay, 0] - gate[:, 0], origin_xy[stay, 1] - gate[:, 1])
        d_point = np.hypot(point_xy[stay, 0] - gate[:, 0], point_xy[stay, 1] - gate[:, 1])
        share_out, _ = _split_share(d_origin, d_point)
        share_ret, _ = _split_share(d_point, d_origin)
        t_out = out_dep[stay] + share_out * (out_arr[stay] - out_dep[stay])
        t_ret = ret_dep[stay] + share_ret * (ret_arr[stay] - ret_dep[stay])
        for k, (i, stop) in enumerate(pair_keys):
            out_time[i, stop], ret_time[i, stop] = float(t_out[k]), float(t_ret[k])
            if stop in hub_stops:
                served[i, stop] = True
                continue
            # the routes at this stop that head toward the stay's external point
            keys = {route_keys[r] for r in stop_rows[stop] if toward[i][r]}
            ok = _has_departure(table, stop, keys, out_time[i, stop], service_window_s)
            if ok and has_return[i]:
                ok = _has_departure(table, stop, keys, ret_time[i, stop], service_window_s)
            served[i, stop] = ok
        return out_time, ret_time, served

    out_time, ret_time, served = service(within)
    served_within = [[s for s in within[i] if served[i, s]] for i in range(n)]
    routed = [served_within[i][:n_candidates] for i in range(n)]  # ordered by detour

    # #572 diagnostic: stays without a served stop within the cap, checked against the stops beyond it (cheap:
    # only these stays, and only their beyond-cap stops).
    probe = [beyond[i] if directional[i] and not served_within[i] else [] for i in range(n)]
    _, _, served_beyond = service(probe)
    capped = sum(1 for i in range(n) if any(served_beyond[i, s] for s in probe[i]))

    def task(i, stop, direction):
        gx, gy = xy[stop]
        ox, oy = origin_xy[i]
        if direction == _OUT:
            return (f"{i}:{stop}:{_OUT}", ox, oy, gx, gy, float(out_dep[i]))
        return (f"{i}:{stop}:{_RET}", gx, gy, ox, oy, ret_time[i, stop])

    kwargs = dict(inside_stop_ids=inside_stop_ids, walk_max_m=walk_max_m, latest_arrival_s=latest_arrival_s)
    routed_keys = [(i, s) for i in range(n) for s in routed[i]]
    out_ok = _connected(router, [task(i, s, _OUT) for i, s in routed_keys], **kwargs)
    out_pass = [(i, s) for i, s in routed_keys if f"{i}:{s}:{_OUT}" in out_ok]
    ret_pairs = [(i, s) for i, s in out_pass if has_return[i]]
    ret_ok = _connected(router, [task(i, s, _RET) for i, s in ret_pairs], **kwargs)
    connected = {(i, s) for i, s in out_pass if not has_return[i] or f"{i}:{s}:{_RET}" in ret_ok}

    rows, beyond_top_n = [], 0
    for i in range(n):
        won = [s for s in routed[i] if (i, s) in connected]  # routed stops are ordered by detour
        if not directional[i]:
            reason = REASON_NO_GATE_IN_DIRECTION
        elif not served_within[i]:
            reason = REASON_NO_GATE_SERVED
        elif not won:
            reason = REASON_NO_CONNECTION
        else:
            reason = REASON_GATE_FOUND
        gate = won[0] if won else None
        if gate is not None and [s for s, _ in directional[i]].index(gate) + 1 > n_candidates:
            beyond_top_n += 1
        rows.append((gate, xy[gate][0] if gate else np.nan, xy[gate][1] if gate else np.nan, reason,
                     len(within[i]), len(served_within[i]), len(won)))
    frame = pd.DataFrame(rows, columns=PT_GATE_COLUMNS)
    frame["gate_id"] = frame["gate_id"].astype(object).where(frame["gate_id"].notna(), None)
    counts = frame["reason"].value_counts()
    logger.info("[portal_trips.gates] pt gates over %d pt stays: %s", n,
                ", ".join(f"{r} {int(counts.get(r, 0))}/{n} ({100.0 * counts.get(r, 0) / max(n, 1):.1f}%)"
                          for r in _REASONS))
    share = 100.0 / max(n, 1)
    logger.info("[portal_trips.gates] pt gates: gate beyond the %d smallest-detour directional stops %d/%d (%.1f%%), "
                "no served stop within the extra-detour cap of %.0f m although one is served beyond it %d/%d (%.1f%%)",
                n_candidates, beyond_top_n, n, beyond_top_n * share, max_extra_detour_m, capped, n, capped * share)
    frame.attrs["n_gate_beyond_top_n"] = int(beyond_top_n)
    frame.attrs["n_no_served_within_cap"] = int(capped)
    return frame


def _nearest_by_detour(origin_xy, point_xy, candidate_xy):
    """Index of the candidate minimising |origin - candidate| + |candidate - point| per row."""
    inside = np.hypot(origin_xy[:, None, 0] - candidate_xy[None, :, 0],
                      origin_xy[:, None, 1] - candidate_xy[None, :, 1])
    outside = np.hypot(point_xy[:, None, 0] - candidate_xy[None, :, 0],
                       point_xy[:, None, 1] - candidate_xy[None, :, 1])
    return np.argmin(inside + outside, axis=1)


def choose_gates(origin_xy, point_xy, modes, road_gates, pt_gates: pd.DataFrame) -> pd.DataFrame:
    """The gate of each stay: from ``pt_gates`` for pt stays, the road gate with the smallest detour otherwise.

    ``pt_gates`` (``choose_pt_gates`` result) has one row per pt stay, in the order of the pt stays in
    ``modes``. A pt stay whose ``gate_id`` is None raises: the fallback chain must resolve it before.
    """
    origin_xy = np.asarray(origin_xy, dtype=float)
    point_xy = np.asarray(point_xy, dtype=float)
    modes = np.asarray(modes, dtype=object)
    if not np.isfinite(origin_xy).all():
        raise ValueError("[portal_trips] origin coordinates contain non-finite values; no gate can be chosen")
    if not np.isfinite(point_xy).all():
        raise ValueError("[portal_trips] external point coordinates contain non-finite values; "
                         "no gate can be chosen")
    is_pt = modes == "pt"
    out = pd.DataFrame(index=range(len(modes)), columns=GATE_COLUMNS)
    if is_pt.any():
        if pt_gates is None or len(pt_gates) != int(is_pt.sum()):
            raise ValueError(f"[portal_trips] {int(is_pt.sum())} pt stays but {0 if pt_gates is None else len(pt_gates)}"
                             " pt gate rows; pass one row per pt stay")
        unresolved = pt_gates["gate_id"].isna().to_numpy()
        if unresolved.any():
            reasons = pt_gates.loc[unresolved, "reason"].value_counts().to_dict()
            raise ValueError(f"[portal_trips] {int(unresolved.sum())} pt stays have no admissible gate "
                             f"(reasons {reasons}); resolve them (mode switch, shift) before choosing gates")
        out.loc[is_pt, "gate_id"] = pt_gates["gate_id"].astype(str).to_numpy()
        out.loc[is_pt, "kind"] = KIND_PT
        out.loc[is_pt, "x"] = pt_gates["x"].to_numpy(dtype=float)
        out.loc[is_pt, "y"] = pt_gates["y"].to_numpy(dtype=float)
    if (~is_pt).any():
        if len(road_gates) == 0:
            raise ValueError(f"[portal_trips] {int((~is_pt).sum())} road stays but no road gate")
        road_xy = np.column_stack([road_gates.geometry.x.to_numpy(), road_gates.geometry.y.to_numpy()])
        picked = _nearest_by_detour(origin_xy[~is_pt], point_xy[~is_pt], road_xy)
        out.loc[~is_pt, "gate_id"] = road_gates["gate_id"].astype(str).to_numpy()[picked]
        out.loc[~is_pt, "kind"] = KIND_ROAD
        out.loc[~is_pt, "x"] = road_xy[picked, 0]
        out.loc[~is_pt, "y"] = road_xy[picked, 1]
    out["x"] = out["x"].astype(float)
    out["y"] = out["y"].astype(float)
    return out
