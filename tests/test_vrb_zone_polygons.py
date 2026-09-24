"""Synthetic-geometry tests for braunschweig.data.vrb.zone_polygons (no real polygons needed)."""
import json
from pathlib import Path

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Polygon, box

from braunschweig.data.vrb import zone_polygons as zp

CRS = "EPSG:25832"


def _write(path: Path, rows):
    gpd.GeoDataFrame(rows, crs=CRS).to_file(path, driver="GeoJSON")


def _three_zones(tmp_path: Path) -> Path:
    bowtie = Polygon([(2000, 0), (3000, 1000), (3000, 0), (2000, 1000)])  # self-intersecting -> invalid
    rows = [
        {"Tarifzone": "40", "Code": 1, "geometry": box(0, 0, 1000, 1000)},
        {"Tarifzone": "70", "Code": 2, "geometry": box(990, 0, 2000, 1000)},   # 10 m x 1000 m overlap with 40
        {"Tarifzone": "20", "Code": 3, "geometry": bowtie},
    ]
    path = tmp_path / "zones.geojson"
    _write(path, rows)
    return path


def test_load_repairs_invalid_ring_and_reports_it(tmp_path):
    zones, report = zp.load_zone_polygons(_three_zones(tmp_path), expected_feature_count=3)
    assert list(zones.columns) == ["zone_id", "geometry"]
    assert zones.geometry.is_valid.all()
    assert report["invalid_repaired"] == ["20"]
    assert zones.crs.to_epsg() == 25832


def test_load_rejects_wrong_feature_count_and_duplicate_ids(tmp_path):
    path = _three_zones(tmp_path)
    with pytest.raises(ValueError, match="expected 46 features"):
        zp.load_zone_polygons(path, expected_feature_count=46)
    _write(tmp_path / "dup.geojson", [
        {"Tarifzone": "40", "geometry": box(0, 0, 1, 1)}, {"Tarifzone": "40", "geometry": box(2, 2, 3, 3)}])
    with pytest.raises(ValueError, match="duplicate zone id"):
        zp.load_zone_polygons(tmp_path / "dup.geojson", expected_feature_count=2)


def test_overlap_sliver_is_trimmed_to_lower_zone_id(tmp_path):
    zones, _ = zp.load_zone_polygons(_three_zones(tmp_path), expected_feature_count=3)
    resolved, trimmed = zp.resolve_overlaps(zones, minimum_overlap_m2=1.0)
    assert trimmed == [{"kept_zone": "40", "trimmed_zone": "70", "area_m2": 10000.0}]
    by_id = resolved.set_index("zone_id").geometry
    assert by_id["40"].area == pytest.approx(1_000_000.0)
    assert by_id["70"].area == pytest.approx(1_000_000.0)  # 1010 x 1000 minus the 10 x 1000 sliver
    assert by_id["40"].intersection(by_id["70"]).area == pytest.approx(0.0)


def test_station_point_zones_are_built_from_gtfs_stop_names(tmp_path):
    zones, _ = zp.load_zone_polygons(_three_zones(tmp_path), expected_feature_count=3)
    # WGS84 coordinates far east of the synthetic squares (which sit at the EPSG:25832 origin).
    stops = pd.DataFrame([
        {"stop_id": "a", "stop_name": "Haemelerwald", "stop_lat": 52.4, "stop_lon": 10.1},
        {"stop_id": "b", "stop_name": "Haemelerwald, Bahnhof", "stop_lat": 52.4005, "stop_lon": 10.1},
        {"stop_id": "c", "stop_name": "Dedenhausen", "stop_lat": 52.45, "stop_lon": 10.05},
    ])
    points = zp.station_point_zones(stops, {"55": "Haemelerwald", "56": "Dedenhausen"}, 300.0, zones)
    assert sorted(points["zone_id"]) == ["55", "56"]
    assert points.geometry.area.min() == pytest.approx(3.1416 * 300 ** 2, rel=0.01)


def test_station_point_zones_fail_loudly_on_missing_or_scattered_names(tmp_path):
    zones, _ = zp.load_zone_polygons(_three_zones(tmp_path), expected_feature_count=3)
    stops = pd.DataFrame([{"stop_id": "a", "stop_name": "Haemelerwald", "stop_lat": 52.4, "stop_lon": 10.1},
                          {"stop_id": "b", "stop_name": "Haemelerwald Sued", "stop_lat": 52.5, "stop_lon": 10.1}])
    with pytest.raises(ValueError, match="no GTFS stop named like 'Dedenhausen'"):
        zp.station_point_zones(stops, {"56": "Dedenhausen"}, 300.0, zones)
    with pytest.raises(ValueError, match="spread"):
        zp.station_point_zones(stops, {"55": "Haemelerwald"}, 300.0, zones)


class _Context:
    def __init__(self, tmp_path, cleaned_dir, data_path):
        self._path = tmp_path / "stage"
        self._path.mkdir()
        self._cleaned = cleaned_dir
        self.values = {"data_path": str(data_path), "vrb_zone_polygons_path": "zones.geojson",
                       "vrb_zone_polygon_feature_count": 3, "vrb_zone_point_radius_m": 300.0,
                       "vrb_zone_point_stations": {"55": "Haemelerwald", "56": "Dedenhausen"}}
        self.declared = []

    def config(self, key, default=None):
        self.declared.append(key)
        return self.values.get(key, default)

    def stage(self, name):
        self.declared.append(("stage", name))
        return None

    def path(self, name=None):
        return str(self._cleaned if name == "data.gtfs.cleaned" else self._path)


def test_stage_execute_writes_gpkg_report_and_returns_48_style_frame(tmp_path):
    _three_zones(tmp_path)
    cleaned = tmp_path / "cleaned"
    (cleaned / "output").mkdir(parents=True)
    pd.DataFrame([{"stop_id": "a", "stop_name": "Haemelerwald", "stop_lat": 52.4, "stop_lon": 10.1},
                  {"stop_id": "c", "stop_name": "Dedenhausen", "stop_lat": 52.45, "stop_lon": 10.05}]
                 ).to_csv(cleaned / "output" / "stops.txt", index=False)
    context = _Context(tmp_path, cleaned, tmp_path)
    zp.configure(context)
    assert ("stage", "data.gtfs.cleaned") in context.declared
    result = zp.execute(context)
    assert sorted(result["zone_id"]) == ["20", "40", "55", "56", "70"]
    assert (Path(context.path()) / "vrb_tariff_zones.gpkg").is_file()
    report = json.loads((Path(context.path()) / "zone_polygons_report.json").read_text())
    assert report["polygon_count"] == 3 and report["point_zone_count"] == 2
    assert report["trimmed_overlaps"][0]["trimmed_zone"] == "70"
