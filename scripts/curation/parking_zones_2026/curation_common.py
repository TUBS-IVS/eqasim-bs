"""Shared helpers of the one-off curation of the 2026 parking cost zones (issue #249). Not part of the pipeline.

The curation scripts in this directory produced the committed
``eqasim-data/data/braunschweig/parking/parking_zones_2026.geojson``; their inputs are the gitignored raw files of
the curation session (``raw_overpass/`` Overpass responses of 2026-09-29, ``raw_sources/`` retrieved municipal
maps) and the OSM-derived car network of a local MATSim scenario. Every parameter that shaped a polygon is also
recorded in that polygon's ``digitising_note``. CRS: EPSG:4326 for OSM coordinates, EPSG:25832 (metres) for all
geometry work.
"""
from __future__ import annotations

import gzip
import json
import unicodedata
import xml.etree.ElementTree as ElementTree
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString, Point, Polygon
from shapely.ops import unary_union

METRIC_CRS = "EPSG:25832"


def overpass_response(raw_overpass_dir, ags: str, date: str = "2026-09-29") -> dict:
    """The saved Overpass response of one municipality (written by scripts/build_parking_zones_from_osm.py)."""
    path = Path(raw_overpass_dir) / f"{ags}_overpass_{date}.json"
    if not path.is_file():
        raise FileNotFoundError(f"Overpass response missing: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def element_geometry(element: dict):
    """WGS84 geometry of an Overpass element: node point, closed non-highway way polygon, other ways lines."""
    tags = element.get("tags", {})
    coordinates = [(point["lon"], point["lat"]) for point in (element.get("geometry") or []) if point]
    if element["type"] == "node" and "lat" in element:
        return Point(element["lon"], element["lat"])
    if len(coordinates) < 2:
        return None
    if "highway" not in tags and len(coordinates) >= 4 and coordinates[0] == coordinates[-1]:
        return Polygon(coordinates)
    return LineString(coordinates)


def to_metric(geometries):
    return list(gpd.GeoSeries(geometries, crs="EPSG:4326").to_crs(METRIC_CRS))


def is_paid(tags: dict) -> bool:
    """fee=yes car parks and highway ways with an on-street fee or ticket tag (the helper's fee evidence)."""
    return (tags.get("amenity") == "parking" and str(tags.get("fee", "")).startswith("yes")) or (
        "highway" in tags and any(("fee" in key and "yes" in str(value)) or ("condition" in key and "ticket" in str(value))
                                  for key, value in tags.items()))


def paid_objects(response: dict, window):
    """Fee-tagged objects intersecting a metric window: list of (osm reference, kind, metric geometry, name)."""
    elements = response["elements"]
    rows = []
    for element, geometry in zip(elements, to_metric([element_geometry(e) for e in elements])):
        tags = element.get("tags", {})
        if geometry is None or not is_paid(tags) or not geometry.intersects(window):
            continue
        rows.append((f"{element['type']}/{element['id']}", "street" if "highway" in tags else "parking", geometry,
                     tags.get("name", "")))
    return rows


def named_ways(response: dict) -> gpd.GeoDataFrame:
    """All ways of a response as a metric frame with name and highway tags."""
    ways = [e for e in response["elements"] if e["type"] == "way"]
    return gpd.GeoDataFrame({"osm_id": [e["id"] for e in ways],
                             "name": [e.get("tags", {}).get("name", "") for e in ways],
                             "highway": [e.get("tags", {}).get("highway", "") for e in ways]},
                            geometry=to_metric([element_geometry(e) for e in ways]), crs=METRIC_CRS)


def fill_holes(geometry):
    parts = getattr(geometry, "geoms", [geometry])
    return unary_union([Polygon(part.exterior) for part in parts if part.geom_type == "Polygon"])


def closed_union(geometries, closing_m: float):
    """Union, morphological closing by ``closing_m`` metres (mitred), enclosed holes filled."""
    union = unary_union(geometries)
    closed = union.buffer(closing_m, join_style=2).buffer(-closing_m, join_style=2)
    return fill_holes(closed.union(union))


def largest_parts(geometry, minimum_m2: float):
    return unary_union([part for part in getattr(geometry, "geoms", [geometry]) if part.area >= minimum_m2])


def load_network_links(network_path) -> gpd.GeoDataFrame:
    """Car-network links of a MATSim network (osm:way:name / osm:way:highway attributes) as metric lines.

    The network of the local scenario eqasim-data/output_bs (2026-04-29) is OSM-derived and in EPSG:25832.
    """
    nodes, rows, current = {}, [], None
    with gzip.open(network_path, "rb") as stream:
        for event, element in ElementTree.iterparse(stream, events=("start", "end")):
            tag = element.tag
            if event == "start" and tag == "link":
                current = {"id": element.get("id"), "from": element.get("from"), "to": element.get("to"), "attrs": {}}
            elif event == "end" and tag == "node":
                nodes[element.get("id")] = (float(element.get("x")), float(element.get("y")))
                element.clear()
            elif event == "end" and tag == "attribute" and current is not None:
                current["attrs"][element.get("name")] = element.text
            elif event == "end" and tag == "link":
                rows.append(current)
                current = None
                element.clear()
    records = []
    for link in rows:
        start, end = nodes.get(link["from"]), nodes.get(link["to"])
        if start is None or end is None:
            continue
        records.append({"link_id": link["id"], "name": link["attrs"].get("osm:way:name", ""),
                        "highway": link["attrs"].get("osm:way:highway", ""), "geometry": LineString([start, end])})
    return gpd.GeoDataFrame(records, geometry="geometry", crs=METRIC_CRS)


#: German letters in the ASCII form of the committed parking files (umlauts as two letters, sharp s as ss).
GERMAN_ASCII = str.maketrans({"\u00e4": "ae", "\u00f6": "oe", "\u00fc": "ue", "\u00c4": "Ae", "\u00d6": "Oe",
                              "\u00dc": "Ue", "\u00df": "ss", "\u1e9e": "SS"})


def ascii_transliteration(text: str) -> str:
    """``text`` in the ASCII form of the committed files: German letters as ae/oe/ue/ss, other accents dropped
    (NFKD); a character that has no ASCII form raises instead of being replaced silently."""
    folded = unicodedata.normalize("NFKD", text.translate(GERMAN_ASCII))
    folded = "".join(character for character in folded if not unicodedata.combining(character))
    offending = sorted({character for character in folded if ord(character) > 127})
    if offending:
        raise SystemExit(f"no ASCII form for {offending} in the QA table")
    return folded
