"""Tests for the cordon extent GeoPackage writer (input to RunScenarioCutter).

eqasim's RunScenarioCutter requires a single-polygon extent without holes
(docs/cutting.md). This writer produces exactly that from the cordon polygon.
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
from shapely.geometry import MultiPolygon, Point, Polygon, box

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon.extent import (  # noqa: E402
    single_polygon_without_holes,
    write_cordon_extent,
)


def test_single_polygon_drops_holes():
    outer = box(0, 0, 100, 100)
    hole = box(40, 40, 60, 60)
    holed = Polygon(outer.exterior.coords, [list(hole.exterior.coords)])
    assert holed.contains(Point(10, 10)) and not holed.contains(Point(50, 50))
    filled = single_polygon_without_holes(holed)
    assert len(filled.interiors) == 0
    assert filled.contains(Point(50, 50))   # hole is filled


def test_single_polygon_picks_largest_of_multipolygon():
    big = box(0, 0, 100, 100)
    small = box(200, 200, 210, 210)
    mp = MultiPolygon([big, small])
    out = single_polygon_without_holes(mp)
    assert out.geom_type == "Polygon"
    assert out.contains(Point(50, 50)) and not out.contains(Point(205, 205))


def test_write_cordon_extent_single_feature_gpkg(tmp_path):
    poly = box(600000, 5790000, 620000, 5800000)
    path = tmp_path / "cordon_extent.gpkg"
    out = write_cordon_extent(str(path), poly, crs="EPSG:25832")
    assert Path(out).exists()
    gdf = gpd.read_file(out)
    assert len(gdf) == 1                       # single polygon feature (cutter req.)
    assert gdf.geometry.iloc[0].geom_type == "Polygon"
    assert len(gdf.geometry.iloc[0].interiors) == 0
    assert str(gdf.crs).endswith("25832")
