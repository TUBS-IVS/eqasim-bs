"""Derive cordon gates: network links crossing the cordon boundary.

A gate is the point where a network link intersects the cordon-polygon boundary.
Each crossing link yields one gate point, carrying the link's capacity so callers
can rank/select the dominant corridors. External origin Kreise are then mapped to
their nearest gate. Region-neutral; expects a metric CRS (EPSG:25832 for ZGB).
Rail/PT gates are derived separately from GTFS entry stops.

Part of the cross-cordon external-demand module; see
``docs/superpowers/specs/2026-06-05-cross-cordon-external-demand-design.md``.
"""
from __future__ import annotations

import geopandas as gpd
from shapely.geometry import Point
from shapely.geometry.base import BaseGeometry


def derive_road_gates(links: gpd.GeoDataFrame, cordon: BaseGeometry) -> gpd.GeoDataFrame:
    """Return one gate Point per network link that crosses the cordon boundary.

    A link "crosses" if it intersects the cordon-polygon boundary (i.e. it has
    points both inside and outside). The gate is the boundary intersection point
    (a representative point if the intersection is multi-part). Output is sorted by
    descending ``capacity`` so callers can pick the dominant corridors first.

    Args:
        links: LineString network links with ``link_id`` and ``capacity`` columns.
        cordon: the cordon polygon (its ``.boundary`` is the crossing line).

    Returns:
        GeoDataFrame [link_id, capacity, geometry(Point)] in ``links.crs``.
    """
    boundary = cordon.boundary
    has_road_class = "road_class" in links.columns
    columns = ["link_id", "capacity"] + (["road_class"] if has_road_class else []) + ["geometry"]
    rows = []
    for _, row in links.iterrows():
        geom = row.geometry
        if geom is None or not geom.intersects(boundary):
            continue
        hit = geom.intersection(boundary)
        pt = hit if isinstance(hit, Point) else hit.representative_point()
        record = {"link_id": row["link_id"], "capacity": row.get("capacity"),
                  "geometry": Point(pt.x, pt.y)}
        if has_road_class:
            record["road_class"] = row.get("road_class")
        rows.append(record)
    gates = gpd.GeoDataFrame(rows, columns=columns, geometry="geometry", crs=links.crs)
    if len(gates):
        gates = gates.sort_values("capacity", ascending=False).reset_index(drop=True)
    return gates


def dedupe_gates(gates: gpd.GeoDataFrame, tolerance_m: float = 100.0) -> gpd.GeoDataFrame:
    """Merge gates at the same physical boundary crossing into one.

    A road crossing the cordon has two directed links (one per direction), so
    :func:`derive_road_gates` yields two near-identical gate points per crossing.
    Snap gate points to a ``tolerance_m`` grid and keep one representative per cell
    (the highest-capacity row), so each physical corridor counts once.

    Args:
        gates: output of :func:`derive_road_gates` (point geometry, ``capacity``).
        tolerance_m: grid size in metres for collapsing co-located gates.

    Returns:
        The deduplicated gates, capacity-sorted descending.
    """
    if len(gates) == 0:
        return gates
    g = gates.copy()
    g["_gx"] = (g.geometry.x / tolerance_m).round().astype(int)
    g["_gy"] = (g.geometry.y / tolerance_m).round().astype(int)
    g = g.sort_values("capacity", ascending=False).drop_duplicates(subset=["_gx", "_gy"],
                                                                   keep="first")
    return g.drop(columns=["_gx", "_gy"]).reset_index(drop=True)


def select_major_gates(gates: gpd.GeoDataFrame, allowed_classes=None,
                       min_capacity: float | None = None,
                       top_n: int | None = None) -> gpd.GeoDataFrame:
    """Restrict gates to the main corridors where long-distance commuters enter.

    Long-distance car commuters use motorways / trunk / primary roads, so keep
    only those gates (by ``road_class`` allowlist and/or a minimum capacity and/or
    the top-N by capacity). If the filters would leave no gate but there were
    crossings, fall back to the single highest-capacity gate so a region never
    ends up gateless.

    Args:
        gates: output of :func:`derive_road_gates` (sorted by capacity desc).
        allowed_classes: iterable of road classes to keep (needs a ``road_class``
            column); e.g. ``{"motorway", "trunk", "primary"}``. None = no class filter.
        min_capacity: keep only gates with ``capacity >= min_capacity``. None = off.
        top_n: keep only the top-N gates by capacity. None = keep all matching.

    Returns:
        The filtered gates (capacity-sorted), or the strongest single gate as a
        fallback.
    """
    if len(gates) == 0:
        return gates
    ranked = gates.sort_values("capacity", ascending=False).reset_index(drop=True)
    out = ranked
    if allowed_classes is not None and "road_class" in out.columns:
        out = out[out["road_class"].isin(set(allowed_classes))]
    if min_capacity is not None:
        out = out[out["capacity"] >= min_capacity]
    if top_n is not None:
        out = out.head(top_n)
    if len(out) == 0:
        # Never leave a region without a gate when links did cross the boundary.
        out = ranked.head(1)
    return out.reset_index(drop=True)


def assign_kreise_to_gates(kreise: gpd.GeoDataFrame, gates: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Attach the nearest gate to each Kreis (by the Kreis representative point).

    Args:
        kreise: external origin Kreise (any geometry; uses representative_point()).
        gates: gate points; must carry a ``gate_id`` column.

    Returns:
        ``kreise`` with the nearest ``gate_id`` (and gate geometry) joined on.

    Raises:
        ValueError: if ``gates`` has no ``gate_id`` column.
    """
    if "gate_id" not in gates.columns:
        raise ValueError("assign_kreise_to_gates: gates needs a 'gate_id' column")
    k = kreise.copy()
    k["geometry"] = k.geometry.representative_point()
    joined = gpd.sjoin_nearest(k, gates[["gate_id", "geometry"]], how="left")
    return joined.drop(columns=[c for c in joined.columns if c.startswith("index_")])
