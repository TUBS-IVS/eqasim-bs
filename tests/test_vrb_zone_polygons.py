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


def test_load_rejects_a_layer_whose_zone_ids_differ_from_the_expected_set(tmp_path):
    path = _three_zones(tmp_path)  # zones 20, 40, 70
    zones, _ = zp.load_zone_polygons(path, expected_feature_count=3, expected_zone_ids={"20", "40", "70"})
    assert sorted(zones["zone_id"]) == ["20", "40", "70"]
    # Same count, no duplicate: zone 71 is missing and zone 70 replaced it.
    with pytest.raises(ValueError, match=r"missing \['71'\], unexpected \['70'\]"):
        zp.load_zone_polygons(path, expected_feature_count=3, expected_zone_ids={"20", "40", "71"})


def test_overlap_sliver_is_trimmed_to_lower_zone_id(tmp_path):
    zones, _ = zp.load_zone_polygons(_three_zones(tmp_path), expected_feature_count=3)
    resolved, trimmed = zp.resolve_overlaps(zones, minimum_overlap_m2=1.0)
    assert trimmed == [{"kept_zone": "40", "trimmed_zone": "70", "area_m2": 10000.0}]
    by_id = resolved.set_index("zone_id").geometry
    assert by_id["40"].area == pytest.approx(1_000_000.0)
    assert by_id["70"].area == pytest.approx(1_000_000.0)  # 1010 x 1000 minus the 10 x 1000 sliver
    assert by_id["40"].intersection(by_id["70"]).area == pytest.approx(0.0)


# A station complex as the cleaned DELFI feed writes it (see the stop rows of "Hämelerwald Bahnhof"):
# the parent station (location_type "1.0", parent "nan"), the rail platform stop and the forecourt bus
# stop as its children (parent ids written as floats, "402454.0"), and village bus stops about 800 m away
# that also carry the place name. WGS84 coordinates, far from the synthetic squares at the origin.
GTFS_STOPS = [
    {"stop_name": "Hämelerwald Bahnhof", "parent_station": "nan", "stop_id": "402454",
     "stop_lat": 52.3480, "stop_lon": 10.1100, "location_type": "1.0"},
    {"stop_name": "Hämelerwald", "parent_station": "402454.0", "stop_id": "273565",
     "stop_lat": 52.3481, "stop_lon": 10.1097, "location_type": ""},
    {"stop_name": "Hämelerwald Bahnhof", "parent_station": "402454.0", "stop_id": "184038",
     "stop_lat": 52.3478, "stop_lon": 10.1099, "location_type": ""},
    {"stop_name": "Hämelerwald Försterstraße", "parent_station": "4402.0", "stop_id": "466850",
     "stop_lat": 52.3408, "stop_lon": 10.1130, "location_type": ""},
    {"stop_name": "Dedenhausen", "parent_station": "486545.0", "stop_id": "547191",
     "stop_lat": 52.4322, "stop_lon": 10.2387, "location_type": ""},
    {"stop_name": "Dedenhausen Eltzer Straße", "parent_station": "73671.0", "stop_id": "653480",
     "stop_lat": 52.4390, "stop_lon": 10.2370, "location_type": ""},
]
RAIL_STOPS = {"273565", "547191"}
STATIONS = {"55": "Hämelerwald", "56": "Dedenhausen"}


def _stops():
    return pd.DataFrame(GTFS_STOPS, dtype=str)


def test_point_zone_is_the_rail_station_complex_not_the_village(tmp_path):
    zones, _ = zp.load_zone_polygons(_three_zones(tmp_path), expected_feature_count=3)
    points = zp.station_point_zones(_stops(), RAIL_STOPS, STATIONS, radius_m=50.0, maximum_spread_m=300.0,
                                    existing=zones).set_index("zone_id")
    assert points.loc["55", "rail_stop_ids"] == ["273565"]
    # The forecourt bus stop shares the parent station; the village stop 800 m away does not belong to it.
    assert points.loc["55", "station_stop_ids"] == ["184038", "273565"]
    assert points.loc["56", "station_stop_ids"] == ["547191"]
    stop_points = gpd.GeoSeries(gpd.points_from_xy(_stops().stop_lon.astype(float), _stops().stop_lat.astype(float)),
                                index=_stops().stop_id, crs="EPSG:4326").to_crs(CRS)
    assert points.loc["55", "geometry"].contains(stop_points["273565"])
    assert points.loc["55", "geometry"].contains(stop_points["184038"])
    assert not points.loc["55", "geometry"].contains(stop_points["466850"])
    assert not points.loc["56", "geometry"].contains(stop_points["653480"])


def test_point_zone_needs_a_rail_served_stop_with_the_station_name(tmp_path):
    zones, _ = zp.load_zone_polygons(_three_zones(tmp_path), expected_feature_count=3)
    with pytest.raises(ValueError, match="no rail-served GTFS stop named like 'Dedenhausen'"):
        zp.station_point_zones(_stops(), {"273565"}, STATIONS, radius_m=50.0, maximum_spread_m=300.0, existing=zones)


def test_station_complex_wider_than_the_spread_limit_is_rejected(tmp_path):
    zones, _ = zp.load_zone_polygons(_three_zones(tmp_path), expected_feature_count=3)
    with pytest.raises(ValueError, match="spread"):
        zp.station_point_zones(_stops(), RAIL_STOPS, STATIONS, radius_m=50.0, maximum_spread_m=10.0, existing=zones)


def test_rail_served_stops_come_from_rail_route_types(tmp_path):
    gtfs = tmp_path / "gtfs"
    gtfs.mkdir()
    pd.DataFrame([{"route_id": "re", "route_type": 2}, {"route_id": "bus", "route_type": 3},
                  {"route_id": "ext", "route_type": 106}]).to_csv(gtfs / "routes.txt", index=False)
    pd.DataFrame([{"route_id": "re", "trip_id": "t1"}, {"route_id": "bus", "trip_id": "t2"},
                  {"route_id": "ext", "trip_id": "t3"}]).to_csv(gtfs / "trips.txt", index=False)
    pd.DataFrame([{"trip_id": "t1", "stop_id": "273565"}, {"trip_id": "t2", "stop_id": "184038"},
                  {"trip_id": "t3", "stop_id": "547191"}, {"trip_id": "t2", "stop_id": "466850"}]
                 ).to_csv(gtfs / "stop_times.txt", index=False)
    candidates = {"273565", "184038", "547191", "466850"}
    assert zp.rail_served_stop_ids(gtfs, candidates, chunk_rows=2) == {"273565", "547191"}


class _Context:
    def __init__(self, tmp_path, cleaned_dir, data_path):
        self._path = tmp_path / "stage"
        self._path.mkdir()
        self._cleaned = cleaned_dir
        self.values = {"data_path": str(data_path), "vrb_zone_polygons_path": "zones.geojson",
                       "vrb_zone_polygon_feature_count": 3, "vrb_zone_point_radius_m": 50.0,
                       "vrb_zone_point_maximum_station_spread_m": 300.0, "vrb_zone_minimum_overlap_m2": 1.0,
                       "vrb_zone_point_stations": STATIONS}
        self.declared = []

    def config(self, key, default=None):
        self.declared.append(key)
        return self.values.get(key, default)

    def stage(self, name):
        self.declared.append(("stage", name))
        return None

    def path(self, name=None):
        return str(self._cleaned if name == "data.gtfs.cleaned" else self._path)


def _matrix_zones(monkeypatch, zones):
    """Stand-in for the committed price-stage matrix: only its zone list matters to this stage."""
    monkeypatch.setattr(zp.fare_model_export, "load_price_stage_matrix", lambda path=None: (list(zones), {}))


def test_stage_execute_writes_gpkg_report_and_returns_48_style_frame(tmp_path, monkeypatch):
    _matrix_zones(monkeypatch, ["20", "40", "55", "56", "70"])
    _three_zones(tmp_path)
    cleaned = tmp_path / "cleaned"
    (cleaned / "output").mkdir(parents=True)
    _stops().to_csv(cleaned / "output" / "stops.txt", index=False)
    pd.DataFrame([{"route_id": "re", "route_type": 2}]).to_csv(cleaned / "output" / "routes.txt", index=False)
    pd.DataFrame([{"route_id": "re", "trip_id": "t1"}]).to_csv(cleaned / "output" / "trips.txt", index=False)
    pd.DataFrame([{"trip_id": "t1", "stop_id": stop} for stop in sorted(RAIL_STOPS)]).to_csv(
        cleaned / "output" / "stop_times.txt", index=False)
    context = _Context(tmp_path, cleaned, tmp_path)
    zp.configure(context)
    assert ("stage", "data.gtfs.cleaned") in context.declared
    result = zp.execute(context)
    assert sorted(result["zone_id"]) == ["20", "40", "55", "56", "70"]
    assert (Path(context.path()) / "vrb_tariff_zones.gpkg").is_file()
    report = json.loads((Path(context.path()) / "zone_polygons_report.json").read_text())
    assert report["polygon_count"] == 3 and report["point_zone_count"] == 2
    assert report["trimmed_overlaps"][0]["trimmed_zone"] == "70"
    assert report["point_zones"]["55"] == {"rail_stop_ids": ["273565"], "station_stop_ids": ["184038", "273565"],
                                           "spread_m": report["point_zones"]["55"]["spread_m"]}
    assert report["point_zones"]["55"]["spread_m"] < 50.0


def test_stage_execute_requires_the_matrix_zones_minus_the_point_zones(tmp_path, monkeypatch):
    # The fare model knows zone 71, the layer carries 70 instead: the stage must fail before writing anything.
    _matrix_zones(monkeypatch, ["20", "40", "55", "56", "71"])
    _three_zones(tmp_path)
    cleaned = tmp_path / "cleaned"
    (cleaned / "output").mkdir(parents=True)
    context = _Context(tmp_path, cleaned, tmp_path)
    with pytest.raises(ValueError, match=r"missing \['71'\], unexpected \['70'\]"):
        zp.execute(context)
    assert not (Path(context.path()) / "vrb_tariff_zones.gpkg").exists()


def test_validate_token_changes_when_the_price_stage_matrix_changes(tmp_path, monkeypatch):
    _three_zones(tmp_path)
    matrix = tmp_path / "matrix.csv"
    header = "origin_zone,destination_zone,status,price_stage"
    matrix.write_text("\n".join([header, "40,40,defined,1", ""]), encoding="utf-8")
    prices, _, rail = zp.fare_model_export.committed_input_paths()
    monkeypatch.setattr(zp.fare_model_export, "committed_input_paths", lambda: (prices, matrix, rail))
    context = _Context(tmp_path, tmp_path, tmp_path)
    before = zp.validate(context)
    matrix.write_text("\n".join([header, "40,71,defined,1", ""]), encoding="utf-8")
    assert zp.validate(context) != before
