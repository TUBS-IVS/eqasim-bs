"""The parking-lot release rule of scripts/build_parking_lots_from_osm.py (issue #465, ADR-0169).

The release feeds the large-site access step: a lot that is wrongly dropped or gets the wrong number of spaces
shifts the share of a site's car access to the wrong road. The spaces follow the parking module's capacity rule
(braunschweig.parking.supply_share, B2), which this test pins through the release.
"""
import importlib.util
import pathlib

import geopandas as gpd
from shapely.geometry import Point, box

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "build_parking_lots_from_osm.py"
spec = importlib.util.spec_from_file_location("build_parking_lots_from_osm", SCRIPT)
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)


def test_lots_from_objects_keeps_off_street_lots_with_their_spaces():
    objects = gpd.GeoDataFrame({
        "osm_type": ["way", "way", "way", "way", "node", "node", "way"],
        "osm_id": [1, 2, 3, 4, 5, 6, 7],
        "tags": [
            {"amenity": "parking", "parking": "surface", "access": "private", "name": "VW WOB MA P6"},
            {"amenity": "parking", "capacity": "40"},
            {"amenity": "parking", "parking": "multi-storey", "building:levels": "3"},
            {"amenity": "parking", "parking": "street_side"},
            {"amenity": "parking"},
            {"amenity": "parking", "capacity": "12", "access": "customers"},
            {"amenity": "bicycle_parking"},
        ],
    }, geometry=[
        box(0.0, 0.0, 50.0, 50.0),          # 2500 m2 / 25 m2 = 100 spaces (area heuristic)
        box(100.0, 0.0, 110.0, 10.0),       # capacity tag 40 wins over the area
        box(200.0, 0.0, 220.0, 25.0),       # 500 m2 / 25 m2 x 3 levels = 60
        box(300.0, 0.0, 400.0, 5.0),        # street-side parking: not a lot
        Point(500.0, 0.0),                  # a node without capacity has no extent: 0 spaces, dropped
        Point(600.0, 10.0),                 # a node with a capacity tag
        box(700.0, 0.0, 750.0, 50.0),       # not amenity=parking
    ], crs="EPSG:25832")

    lots = build.lots_from_objects(objects)

    assert list(lots.columns) == ["lot_id", "x", "y", "spaces", "capacity_source", "access", "parking", "name"]
    assert lots["lot_id"].tolist() == ["node/6", "way/1", "way/2", "way/3"]
    assert lots["spaces"].tolist() == [12, 100, 40, 60]
    assert lots["capacity_source"].tolist() == ["tag", "area", "tag", "area_levels"]
    assert lots.set_index("lot_id").loc["way/1", ["x", "y"]].tolist() == [25.0, 25.0]
    assert lots.set_index("lot_id").loc["node/6", ["x", "y"]].tolist() == [600.0, 10.0]
    assert lots.set_index("lot_id").loc["way/1", "access"] == "private"
    assert lots.set_index("lot_id").loc["way/1", "name"] == "VW WOB MA P6"
    assert lots.set_index("lot_id").loc["way/2", "access"] == ""
    assert str(lots["spaces"].dtype).startswith("int")


def test_the_extract_must_be_the_pinned_one():
    pinned = build.PINNED_EXTRACT_SHA256
    build.check_extract_sha256({"file": "x.osm.pbf", "sha256": pinned}, pinned)
    try:
        build.check_extract_sha256({"file": "x.osm.pbf", "sha256": "0" * 64}, pinned)
    except SystemExit as error:
        assert "x.osm.pbf" in str(error)
    else:
        raise AssertionError("a different extract must stop the curation")
