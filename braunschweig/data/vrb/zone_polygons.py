"""Official VRB tariff-zone polygons as one GeoDataFrame per scenario.

Source: the Regionalverband Grossraum Braunschweig ArcGIS layer ``Verkehr/Tarifzonen``
(Data Registry record ``vrb_tariff_zone_polygons``): 46 polygons, EPSG:25832, VRB zone id
in the ``Tarifzone`` field. VRB zones 55 and 56 (Haemelerwald and Dedenhausen stations,
Region Hannover) are not in the layer; VRB terms 2026 section 9.2 treat them as separate
zones, so they are added here as buffered points around the cleaned GTFS stops carrying
those names (ASSUMPTION: 300 m radius, ADR-0133 D2).

Overlap slivers between polygons (one of 1,798 m2 between zones 20 and 15 in the 2023
layer) are trimmed so that the LOWER zone id keeps the area; every trim is reported.
CRS: EPSG:25832 throughout; all distances in metres.
"""
from __future__ import annotations

import hashlib
import json
import logging
from pathlib import Path
from typing import Mapping

import geopandas as gpd
import pandas as pd
from shapely.validation import make_valid

log = logging.getLogger(__name__)

CRS = "EPSG:25832"
ZONE_FIELD = "Tarifzone"
DEFAULT_POLYGONS_PATH = "vrb/vrb_tarifzonen_rgb_25832.geojson"
DEFAULT_FEATURE_COUNT = 46
#: ASSUMPTION (ADR-0133 D2): single-station zones outside the layer, matched by GTFS stop name.
DEFAULT_POINT_STATIONS = {"55": "Hämelerwald", "56": "Dedenhausen"}
DEFAULT_POINT_RADIUS_M = 300.0
OUTPUT_GPKG = "vrb_tariff_zones.gpkg"
OUTPUT_REPORT = "zone_polygons_report.json"


def load_zone_polygons(path, *, expected_feature_count=DEFAULT_FEATURE_COUNT, zone_field=ZONE_FIELD):
    """Read the layer, check CRS/count/ids, repair invalid rings; return (zones, report)."""
    zones = gpd.read_file(path)
    if zones.crs is None or zones.crs.to_epsg() != 25832:
        raise ValueError(f"{path}: zone polygons must be EPSG:25832, found {zones.crs}")
    if len(zones) != expected_feature_count:
        raise ValueError(f"{path}: expected {expected_feature_count} features, found {len(zones)}")
    if zone_field not in zones.columns:
        raise ValueError(f"{path}: zone id field {zone_field!r} is missing; columns {list(zones.columns)}")
    ids = zones[zone_field].astype(str).str.strip()
    duplicates = sorted(ids[ids.duplicated()].unique())
    if duplicates:
        raise ValueError(f"{path}: duplicate zone id(s) {duplicates}")
    invalid_mask = ~zones.geometry.is_valid
    repaired = zones.geometry.copy()
    repaired[invalid_mask] = zones.geometry[invalid_mask].apply(make_valid)
    result = gpd.GeoDataFrame({"zone_id": ids.values, "geometry": repaired.values}, crs=CRS)
    if not result.geometry.is_valid.all():
        raise ValueError(f"{path}: geometry still invalid after repair")
    report = {"source": str(path), "feature_count": int(len(result)),
              "invalid_repaired": sorted(ids[invalid_mask].tolist(), key=int),
              "total_area_km2": round(float(result.geometry.area.sum()) / 1e6, 1)}
    return result, report


def resolve_overlaps(zones: gpd.GeoDataFrame, *, minimum_overlap_m2: float = 1.0):
    """Trim pairwise overlaps so the lower zone id keeps the area; return (zones, trimmed_list)."""
    ordered = zones.sort_values("zone_id", key=lambda s: s.astype(int)).reset_index(drop=True)
    geometries = list(ordered.geometry)
    trimmed = []
    for i in range(len(geometries)):
        for j in range(i + 1, len(geometries)):
            if not geometries[i].intersects(geometries[j]):
                continue
            overlap = geometries[i].intersection(geometries[j])
            if overlap.area <= minimum_overlap_m2:
                continue
            geometries[j] = geometries[j].difference(geometries[i])
            trimmed.append({"kept_zone": str(ordered.zone_id[i]), "trimmed_zone": str(ordered.zone_id[j]),
                            "area_m2": round(float(overlap.area), 1)})
    resolved = ordered.copy()
    resolved["geometry"] = gpd.GeoSeries(geometries, crs=CRS)
    return resolved, trimmed


def station_point_zones(stops: pd.DataFrame, names: Mapping[str, str], radius_m: float,
                        existing: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Buffer the GTFS stops named like each station into one point zone per zone id."""
    if not (radius_m > 0):
        raise ValueError("radius_m must be positive")
    rows = []
    for zone_id, name in names.items():
        matches = stops[stops["stop_name"].astype(str).str.contains(name, case=False, regex=False)]
        if matches.empty:
            raise ValueError(f"zone {zone_id}: no GTFS stop named like {name!r} in the cleaned feed")
        points = gpd.GeoSeries(gpd.points_from_xy(matches["stop_lon"], matches["stop_lat"]),
                               crs="EPSG:4326").to_crs(CRS)
        centroid = points.union_all().centroid
        spread_m = float(points.distance(centroid).max())
        if spread_m > 2 * radius_m:
            raise ValueError(f"zone {zone_id}: stops named like {name!r} spread {spread_m:.0f} m around their "
                             f"centroid (limit {2 * radius_m:.0f} m); refusing to build a point zone")
        geometry = centroid.buffer(radius_m)
        if existing.geometry.intersects(geometry).any():
            raise ValueError(f"zone {zone_id}: point zone intersects an existing polygon zone")
        rows.append({"zone_id": str(zone_id), "geometry": geometry, "matched_stops": int(len(matches))})
    return gpd.GeoDataFrame(rows, crs=CRS)


def configure(context):
    context.config("data_path")
    context.config("vrb_zone_polygons_path", DEFAULT_POLYGONS_PATH)
    context.config("vrb_zone_polygon_feature_count", DEFAULT_FEATURE_COUNT)
    context.config("vrb_zone_point_stations", DEFAULT_POINT_STATIONS)
    context.config("vrb_zone_point_radius_m", DEFAULT_POINT_RADIUS_M)
    context.stage("data.gtfs.cleaned")


def _polygons_path(context) -> Path:
    return Path(context.config("data_path")) / context.config("vrb_zone_polygons_path")


def validate(context):
    """Cache token: the polygon file bytes (a new download must invalidate the stage)."""
    path = _polygons_path(context)
    if not path.is_file():
        raise RuntimeError(f"VRB zone polygons missing at {path}; download them with the curl command in "
                           "docs/registry/data/vrb_tariff_zone_polygons.yml")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def execute(context):
    zones, report = load_zone_polygons(_polygons_path(context),
                                       expected_feature_count=context.config("vrb_zone_polygon_feature_count"))
    zones, trimmed = resolve_overlaps(zones)
    stops_path = Path(context.path("data.gtfs.cleaned")) / "output" / "stops.txt"
    if not stops_path.is_file():
        raise RuntimeError(f"cleaned GTFS stops missing at {stops_path}; the point zones need them")
    stops = pd.read_csv(stops_path, dtype={"stop_id": str, "stop_name": str})
    points = station_point_zones(stops, context.config("vrb_zone_point_stations"),
                                 float(context.config("vrb_zone_point_radius_m")), zones)
    result = gpd.GeoDataFrame(pd.concat([zones[["zone_id", "geometry"]], points[["zone_id", "geometry"]]],
                                        ignore_index=True), crs=CRS)
    output = Path(context.path())
    result.to_file(output / OUTPUT_GPKG, driver="GPKG")
    report.update({"polygon_count": int(len(zones)), "point_zone_count": int(len(points)),
                   "point_zone_matched_stops": {str(row.zone_id): int(row.matched_stops) for row in points.itertuples()},
                   "trimmed_overlaps": trimmed})
    (output / OUTPUT_REPORT).write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("[vrb-zones] polygons=%d point_zones=%d repaired=%s trimmed_overlaps=%d total_area_km2=%.1f",
             len(zones), len(points), report["invalid_repaired"], len(trimmed), report["total_area_km2"])
    return result
