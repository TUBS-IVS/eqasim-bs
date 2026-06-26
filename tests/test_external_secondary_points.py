import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from braunschweig.data.external_secondary_points import build_external_secondary_points


def _square(x, y, s=10.0):
    return Polygon([(x, y), (x + s, y), (x + s, y + s), (x, y + s)])


def _gemeinden():
    # vg250_gem geometry is POLYGONS (this is what _load_gemeinden returns on real
    # data) -- the fixture must mirror that so the test exercises the polygon->point
    # conversion. Two ZGB Gemeinden (ars5 03101) + two external (05111, 03241).
    return gpd.GeoDataFrame({
        "ars5": ["03101", "03101", "05111", "03241"],
        "gem_ags": ["03101000", "03101001", "05111000", "03241001"],
        "ewz": [250000.0, 5000.0, 600000.0, 40000.0],
        "geometry": [_square(0, 0), _square(20, 0), _square(40, 0), _square(60, 0)],
    }, crs="EPSG:25832")


def test_external_points_exclude_zgb_and_build_ext_ids():
    zgb = ["03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158"]
    out = build_external_secondary_points(_gemeinden(), zgb)

    # ZGB Gemeinden (ars5 03101) dropped; only the two external rows remain.
    assert set(out["ars5"]) == {"05111", "03241"}
    assert set(out["commune_id"]) == {"EXT05111000", "EXT03241001"}
    assert (out["ewz"] > 0).all()
    assert str(out.crs) == "EPSG:25832"
    assert len(out) == 2
    # Geometry MUST be points (carla's discretization reads geometry.x/.y; a polygon
    # there raises "x attribute access only provided for Point geometries").
    assert (out.geometry.geom_type == "Point").all()
