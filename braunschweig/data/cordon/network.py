"""Readers for the cordon module: MATSim network links + external Gemeinde points.

Importable counterparts of the parsing in ``scripts/validate_cordon_gates.py`` so the
synpp cordon stages and the validator share one implementation (DRY). Region-neutral;
metric CRS (EPSG:25832 for ZGB).

See ``docs/superpowers/plans/2026-06-05-cordon-einpendler-injection.md`` (Task 1b).
"""
from __future__ import annotations

import gzip
import os
import xml.etree.ElementTree as ET

import geopandas as gpd
from shapely.geometry import LineString

# Inner path of the layers inside the VG250-EW GeoPackage zip.
VG250_INNER_GPKG = ("vg250-ew_12-31.utm32s.gpkg.ebenen/"
                    "vg250-ew_ebenen_1231/DE_VG250.gpkg")

# The eight Zweckverband Grossraum Braunschweig (ZGB) Kreise, as 5-digit ARS prefixes.
ZGB_KREIS_PREFIXES = ("03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158")

# Candidate link attribute names that may carry the OSM road type.
_HIGHWAY_ATTR_NAMES = ("osm:way:highway", "osm_highway", "osm:highway", "highway", "type")


def _normalise_road_class(value):
    """Lower-case and collapse motorway_link/trunk_link/... to their base class."""
    if value is None:
        return None
    v = str(value).strip().lower()
    if v.endswith("_link"):
        v = v[: -len("_link")]
    return v or None


def read_matsim_links(network_path: str, crs: str = "EPSG:25832") -> gpd.GeoDataFrame:
    """Parse a MATSim network XML(.gz) into a link GeoDataFrame.

    Returns columns ``[link_id, capacity, road_class, geometry]`` (LineString) in
    ``crs``. Nodes are read first into a coordinate map; links are streamed to keep
    memory bounded on large networks. The road class is taken from the first
    recognised highway attribute (link ``type`` attribute or an ``<attributes>``
    child), normalised so ``motorway_link`` counts as ``motorway``.
    """
    opener = gzip.open if network_path.endswith(".gz") else open
    nodes: dict[str, tuple[float, float]] = {}
    rows = []
    with opener(network_path, "rb") as handle:
        for _event, elem in ET.iterparse(handle, events=("end",)):
            if elem.tag == "node":
                nid = elem.get("id")
                if nid is not None:
                    nodes[nid] = (float(elem.get("x")), float(elem.get("y")))
                elem.clear()
            elif elem.tag == "link":
                src, dst = elem.get("from"), elem.get("to")
                if src in nodes and dst in nodes:
                    road_class = _normalise_road_class(elem.get("type"))
                    if road_class is None:
                        for attr in elem.iter("attribute"):
                            if attr.get("name") in _HIGHWAY_ATTR_NAMES:
                                road_class = _normalise_road_class(attr.text)
                                if road_class is not None:
                                    break
                    cap = elem.get("capacity")
                    rows.append({
                        "link_id": elem.get("id"),
                        "capacity": float(cap) if cap is not None else None,
                        "road_class": road_class,
                        "geometry": LineString([nodes[src], nodes[dst]]),
                    })
                elem.clear()
    if not rows:
        raise ValueError(f"No links parsed from {network_path}")
    return gpd.GeoDataFrame(rows, geometry="geometry", crs=crs)


def read_transit_stops_routes(schedule_path: str):
    """Parse a MATSim transit schedule XML(.gz) into stops + routes.

    Returns ``(stops, routes)`` where ``stops`` is ``{stop_id: (x, y)}`` and
    ``routes`` is a list of ``(transport_mode, [stop_id, ...])`` in route order. Used
    to derive PT entry stops (one-seat rides into ZGB) for PT in-commuters.
    """
    opener = gzip.open if schedule_path.endswith(".gz") else open
    stops: dict[str, tuple[float, float]] = {}
    routes = []
    current = None
    mode = None
    with opener(schedule_path, "rb") as handle:
        for event, elem in ET.iterparse(handle, events=("start", "end")):
            tag = elem.tag.split("}")[-1]
            if event == "end" and tag == "stopFacility":
                stops[elem.get("id")] = (float(elem.get("x")), float(elem.get("y")))
                elem.clear()
            elif event == "start" and tag == "transitRoute":
                current = []
            elif event == "end" and tag == "transportMode":
                mode = elem.text
            elif event == "end" and tag == "stop":
                if current is not None and elem.get("refId"):
                    current.append(elem.get("refId"))
            elif event == "end" and tag == "transitRoute":
                routes.append((mode, current))
                current = None
                mode = None
    return stops, routes


def read_kreise(vg250_path: str, crs: str = "EPSG:25832") -> gpd.GeoDataFrame:
    """Kreis polygons [ars5, geometry] from VG250 (vg250_krs), land area only."""
    src = (f"/vsizip/{os.path.abspath(vg250_path)}/{VG250_INNER_GPKG}"
           if vg250_path.endswith(".zip") else vg250_path)
    krs = gpd.read_file(src, layer="vg250_krs")
    krs["ARS"] = krs["ARS"].astype(str)
    if "GF" in krs.columns:
        krs = krs[krs["GF"] == 4]
    krs = krs.to_crs(crs).copy()
    krs["ars5"] = krs["ARS"].str[:5]
    return krs.dissolve(by="ars5", as_index=False)[["ars5", "geometry"]]


def read_external_gemeinden(vg250_path: str, crs: str = "EPSG:25832",
                            zgb_prefixes=ZGB_KREIS_PREFIXES) -> gpd.GeoDataFrame:
    """External (non-ZGB) Gemeinde points with population (EWZ) from VG250 (vg250_gem).

    Returns columns ``[ars5, ewz, geometry]``. ``vg250_path`` may be the VG250-EW zip
    (the inner GeoPackage is addressed via ``/vsizip``) or a plain GeoPackage path.
    """
    if not vg250_path:
        raise ValueError("read_external_gemeinden: no vg250_path given")
    src = (f"/vsizip/{os.path.abspath(vg250_path)}/{VG250_INNER_GPKG}"
           if vg250_path.endswith(".zip") else vg250_path)
    gem = gpd.read_file(src, layer="vg250_gem")
    gem["ARS"] = gem["ARS"].astype(str)
    if "GF" in gem.columns:
        gem = gem[gem["GF"] == 4]   # land area
    if "EWZ" not in gem.columns:
        raise ValueError("vg250_gem has no EWZ (population) column")
    ext = gem[~gem["ARS"].str[:5].isin(set(zgb_prefixes))].to_crs(crs).copy()
    ext["ars5"] = ext["ARS"].str[:5]
    return ext.rename(columns={"EWZ": "ewz"})[["ars5", "ewz", "geometry"]]
