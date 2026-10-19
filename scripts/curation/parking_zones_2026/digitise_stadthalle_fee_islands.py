"""Digitise the three Parkscheininseln of the Braunschweig Stadthalle concept (one-off curation, issue #249).

Not part of the pipeline. Inputs are gitignored raw files of the curation session of 2026-09-29: the concept plan
raw_sources/bs_prmk_stadthalle.jpg (202308-Beschilderung-Bestand-PRMK-Stadthalle, 1920x1358 px, linked from
parkraummanagement.php), the Overpass response raw_overpass/03101000_overpass_2026-09-29.json, and the committed
control points stadthalle_plan_control_points.json (nine street junctions: plan pixel read off zoomed crops, EPSG:25832
from the OSM ways). The page names the islands by street (Marthastrasse/Koernerstrasse, Gerstaeckerstrasse/Kleine
Campestrasse, Mentestrasse); the plan draws their extent as dark-blue 'Parkgebuehren' kerb lines.

Method: a similarity transform (scale, rotation, shift) from EPSG:25832 to plan pixels is fitted to the control points
by least squares. The dark-blue pixels (B > 110, B - R > 70, R < 90, G < 130; legend box excluded) are grouped by a
10 px dilation (joins dashes and both kerb lines of a street) and transformed to EPSG:25832; each island takes the
group nearest to its OSM junction (at most 60 m away; every group must belong to an island). For every street the
page names for the island, the blue pixels within 12 m of its OSM ways are projected onto the way, and the section
between the smallest and the largest projection is kept; the island is the union of its sections buffered 15 m (the
street_list_buffer standard of the zone file). The parameters and the fit are written into every feature and end up
in the digitising notes. Output: GeoJSON (EPSG:4326) read by assemble_parking_zones.py.

Usage (from the repository root)::

    python scripts/curation/parking_zones_2026/digitise_stadthalle_fee_islands.py \
        --plan eqasim-data/data/braunschweig/parking/raw_sources/bs_prmk_stadthalle.jpg \
        --control-points scripts/curation/parking_zones_2026/stadthalle_plan_control_points.json \
        --raw-overpass eqasim-data/data/braunschweig/parking/raw_overpass --out stadthalle_fee_islands.geojson
"""
from __future__ import annotations

import argparse
import json

import geopandas as gpd
import numpy as np
from PIL import Image
from scipy import ndimage
from shapely.geometry import MultiPoint
from shapely.ops import substring, unary_union

import curation_common as cc

#: Legend box of the plan (x0, y0, x1, y1 in px); its 'Parkgebuehren' sample line is not an island.
LEGEND_BOX_PX = (1280, 0, 1920, 440)
GROUP_DILATION_PX = 10
MINIMUM_GROUP_PX = 30
MAXIMUM_JUNCTION_DISTANCE_M = 60.0
SECTION_TOLERANCE_M = 12.0
MINIMUM_SECTION_POINTS = 10
#: Share of an island's blue pixels that must lie within SECTION_TOLERANCE_M of its named streets.
MINIMUM_COVERED_SHARE = 0.95
STREET_BUFFER_M = 15.0
#: zone_id, the OSM street names of the island (as the page names them), the junction that anchors the island.
ISLANDS = (
    ("bs_parkscheininsel_marthastrasse_koernerstrasse", ("Marthastraße", "Körnerstraße"), ("Körnerstraße", "Marthastraße")),
    ("bs_parkscheininsel_gerstaeckerstrasse_kleine_campestrasse", ("Gerstäckerstraße", "Kleine Campestraße"),
     ("Gerstäckerstraße", "Kleine Campestraße")),
    ("bs_parkscheininsel_mentestrasse", ("Mentestraße",), ("Mentestraße", "Schillstraße")),
)
ASCII = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def fit_similarity(points):
    """Least-squares similarity EPSG:25832 -> plan px (y down, north up); returns (to_px, to_world, report)."""
    pixel = np.array([[p["px"], p["py"]] for p in points], dtype=float)
    world = np.array([[p["E"], p["N"]] for p in points], dtype=float)
    e0, n0 = world.mean(axis=0)
    u, v = world[:, 0] - e0, world[:, 1] - n0
    design = np.zeros((2 * len(u), 4))
    design[0::2] = np.column_stack([u, -v, np.ones_like(u), np.zeros_like(u)])
    design[1::2] = np.column_stack([-v, -u, np.zeros_like(u), np.ones_like(u)])
    (a, b, c, d), *_ = np.linalg.lstsq(design, pixel.reshape(-1), rcond=None)
    residual = np.hypot(*(design @ np.array([a, b, c, d]) - pixel.reshape(-1)).reshape(-1, 2).T)
    scale = np.hypot(a, b)

    def to_world(px, py):
        # Invert px = a u - b v + c, py = -b u - a v + d.
        x, y = np.asarray(px, dtype=float) - c, np.asarray(py, dtype=float) - d
        uu, vv = (a * x - b * y) / scale ** 2, (-b * x - a * y) / scale ** 2
        return uu + e0, vv + n0

    rms_px = float(np.sqrt((residual ** 2).mean()))
    report = {"control_points": len(points), "metres_per_px": float(1 / scale),
              "rotation_deg": float(np.degrees(np.arctan2(b, a))), "cp_rms_px": rms_px, "cp_rms_m": rms_px / scale,
              "cp_residual_px": {p["label"]: round(float(r), 2) for p, r in zip(points, residual)}}
    return to_world, report


def junction(streets: gpd.GeoDataFrame, first: str, second: str):
    a = unary_union(list(streets[streets["name"] == first].geometry))
    b = unary_union(list(streets[streets["name"] == second].geometry))
    points = [p for p in getattr(a.intersection(b), "geoms", [a.intersection(b)]) if p.geom_type == "Point"]
    if len(points) != 1:
        raise SystemExit(f"expected one junction of {first} and {second} in OSM, found {len(points)}")
    return points[0]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--plan", required=True)
    parser.add_argument("--control-points", required=True)
    parser.add_argument("--raw-overpass", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args(argv)

    to_world, fit = fit_similarity(json.load(open(args.control_points, encoding="utf-8"))["points"])
    print(f"similarity fit: {fit['control_points']} control points, {fit['metres_per_px']:.4f} m/px, rotation "
          f"{fit['rotation_deg']:.2f} deg, RMS {fit['cp_rms_px']:.2f} px = {fit['cp_rms_m']:.1f} m")
    for label, residual in fit["cp_residual_px"].items():
        print(f"   {label:45s} {residual:5.2f} px")

    rgb = np.asarray(Image.open(args.plan).convert("RGB")).astype(int)
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    blue = (b > 110) & ((b - r) > 70) & (r < 90) & (g < 130)
    x0, y0, x1, y1 = LEGEND_BOX_PX
    blue[y0:y1, x0:x1] = False
    labels, count = ndimage.label(ndimage.binary_dilation(blue, iterations=GROUP_DILATION_PX))
    groups = []
    for label in range(1, count + 1):
        rows, cols = np.nonzero((labels == label) & blue)
        if len(cols) < MINIMUM_GROUP_PX:
            continue
        east, north = to_world(cols + 0.5, rows + 0.5)
        groups.append({"pixels": len(cols), "points": MultiPoint(np.column_stack([east, north])),
                       "bbox_px": [int(cols.min()), int(rows.min()), int(cols.max()), int(rows.max())]})
    print(f"{len(groups)} blue groups outside the legend: " + ", ".join(f"{g['pixels']} px {g['bbox_px']}" for g in groups))

    streets = cc.named_ways(cc.overpass_response(args.raw_overpass, "03101000"))
    streets = streets[streets["highway"] != ""]
    rows, used = [], set()
    for zone_id, names, anchor in ISLANDS:
        centre = junction(streets, *anchor)
        distances = [g["points"].centroid.distance(centre) for g in groups]
        nearest = int(np.argmin(distances))
        if distances[nearest] > MAXIMUM_JUNCTION_DISTANCE_M or nearest in used:
            raise SystemExit(f"{zone_id}: no blue group within {MAXIMUM_JUNCTION_DISTANCE_M} m of the junction")
        used.add(nearest)
        points = list(groups[nearest]["points"].geoms)
        sections, lengths, way_ids = [], {}, []
        for name in names:
            length = 0.0
            for _, way in streets[streets["name"] == name].iterrows():
                near = [p for p in points if way.geometry.distance(p) <= SECTION_TOLERANCE_M]
                if len(near) < MINIMUM_SECTION_POINTS:
                    continue
                positions = [way.geometry.project(p) for p in near]
                section = substring(way.geometry, min(positions), max(positions))
                sections.append(section)
                way_ids.append(int(way["osm_id"]))
                length += section.length
            if length == 0:
                raise SystemExit(f"{zone_id}: no blue section on {name}")
            lengths[name.translate(ASCII)] = round(length, 1)
        named = unary_union(list(streets[streets["name"].isin(names)].geometry))
        covered = sum(named.distance(p) <= SECTION_TOLERANCE_M for p in points) / len(points)
        if covered < MINIMUM_COVERED_SHARE:
            raise SystemExit(f"{zone_id}: only {covered:.1%} of the blue pixels lie within {SECTION_TOLERANCE_M} m of "
                             f"the named streets {names}; the plan shows the island on another street")
        geometry = unary_union([section.buffer(STREET_BUFFER_M) for section in sections])
        print(f"{zone_id}: group of {groups[nearest]['pixels']} px at {distances[nearest]:.1f} m from the junction "
              f"(E {centre.x:.1f} / N {centre.y:.1f}), {covered:.1%} within {SECTION_TOLERANCE_M:.0f} m of the named "
              f"streets; sections {lengths} m on OSM ways {way_ids}; {geometry.area:.0f} m2")
        rows.append({"zone_id": zone_id, "streets": ", ".join(n.translate(ASCII) for n in names),
                     "osm_way_ids": ", ".join(str(i) for i in way_ids),
                     "sections": ", ".join(f"{k} {v:.0f} m" for k, v in lengths.items()),
                     "blue_px": groups[nearest]["pixels"], "blue_share_on_named_streets": round(covered, 3),
                     "junction_e": round(centre.x, 1), "junction_n": round(centre.y, 1),
                     "cp_count": fit["control_points"], "cp_rms_px": round(fit["cp_rms_px"], 2),
                     "cp_rms_m": round(fit["cp_rms_m"], 2), "metres_per_px": round(fit["metres_per_px"], 4),
                     "rotation_deg": round(fit["rotation_deg"], 2), "geometry": geometry})
    if len(used) != len(groups):
        raise SystemExit(f"{len(groups) - len(used)} blue group(s) of the plan belong to no island")
    gpd.GeoDataFrame(rows, geometry="geometry", crs=cc.METRIC_CRS).to_crs("EPSG:4326").to_file(args.out, driver="GeoJSON")
    print(f"written {args.out} with {len(rows)} islands")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
