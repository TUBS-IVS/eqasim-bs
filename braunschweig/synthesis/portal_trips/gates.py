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
    """One point per road gate: the endpoint of the gate's link that lies inside the extent."""
    link_geometry = links.set_index("link_id")["geometry"]
    rows, points = [], []
    for gate in gates.itertuples(index=False):
        line = link_geometry.get(gate.link_id)
        if line is None:
            raise ValueError(f"[portal_trips] gate {gate.gate_id}: link {gate.link_id} is not in the cordon network")
        candidates = [Point(line.coords[0]), Point(line.coords[-1])]
        inside = [point for point in candidates if cordon_polygon.contains(point)]
        if not inside:
            raise ValueError(f"[portal_trips] gate {gate.gate_id}: no endpoint of link {gate.link_id} "
                             "lies inside the cordon extent; the stay would be cut again")
        # Both endpoints inside (a short link entirely inside): take the one deeper in the extent.
        chosen = max(inside, key=lambda point: cordon_polygon.exterior.distance(point))
        rows.append((gate.gate_id, gate.link_id, gate.road_class, float(gate.capacity)))
        points.append(chosen)
    frame = pd.DataFrame(rows, columns=["gate_id", "link_id", "road_class", "capacity"])
    return gpd.GeoDataFrame(frame, geometry=points, crs=gates.crs)


def rail_exit_stations(stops: dict, routes: list, cordon_polygon,
                       rail_like_modes=RAIL_LIKE_MODES) -> gpd.GeoDataFrame:
    """Inside stops of rail routes that are adjacent, in route order, to a stop outside the extent."""
    inside_cache = {}

    def is_inside(stop_id):
        if stop_id not in inside_cache:
            x, y = stops[stop_id]
            inside_cache[stop_id] = cordon_polygon.contains(Point(x, y))
        return inside_cache[stop_id]

    exits = []
    for mode, sequence in routes:
        if mode is None or str(mode).strip().lower() not in rail_like_modes:
            continue
        flags = [is_inside(stop_id) for stop_id in sequence]
        for position, stop_id in enumerate(sequence):
            if not flags[position]:
                continue
            before = position > 0 and not flags[position - 1]
            after = position + 1 < len(sequence) and not flags[position + 1]
            if (before or after) and stop_id not in exits:
                exits.append(stop_id)
    frame = pd.DataFrame({"stop_id": exits})
    return gpd.GeoDataFrame(frame, geometry=[Point(*stops[s]) for s in exits], crs="EPSG:25832")


def draw_external_points(reported_m, origin_xy, external_points, tolerance, rng):
    """Draw one external point per stay at the reported distance (band ``reported * (1 +- tolerance)``).

    Population-weighted inside the band; the point nearest to the reported distance when the
    band is empty (counted in ``band_miss``). Returns ``(point_xy, commune_id, band_miss)``.
    """
    reported_m = np.asarray(reported_m, dtype=float)
    origin_xy = np.asarray(origin_xy, dtype=float)
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
