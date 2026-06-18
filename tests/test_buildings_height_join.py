# tests/test_buildings_height_join.py
import geopandas as gpd, pandas as pd
from shapely.geometry import Point
from braunschweig.data import buildings as B


def _b():
    return gpd.GeoDataFrame(
        {"building_id": [0, 1, 2], "area_m2": [80.0, 1000.0, 50.0],
         "OI": ["DENIALa", "DENIALb", "DENIALc"], "commune_id": ["x"] * 3},
        geometry=[Point(0, 0), Point(1, 1), Point(2, 2)], crs="EPSG:25832")


def test_join_is_non_destructive():
    heights = pd.DataFrame({"OI": ["DENIALb", "DENIALc"], "height_m": [21.0, 6.0], "roofType": ["1000", "1000"]})
    out = B.join_lod2_heights(_b(), heights)
    assert len(out) == 3                                   # no footprint dropped
    assert {"building_id", "area_m2", "OI", "commune_id", "geometry", "height_m"} <= set(out.columns)
    h = out.set_index("OI")["height_m"]
    assert h["DENIALb"] == 21.0 and h["DENIALc"] == 6.0
    assert pd.isna(h["DENIALa"])                           # unmatched -> NaN, kept
