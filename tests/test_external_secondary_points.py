import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

from braunschweig.data.external_secondary_points import build_external_secondary_points


def _gemeinden():
    # Two ZGB Gemeinden (ars5 03101) + two external (05111, 03241).
    return gpd.GeoDataFrame({
        "ars5": ["03101", "03101", "05111", "03241"],
        "gem_ags": ["03101000", "03101001", "05111000", "03241001"],
        "ewz": [250000.0, 5000.0, 600000.0, 40000.0],
        "geometry": [Point(0, 0), Point(1, 1), Point(2, 2), Point(3, 3)],
    }, crs="EPSG:25832")


def test_external_points_exclude_zgb_and_build_ext_ids():
    zgb = ["03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158"]
    out = build_external_secondary_points(_gemeinden(), zgb)

    # ZGB Gemeinden (ars5 03101) dropped; only the two external rows remain.
    assert set(out["ars5"]) == {"05111", "03241"}
    assert set(out["commune_id"]) == {"EXT05111000", "EXT03241001"}
    assert (out["ewz"] > 0).all()
    # geometry preserved (representative points), CRS preserved.
    assert str(out.crs) == "EPSG:25832"
    assert len(out) == 2
