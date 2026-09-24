"""Official VRB tariff-zone polygons as one GeoDataFrame per scenario.

Source: the Regionalverband Grossraum Braunschweig ArcGIS layer ``Verkehr/Tarifzonen``
(Data Registry record ``vrb_tariff_zone_polygons``): 46 polygons, EPSG:25832, VRB zone id
in the ``Tarifzone`` field. VRB zones 55 and 56 (Haemelerwald and Dedenhausen stations,
Region Hannover) are not in the layer; VRB terms 2026 section 9.2 treat them as separate
zones, so they are added here as point zones around the station complex in the cleaned GTFS:
the rail-served stops carrying the station name plus every stop sharing their parent station
(the forecourt bus bays), never the other stops of the village (ADR-0133 D2).

Overlap slivers between polygons (one of 1,798 m2 between zones 20 and 15 in the 2023
layer) are trimmed so that the LOWER zone id keeps the area; every trim is reported.
CRS: EPSG:25832 throughout; all distances in metres.
"""
from __future__ import annotations

import hashlib
import inspect
import json
import logging
from pathlib import Path
from typing import Mapping

import geopandas as gpd
import pandas as pd
from shapely.ops import unary_union
from shapely.validation import make_valid

from braunschweig.data.vrb import line_scopes

log = logging.getLogger(__name__)

CRS = "EPSG:25832"
ZONE_FIELD = "Tarifzone"
DEFAULT_POLYGONS_PATH = "vrb/vrb_tarifzonen_rgb_25832.geojson"
DEFAULT_FEATURE_COUNT = 46
#: ADR-0133 D2: single-station zones outside the layer, found by the name of their rail-served GTFS stop.
DEFAULT_POINT_STATIONS = {"55": "Hämelerwald", "56": "Dedenhausen"}
#: ASSUMPTION: tolerance around each explicit station stop; schedule stop facilities keep the GTFS
#: coordinates, so the radius only absorbs rounding and must stay below the distance to other stations.
DEFAULT_POINT_RADIUS_M = 50.0
#: ASSUMPTION: the platforms and bus bays of one station lie within this distance of their centroid;
#: a wider complex means the name or parent-station match picked up something else.
DEFAULT_MAXIMUM_STATION_SPREAD_M = 300.0
#: ASSUMPTION: overlaps up to this area are digitisation noise at shared borders and are left as they are.
DEFAULT_MINIMUM_OVERLAP_M2 = 1.0
#: stop_times.txt of the cleaned DELFI feed is about 110 MB; it is read in chunks and filtered.
STOP_TIMES_CHUNK_ROWS = 1_000_000
#: The rail/bus classification is line_scopes.mode_class; hashed into validate() because this stage reads it.
_HELPER_MODULES = (line_scopes,)
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


def normalise_gtfs_id(value) -> str | None:
    """GTFS id as text: the cleaned feed writes parent ids as floats ("402454.0") and gaps as "nan"."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if text in ("", "nan", "NaN", "None"):
        return None
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def rail_served_stop_ids(gtfs_dir, candidate_stop_ids, *, chunk_rows: int = STOP_TIMES_CHUNK_ROWS) -> set[str]:
    """The candidate stops that at least one trip of a rail route (line_scopes.mode_class) serves."""
    gtfs_dir = Path(gtfs_dir)
    routes = pd.read_csv(gtfs_dir / "routes.txt", dtype={"route_id": str}, usecols=["route_id", "route_type"])
    rail_routes = {route for route, route_type in zip(routes["route_id"], routes["route_type"])
                   if line_scopes.mode_class(int(route_type)) == "rail"}
    trips = pd.read_csv(gtfs_dir / "trips.txt", dtype={"route_id": str, "trip_id": str}, usecols=["route_id", "trip_id"])
    rail_trips = set(trips.loc[trips["route_id"].isin(rail_routes), "trip_id"])
    candidates = {normalise_gtfs_id(stop) for stop in candidate_stop_ids}
    served = set()
    for chunk in pd.read_csv(gtfs_dir / "stop_times.txt", dtype={"trip_id": str, "stop_id": str},
                             usecols=["trip_id", "stop_id"], chunksize=chunk_rows):
        chunk = chunk[chunk["stop_id"].isin(candidates) & chunk["trip_id"].isin(rail_trips)]
        served.update(chunk["stop_id"])
    return served


def station_point_zones(stops: pd.DataFrame, rail_stop_ids, names: Mapping[str, str], *, radius_m: float,
                        maximum_spread_m: float, existing: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """One point zone per station: the union of ``radius_m`` buffers around the stops of its station complex.

    The complex is every rail-served stop whose name contains the station name, plus every stop sharing
    their GTFS parent station (e.g. the forecourt bus stop), without the parent station row itself.
    Village stops that merely carry the place name are not part of it.
    """
    if not (radius_m > 0) or not (maximum_spread_m > 0):
        raise ValueError("radius_m and maximum_spread_m must be positive")
    stop_ids = stops["stop_id"].map(normalise_gtfs_id)
    parents = stops["parent_station"].map(normalise_gtfs_id) if "parent_station" in stops else pd.Series(None, index=stops.index)
    location = stops["location_type"].map(normalise_gtfs_id) if "location_type" in stops else pd.Series(None, index=stops.index)
    platforms = location.fillna("0") != "1"
    rail = {normalise_gtfs_id(stop) for stop in rail_stop_ids}
    rows = []
    for zone_id, name in names.items():
        named = stops["stop_name"].astype(str).str.contains(name, case=False, regex=False)
        rail_named = named & stop_ids.isin(rail) & platforms
        if not rail_named.any():
            raise ValueError(f"zone {zone_id}: no rail-served GTFS stop named like {name!r} in the cleaned feed")
        station_parents = set(parents[rail_named].dropna())
        in_complex = platforms & (rail_named | parents.isin(station_parents))
        complex_stops = stops[in_complex]
        points = gpd.GeoSeries(gpd.points_from_xy(complex_stops["stop_lon"].astype(float),
                                                  complex_stops["stop_lat"].astype(float)), crs="EPSG:4326").to_crs(CRS)
        spread_m = float(points.distance(points.union_all().centroid).max())
        if spread_m > maximum_spread_m:
            raise ValueError(f"zone {zone_id}: the stops of station {name!r} spread {spread_m:.0f} m around their "
                             f"centroid (limit {maximum_spread_m:.0f} m); refusing to build a point zone")
        geometry = unary_union([point.buffer(radius_m) for point in points])
        if existing.geometry.intersects(geometry).any():
            raise ValueError(f"zone {zone_id}: point zone intersects an existing polygon zone")
        rows.append({"zone_id": str(zone_id), "geometry": geometry,
                     "rail_stop_ids": sorted(stop_ids[rail_named]), "station_stop_ids": sorted(stop_ids[in_complex]),
                     "spread_m": round(spread_m, 1)})
    return gpd.GeoDataFrame(rows, crs=CRS)


def configure(context):
    context.config("data_path")
    context.config("vrb_zone_polygons_path", DEFAULT_POLYGONS_PATH)
    context.config("vrb_zone_polygon_feature_count", DEFAULT_FEATURE_COUNT)
    context.config("vrb_zone_point_stations", DEFAULT_POINT_STATIONS)
    context.config("vrb_zone_point_radius_m", DEFAULT_POINT_RADIUS_M)
    context.config("vrb_zone_point_maximum_station_spread_m", DEFAULT_MAXIMUM_STATION_SPREAD_M)
    context.config("vrb_zone_minimum_overlap_m2", DEFAULT_MINIMUM_OVERLAP_M2)
    context.stage("data.gtfs.cleaned")


def _polygons_path(context) -> Path:
    return Path(context.config("data_path")) / context.config("vrb_zone_polygons_path")


def validate(context):
    """Cache token: the polygon file bytes (a new download must invalidate the stage) and the helper source."""
    path = _polygons_path(context)
    if not path.is_file():
        raise RuntimeError(f"VRB zone polygons missing at {path}; download them with the curl command in "
                           "docs/registry/data/vrb_tariff_zone_polygons.yml")
    digest = hashlib.sha256(path.read_bytes())
    for module in _HELPER_MODULES:
        digest.update(inspect.getsource(module).encode("utf-8"))
    return digest.hexdigest()


def execute(context):
    zones, report = load_zone_polygons(_polygons_path(context),
                                       expected_feature_count=context.config("vrb_zone_polygon_feature_count"))
    zones, trimmed = resolve_overlaps(zones, minimum_overlap_m2=float(context.config("vrb_zone_minimum_overlap_m2")))
    gtfs_dir = Path(context.path("data.gtfs.cleaned")) / "output"
    stops_path = gtfs_dir / "stops.txt"
    if not stops_path.is_file():
        raise RuntimeError(f"cleaned GTFS stops missing at {stops_path}; the point zones need them")
    stops = pd.read_csv(stops_path, dtype=str)
    stations = context.config("vrb_zone_point_stations")
    named = pd.Series(False, index=stops.index)
    for name in stations.values():
        named |= stops["stop_name"].astype(str).str.contains(name, case=False, regex=False)
    rail_stop_ids = rail_served_stop_ids(gtfs_dir, set(stops.loc[named, "stop_id"]))
    points = station_point_zones(stops, rail_stop_ids, stations,
                                 radius_m=float(context.config("vrb_zone_point_radius_m")),
                                 maximum_spread_m=float(context.config("vrb_zone_point_maximum_station_spread_m")),
                                 existing=zones)
    result = gpd.GeoDataFrame(pd.concat([zones[["zone_id", "geometry"]], points[["zone_id", "geometry"]]],
                                        ignore_index=True), crs=CRS)
    output = Path(context.path())
    result.to_file(output / OUTPUT_GPKG, driver="GPKG")
    report.update({"polygon_count": int(len(zones)), "point_zone_count": int(len(points)),
                   "point_zones": {str(row.zone_id): {"rail_stop_ids": list(row.rail_stop_ids),
                                                      "station_stop_ids": list(row.station_stop_ids),
                                                      "spread_m": float(row.spread_m)} for row in points.itertuples()},
                   "trimmed_overlaps": trimmed})
    (output / OUTPUT_REPORT).write_text(json.dumps(report, indent=2), encoding="utf-8")
    log.info("[vrb-zones] polygons=%d point_zones=%d repaired=%s trimmed_overlaps=%d total_area_km2=%.1f",
             len(zones), len(points), report["invalid_repaired"], len(trimmed), report["total_area_km2"])
    for zone_id, zone in report["point_zones"].items():
        log.info("[vrb-zones] point zone %s: rail stops %s, station stops %s, spread %.1f m", zone_id,
                 zone["rail_stop_ids"], zone["station_stop_ids"], zone["spread_m"])
    return result
