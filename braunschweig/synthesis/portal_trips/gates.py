"""Where a portal trip leaves and re-enters the supplied area (eqasim-bs#442).

Road gates come from ``braunschweig.synthesis.cordon_gates`` as the POINT where a network link
crosses the cordon boundary (= the cutter's extent). A portal stay must sit INSIDE that extent,
or the cutter would cut it again, so the stay is placed at the gate link's inside endpoint
(``gate_inside_points``). Rail portals are the stations inside the extent from which a rail
route continues to a stop outside it (``rail_exit_stations``) -- the in-area counterpart of the
rail ENTRY stations the in-commuters board at. The external point only fixes the direction and
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

#: Same set as braunschweig.synthesis.incommuters.RAIL_LIKE_MODES (kept here to avoid importing
#: the in-commuter stage module into this helper).
RAIL_LIKE_MODES = frozenset({"rail", "train", "subway", "light_rail", "regional_rail"})
KIND_ROAD = "road"
KIND_RAIL = "rail"
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


def rail_exit_stations(stops: dict, routes: list, cordon_polygon, rail_like_modes=RAIL_LIKE_MODES,
                       crs="EPSG:25832") -> gpd.GeoDataFrame:
    """Inside stops of rail routes that are adjacent, in route order, to a stop outside the extent.

    ``crs`` is the CRS of the schedule coordinates in ``stops`` (the Braunschweig pipeline writes
    its transit schedule in EPSG:25832); it is attached to the result unchanged.
    """
    inside_cache = {}

    def is_inside(stop_id, route_index, mode):
        if stop_id not in inside_cache:
            if stop_id not in stops:
                raise ValueError(f"[portal_trips] rail exit stations: stop '{stop_id}' of route #{route_index} "
                                 f"(mode '{mode}') is not in the transit stops")
            x, y = stops[stop_id]
            inside_cache[stop_id] = cordon_polygon.contains(Point(x, y))
        return inside_cache[stop_id]

    exits, exit_set, routes_scanned = [], set(), 0
    for route_index, (mode, sequence) in enumerate(routes):
        if mode is None or str(mode).strip().lower() not in rail_like_modes:
            continue
        routes_scanned += 1
        flags = [is_inside(stop_id, route_index, mode) for stop_id in sequence]
        for position, stop_id in enumerate(sequence):
            if not flags[position]:
                continue
            before = position > 0 and not flags[position - 1]
            after = position + 1 < len(sequence) and not flags[position + 1]
            if (before or after) and stop_id not in exit_set:
                exit_set.add(stop_id)
                exits.append(stop_id)
    logger.info("[portal_trips.gates] %d rail routes scanned, %d exit stations found", routes_scanned, len(exits))
    frame = pd.DataFrame({"stop_id": exits})
    return gpd.GeoDataFrame(frame, geometry=[Point(*stops[s]) for s in exits], crs=crs)


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


def _nearest_by_detour(origin_xy, point_xy, candidate_xy):
    """Index of the candidate minimising |origin - candidate| + |candidate - point| per row."""
    inside = np.hypot(origin_xy[:, None, 0] - candidate_xy[None, :, 0],
                      origin_xy[:, None, 1] - candidate_xy[None, :, 1])
    outside = np.hypot(point_xy[:, None, 0] - candidate_xy[None, :, 0],
                       point_xy[:, None, 1] - candidate_xy[None, :, 1])
    return np.argmin(inside + outside, axis=1)


def choose_gates(origin_xy, point_xy, modes, road_gates, rail_stations) -> pd.DataFrame:
    """The gate of each stay: a rail exit station for pt, the road gate with the smallest detour otherwise."""
    origin_xy = np.asarray(origin_xy, dtype=float)
    point_xy = np.asarray(point_xy, dtype=float)
    modes = np.asarray(modes, dtype=object)
    if not np.isfinite(origin_xy).all():
        raise ValueError("[portal_trips] origin coordinates contain non-finite values; no gate can be chosen")
    if not np.isfinite(point_xy).all():
        raise ValueError("[portal_trips] external point coordinates contain non-finite values; "
                         "no gate can be chosen")
    is_rail = modes == "pt"
    out = pd.DataFrame(index=range(len(modes)), columns=GATE_COLUMNS)
    if is_rail.any():
        if len(rail_stations) == 0:
            raise ValueError(f"[portal_trips] {int(is_rail.sum())} pt stays but no rail exit station inside the extent")
        rail_xy = np.column_stack([rail_stations.geometry.x.to_numpy(), rail_stations.geometry.y.to_numpy()])
        picked = _nearest_by_detour(origin_xy[is_rail], point_xy[is_rail], rail_xy)
        out.loc[is_rail, "gate_id"] = rail_stations["stop_id"].astype(str).to_numpy()[picked]
        out.loc[is_rail, "kind"] = KIND_RAIL
        out.loc[is_rail, "x"] = rail_xy[picked, 0]
        out.loc[is_rail, "y"] = rail_xy[picked, 1]
    if (~is_rail).any():
        if len(road_gates) == 0:
            raise ValueError(f"[portal_trips] {int((~is_rail).sum())} road stays but no road gate")
        road_xy = np.column_stack([road_gates.geometry.x.to_numpy(), road_gates.geometry.y.to_numpy()])
        picked = _nearest_by_detour(origin_xy[~is_rail], point_xy[~is_rail], road_xy)
        out.loc[~is_rail, "gate_id"] = road_gates["gate_id"].astype(str).to_numpy()[picked]
        out.loc[~is_rail, "kind"] = KIND_ROAD
        out.loc[~is_rail, "x"] = road_xy[picked, 0]
        out.loc[~is_rail, "y"] = road_xy[picked, 1]
    out["x"] = out["x"].astype(float)
    out["y"] = out["y"].astype(float)
    return out
