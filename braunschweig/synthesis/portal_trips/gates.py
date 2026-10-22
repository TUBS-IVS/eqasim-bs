"""Where a portal trip leaves and re-enters the supplied area (eqasim-bs#442).

Road gates come from ``braunschweig.synthesis.cordon_gates`` as the POINT where a network link
crosses the cordon boundary (= the cutter's extent). A portal stay must sit INSIDE that extent,
or the cutter would cut it again, so the stay is placed at the gate link's inside endpoint
(``gate_inside_points``). pt portals are the exit stops inside the extent (``exit_stops``) that a route leaves outward,
chosen timetable-aware by ``choose_pt_gates``. The external point only fixes the direction and
the outside distance of a stay; it is drawn among the external Gemeinde points at the donor's
reported distance (ADR-0027's distance proxy; direction has no survey source). The gate is the
one with the smallest detour origin -> gate -> point (ASSUMPTION: no preference for motorways).
"""
from __future__ import annotations

import logging

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
EXIT_COLUMNS = ["stop_id", "line_id", "route_id", "mode", "outside_x", "outside_y"]


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


def exit_stops(schedule, cordon_polygon, modes="all", crs="EPSG:25832") -> gpd.GeoDataFrame:
    """Exit stops of all (or the given) transit modes: one row per (inside stop, route).

    A route exits the extent at an inside stop whose NEXT stop in route order lies outside it;
    ``outside_x/outside_y`` is that next stop (it fixes the direction the route leaves in). An inside
    stop that is only preceded by an outside stop is an ENTRY and is not listed. ``modes`` is
    ``ALL_MODES`` or a set of modes (compared lower-cased). ``crs`` is the CRS of the schedule
    coordinates (EPSG:25832 in the Braunschweig pipeline); it is attached unchanged.
    """
    wanted = _normalise_modes(modes)
    inside_cache = {}

    def is_inside(stop_id, route):
        if stop_id not in inside_cache:
            if stop_id not in schedule.stops:
                raise ValueError(f"[portal_trips] exit stops: stop '{stop_id}' of route '{route.route_id}' "
                                 f"(line '{route.line_id}', mode '{route.mode}') is not in the transit stops")
            inside_cache[stop_id] = cordon_polygon.contains(Point(*schedule.stops[stop_id]))
        return inside_cache[stop_id]

    rows, scanned = [], 0
    for route in schedule.routes:
        if wanted is not None and str(route.mode).strip().lower() not in wanted:
            continue
        scanned += 1
        flags = [is_inside(stop_id, route) for stop_id in route.stop_ids]
        for position in range(len(flags) - 1):
            if flags[position] and not flags[position + 1]:
                outside_x, outside_y = schedule.stops[route.stop_ids[position + 1]]
                rows.append((route.stop_ids[position], route.line_id, route.route_id, route.mode,
                             float(outside_x), float(outside_y)))
    requested = "all" if wanted is None else sorted(wanted)
    logger.info("[portal_trips.gates] exit stops: %d routes scanned (modes: %s), %d exits found at %d stops",
                scanned, requested, len(rows), len({row[0] for row in rows}))
    if scanned == 0 or not rows:
        logger.warning("[portal_trips.gates] exit stops: %d routes scanned and %d exits found for modes %s; "
                       "the timetable offers no exit for these modes", scanned, len(rows), requested)
    frame = pd.DataFrame(rows, columns=EXIT_COLUMNS)
    return gpd.GeoDataFrame(frame, geometry=[Point(*schedule.stops[s]) for s in frame["stop_id"]], crs=crs)


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
    """Per exit row: does the outward route head toward ``point`` (outside stop closer than the exit stop)?"""
    return (np.hypot(exits["outside_x"].to_numpy(dtype=float) - point[0],
                     exits["outside_y"].to_numpy(dtype=float) - point[1])
            < np.hypot(exits.geometry.x.to_numpy() - point[0], exits.geometry.y.to_numpy() - point[1]))


def pt_gate_candidates(origin_xy, point_xy, exits: gpd.GeoDataFrame, n_candidates: int,
                       toward=None) -> list[list[str]]:
    """Per stay the exit ``stop_id``s whose outward route heads toward the external point.

    A route heads toward the point when its outside stop is closer to the point than the exit stop
    itself (``hypot(outside - point) < hypot(stop - point)``). A stop is listed once however many routes
    qualify; the ``n_candidates`` stops with the smallest detour origin -> stop -> point are kept,
    ordered by detour (ties keep the order of ``exits``). ``toward`` optionally gives the per-stay
    ``_toward_mask`` (computed once by the caller when it needs the masks again).
    """
    if n_candidates < 1:
        raise ValueError(f"[portal_trips] pt gates: n_candidates must be >= 1, got {n_candidates}")
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
            rows.append((detour, stop_ids[k]))
        rows.sort(key=lambda row: row[0])  # stable: ties keep the order of ``exits``
        result.append([stop for _, stop in rows[:n_candidates]])
    return result


def _departure_table(schedule, exits: gpd.GeoDataFrame) -> dict:
    """``(stop_id, (line_id, route_id)) -> sorted departure times (s)`` for the exit routes of ``exits``.

    One pass over the schedule routes (built once per call, not per stay). A stop visited twice by one
    route contributes both times, as in ``schedule.departures_at_stop``.
    """
    pairs = {(str(s), (line, route)) for s, line, route in zip(exits["stop_id"], exits["line_id"], exits["route_id"])}
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


def _connected(router, tasks, *, inside_stop_ids, walk_max_m) -> set:
    """Identifiers of ``tasks`` that the router connects (one router call; empty tasks make no call).

    A task without a trips row (the router found no route) is not connected and is counted as ``no_route``.
    """
    from braunschweig.data.transit.connections import TASK_COLUMNS, connection_mask

    if not tasks:
        return set()
    frame = pd.DataFrame(tasks, columns=TASK_COLUMNS)
    trips, legs = router(frame)
    mask = connection_mask(trips, legs, inside_stop_ids=inside_stop_ids, walk_max_m=walk_max_m,
                           task_ids=frame["identifier"])
    return set(mask.index[mask.to_numpy()].astype(str))


def choose_pt_gates(origin_xy, point_xy, outbound_departure_s, outbound_arrival_s, return_departure_s,
                    return_arrival_s, has_return, exits: gpd.GeoDataFrame, schedule, router, *,
                    n_candidates: int, service_window_s: float, inside_stop_ids: set, walk_max_m: float
                    ) -> pd.DataFrame:
    """The pt gate of each pt stay: direction -> served window -> router connection -> smallest detour.

    All arguments are per pt stay (row ``i`` of the arrays is stay ``i``; coordinates metric, times in
    seconds). Per stay: the candidates are ``pt_gate_candidates``; a candidate must be served, i.e. a route
    heading toward the stay's external point departs at the stop in ``[t, t + service_window_s]`` at the planned
    gate arrival (outbound) and, when the stay has a return leg, at the planned re-entry (the outward
    departures near the re-entry time are a PROXY for the return service: the inbound service is not read
    from the timetable here, the router checks it); the router must connect it from the in-region
    end to the gate at the planned trip departure (outbound) and from the gate to the in-region end at
    the re-entry (return; ``connection_mask`` rule with ``inside_stop_ids`` and ``walk_max_m``).
    The gate arrival and the re-entry depend on the candidate and use the distance split of
    ``timing._split_share``. The admissible candidate with the smallest detour wins.

    The router gets ONE batch per direction (identifiers ``"<stay>:<stop_id>:out"`` / ``":ret"``). The
    return batch holds only the candidates already served and connected outbound, and is not sent for
    stays without a return leg. Result: ``PT_GATE_COLUMNS``, one row per stay, ``gate_id`` None with a
    ``reason`` when no candidate is admissible. ``n_in_direction`` is the number of candidates kept (in
    direction, AFTER the ``n_candidates`` cut); ``n_served`` the candidates served in EVERY checked direction
    (outbound, plus return when ``has_return``) by a route heading toward the stay's point; ``n_connected``
    those that also connect in every checked direction. Non-finite coordinates or times raise ``ValueError``
    (return times are only checked for stays with a return leg). The reasons are logged as rates (the stage
    surfaces a high ``no_connection`` share).
    """
    from braunschweig.synthesis.portal_trips.timing import _split_share

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
    candidates = (pt_gate_candidates(origin_xy, point_xy, exits, n_candidates, toward=toward)
                  if len(exits) else [[] for _ in range(n)])
    xy = _stop_xy(exits)
    table = _departure_table(schedule, exits)

    # All (stay, stop) pairs at once: the gate arrival / re-entry share is one vectorised _split_share call.
    pair_keys = [(i, stop) for i in range(n) for stop in candidates[i]]
    out_time, ret_time, served = {}, {}, {}
    if pair_keys:
        stay = np.array([i for i, _ in pair_keys])
        gate = np.array([xy[stop] for _, stop in pair_keys])
        d_origin = np.hypot(origin_xy[stay, 0] - gate[:, 0], origin_xy[stay, 1] - gate[:, 1])
        d_point = np.hypot(point_xy[stay, 0] - gate[:, 0], point_xy[stay, 1] - gate[:, 1])
        share_out, _ = _split_share(d_origin, d_point)
        share_ret, _ = _split_share(d_point, d_origin)
        t_out = out_dep[stay] + share_out * (out_arr[stay] - out_dep[stay])
        t_ret = ret_dep[stay] + share_ret * (ret_arr[stay] - ret_dep[stay])
        stop_rows = {}
        for r, stop in enumerate(exits["stop_id"].astype(str)):
            stop_rows.setdefault(stop, []).append(r)
        route_keys = list(zip(exits["line_id"], exits["route_id"]))
        for k, (i, stop) in enumerate(pair_keys):
            # the routes at this stop that head toward the stay's external point
            keys = {route_keys[r] for r in stop_rows[stop] if toward[i][r]}
            out_time[i, stop], ret_time[i, stop] = float(t_out[k]), float(t_ret[k])
            ok = _has_departure(table, stop, keys, out_time[i, stop], service_window_s)
            if ok and has_return[i]:
                ok = _has_departure(table, stop, keys, ret_time[i, stop], service_window_s)
            served[i, stop] = ok

    def task(i, stop, direction):
        gx, gy = xy[stop]
        ox, oy = origin_xy[i]
        if direction == _OUT:
            return (f"{i}:{stop}:{_OUT}", ox, oy, gx, gy, float(out_dep[i]))
        return (f"{i}:{stop}:{_RET}", gx, gy, ox, oy, ret_time[i, stop])

    kwargs = dict(inside_stop_ids=inside_stop_ids, walk_max_m=walk_max_m)
    served_keys = [(i, s) for i in range(n) for s in candidates[i] if served[i, s]]
    out_ok = _connected(router, [task(i, s, _OUT) for i, s in served_keys], **kwargs)
    out_pass = [(i, s) for i, s in served_keys if f"{i}:{s}:{_OUT}" in out_ok]
    ret_pairs = [(i, s) for i, s in out_pass if has_return[i]]
    ret_ok = _connected(router, [task(i, s, _RET) for i, s in ret_pairs], **kwargs)
    connected = {(i, s) for i, s in out_pass if not has_return[i] or f"{i}:{s}:{_RET}" in ret_ok}

    rows = []
    for i in range(n):
        served_n = sum(served[i, s] for s in candidates[i])
        won = [s for s in candidates[i] if (i, s) in connected]  # candidates are ordered by detour
        if not candidates[i]:
            reason = REASON_NO_GATE_IN_DIRECTION
        elif served_n == 0:
            reason = REASON_NO_GATE_SERVED
        elif not won:
            reason = REASON_NO_CONNECTION
        else:
            reason = REASON_GATE_FOUND
        gate = won[0] if won else None
        rows.append((gate, xy[gate][0] if gate else np.nan, xy[gate][1] if gate else np.nan, reason,
                     len(candidates[i]), int(served_n), len(won)))
    frame = pd.DataFrame(rows, columns=PT_GATE_COLUMNS)
    frame["gate_id"] = frame["gate_id"].astype(object).where(frame["gate_id"].notna(), None)
    counts = frame["reason"].value_counts()
    logger.info("[portal_trips.gates] pt gates over %d pt stays: %s", n,
                ", ".join(f"{r} {int(counts.get(r, 0))}/{n} ({100.0 * counts.get(r, 0) / max(n, 1):.1f}%)"
                          for r in _REASONS))
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
