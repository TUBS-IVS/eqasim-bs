import geopandas as gpd
import pandas as pd
from shapely.geometry import Point
from braunschweig.calibration.distance_fit import distances as D


def _frames():
    act = pd.DataFrame({
        "person_id": [1, 1, 1, 1],
        "activity_index": [0, 1, 2, 3],
        "purpose": ["home", "work", "shop", "home"],
        "is_first": [True, False, False, False],
    })
    loc = gpd.GeoDataFrame({
        "person_id": [1, 1, 1, 1],
        "activity_index": [0, 1, 2, 3],
        "commune_id": ["03101000", "03101000", "03101000", "03101000"],
        "geometry": [Point(0, 0), Point(3000, 0), Point(3000, 1000), Point(0, 0)],
    }, geometry="geometry", crs="EPSG:25832")
    return act, loc


def test_work_distance_is_home_to_work_with_detour():
    act, loc = _frames()
    out = D.realised_distances(act, loc, activity="work", detour_factor=1.3)
    assert len(out) == 1
    assert abs(out.iloc[0]["distance_km"] - 3.9) < 1e-9
    assert out.iloc[0]["home_commune_id"] == "03101000"


def test_secondary_distance_is_leg_from_preceding_activity():
    act, loc = _frames()
    out = D.realised_distances(act, loc, activity="secondary", detour_factor=1.0)
    row = out[out.purpose == "shop"].iloc[0]
    assert abs(row["distance_km"] - 1.0) < 1e-9


def test_rs7_lookup_fills_home_rs7():
    act, loc = _frames()
    out = D.realised_distances(act, loc, activity="work", rs7_lookup={"03101000": 72})
    assert int(out.iloc[0]["home_rs7"]) == 72
