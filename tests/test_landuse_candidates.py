"""Unit tests for deterministic landuse grid seeding (issue #262)."""
import geopandas as gpd
import pytest
from shapely.geometry import Polygon, box

from braunschweig.synthesis.locations.landuse_candidates import grid_seed_polygons


def _gdf(geoms, layer="ln_freiluftundnaherholung"):
    return gpd.GeoDataFrame({"layer": [layer] * len(geoms), "area_m2": [g.area for g in geoms]},
                            geometry=geoms, crs="EPSG:25832")


def test_large_polygon_gets_area_proportional_points():
    # 450m x 450m box positioned to contain the 9 nodes at (150,150)..(450,450)? --
    # use box(100, 100, 560, 560) with spacing 150: nodes at x,y in {150,300,450} -> 9 points
    out = grid_seed_polygons(_gdf([box(100, 100, 560, 560)]), spacing_m=150.0)
    assert len(out) == 9
    assert (out.represented_area_m2 == 150.0 ** 2).all()


def test_fragmentation_invariance():
    # the same square, whole vs split in two halves, yields identical point sets
    whole = grid_seed_polygons(_gdf([box(100, 100, 560, 560)]), spacing_m=150.0)
    split = grid_seed_polygons(
        _gdf([box(100, 100, 330, 560), box(330, 100, 560, 560)]), spacing_m=150.0)
    assert sorted(zip(whole.geometry.x, whole.geometry.y)) == sorted(zip(split.geometry.x, split.geometry.y))


def test_small_polygon_gets_representative_point_with_own_area():
    small = box(10, 10, 60, 60)  # 50m x 50m, catches no 150m node
    out = grid_seed_polygons(_gdf([small]), spacing_m=150.0)
    assert len(out) == 1
    assert out.represented_area_m2.iloc[0] == pytest.approx(small.area)
    assert small.contains(out.geometry.iloc[0])


def test_determinism():
    gdf = _gdf([box(100, 100, 560, 560), box(1000, 1000, 1100, 1100)])
    a = grid_seed_polygons(gdf, spacing_m=150.0)
    b = grid_seed_polygons(gdf, spacing_m=150.0)
    assert a.equals(b)
