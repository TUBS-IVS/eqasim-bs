import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from braunschweig.data.schools.university_facilities import build_university_facilities


def _hochschulen():
    return pd.DataFrame({
        "institution": ["TU BS", "HBK BS", "Ostfalia", "Uni Goettingen"],
        "scope": ["local", "local", "local", "surrounding"],
        "ars5": ["03101", "03101", "03158", ""],
        "enrollment": [14776.0, 851.0, 9373.0, 25513.0],
        "lon": [np.nan, np.nan, np.nan, 9.9358],
        "lat": [np.nan, np.nan, np.nan, 51.5413],
    })


def _osm_uni():
    # OSM university buildings: two in 03101 (areas 300/100), one in 03158 (area 50)
    return gpd.GeoDataFrame({
        "commune_id": ["031010000000", "031010000000", "031580000000"],
        "weight": [300.0, 100.0, 50.0],
        "geometry": [Point(605000, 5790000), Point(606000, 5790000),
                     Point(612000, 5780000)],
    }, crs="EPSG:25832")


def test_local_enrollment_distributed_by_area_per_commune():
    gdf = build_university_facilities(_hochschulen(), _osm_uni())
    assert list(gdf.columns) == ["location_id", "capacity", "geometry"]
    assert gdf.crs.to_string() == "EPSG:25832"
    # 03101 pooled enrollment = 14776 + 851 = 15627, split 300:100 -> 3:1
    bs = gdf[gdf.geometry.y == 5790000].sort_values("capacity", ascending=False)
    assert abs(bs.iloc[0]["capacity"] - 15627 * 0.75) < 1.0
    assert abs(bs.iloc[1]["capacity"] - 15627 * 0.25) < 1.0
    # Ostfalia 9373 -> the single 03158 building gets all of it
    wf = gdf[gdf.geometry.y == 5780000]
    assert abs(wf.iloc[0]["capacity"] - 9373.0) < 1.0


def test_surrounding_is_single_point_with_enrollment():
    gdf = build_university_facilities(_hochschulen(), _osm_uni())
    goe = gdf[abs(gdf["capacity"] - 25513.0) < 1.0]
    assert len(goe) == 1


def test_capacities_sum_to_total_enrollment():
    gdf = build_university_facilities(_hochschulen(), _osm_uni())
    assert abs(gdf["capacity"].sum() - (14776 + 851 + 9373 + 25513)) < 1.0
