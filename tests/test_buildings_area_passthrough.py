import geopandas as gpd, pandas as pd
from shapely.geometry import Polygon
from braunschweig.data import buildings as B


def _alkis():
    # one 100 m² house, one 800 m² block (would be dropped by the old 400 cap)
    polys = [Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
             Polygon([(0, 0), (40, 0), (40, 20), (0, 20)])]
    return gpd.GeoDataFrame({"AGS": ["03101000", "03101000"], "GFK": ["31001_1010", "31001_1010"],
                             "activity": ["residential", "residential"],
                             "area_m2": [100.0, 800.0]}, geometry=polys, crs="EPSG:25832")


def test_large_buildings_kept_and_area_carried():
    out = B.filter_residential_buildings(_alkis())   # pure helper extracted from execute()
    assert set(out["area_m2"]) == {100.0, 800.0}     # 800 m² block NOT dropped
    assert "area_m2" in out.columns
