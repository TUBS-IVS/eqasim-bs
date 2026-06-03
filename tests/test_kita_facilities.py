import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from braunschweig.data.schools.kita_facilities import lsn_unit, build_kita_facilities


def test_lsn_unit_from_ars():
    assert lsn_unit("031510009009") == "151009"
    assert lsn_unit("031515401004") == "151401"
    assert lsn_unit("031010000000") == "101000"   # kreisfrei -> 6-digit form


def _kitas():
    return pd.DataFrame({
        "lsn_code": ["101", "151009"],   # kreisfrei BS (3-digit) + Gifhorn Stadt (6-digit)
        "name": ["BS", "Gifhorn Stadt"],
        "einrichtungen": [211.0, 30.0],
        "plaetze": [13112.0, 2240.0],
    })


def _osm_kg():
    return gpd.GeoDataFrame({
        "commune_id": ["031010000000", "031010000000", "031510009009"],
        "weight": [300.0, 100.0, 50.0],
        "geometry": [Point(605000, 5790000), Point(606000, 5790000),
                     Point(610000, 5860000)],
    }, crs="EPSG:25832")


def test_kreisfrei_fallback_and_area_distribution():
    gdf = build_kita_facilities(_kitas(), _osm_kg())
    assert list(gdf.columns) == ["location_id", "capacity", "geometry"]
    assert gdf.crs.to_string() == "EPSG:25832"
    bs = gdf[gdf.geometry.y == 5790000].sort_values("capacity", ascending=False)
    assert abs(bs.iloc[0]["capacity"] - 13112 * 0.75) < 1.0
    assert abs(bs.iloc[1]["capacity"] - 13112 * 0.25) < 1.0
    gf = gdf[gdf.geometry.y == 5860000]
    assert abs(gf.iloc[0]["capacity"] - 2240.0) < 1.0


def test_capacities_sum_to_total_plaetze():
    gdf = build_kita_facilities(_kitas(), _osm_kg())
    assert abs(gdf["capacity"].sum() - (13112 + 2240)) < 1.0
