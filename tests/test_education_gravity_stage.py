import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from braunschweig.synthesis.locations.education_gravity import (
    age_to_level, assign_education_locations, slope_vector_for_level,
)


def test_age_to_level_bands():
    assert age_to_level(4) == "kindergarten"
    assert age_to_level(6) == "grundschule"
    assert age_to_level(9) == "grundschule"
    assert age_to_level(10) == "sekundar_1"
    assert age_to_level(15) == "sekundar_1"
    assert age_to_level(16) == "sekundar_2"
    assert age_to_level(19) == "sekundar_2"
    assert age_to_level(25) == "university"


def _persons():
    ages = [4, 7, 12, 17, 30]
    return gpd.GeoDataFrame({
        "person_id": [1, 2, 3, 4, 5],
        "age": ages,
        "home_rs7": [72] * 5,
        "geometry": [Point(0.0, 0.0)] * 5,
    }, crs="EPSG:25832")


def _nds_schools():
    return gpd.GeoDataFrame({
        "school_id": ["g1", "s1", "z1"],
        "level": ["grundschule", "sekundar_1", "sekundar_2"],
        "capacity": [100.0, 100.0, 100.0],
        "commune_id": ["03101000"] * 3,
        "geometry": [Point(500.0, 0.0), Point(800.0, 0.0), Point(1000.0, 0.0)],
    }, crs="EPSG:25832")


def _osm_education():
    return gpd.GeoDataFrame({
        "location_id": ["edu_k", "edu_u"],
        "education_type": ["kindergarten", "university"],
        "weight": [10.0, 10.0],
        "commune_id": ["03101000", "03101000"],
        "geometry": [Point(300.0, 0.0), Point(2000.0, 0.0)],
    }, crs="EPSG:25832")


def test_assign_education_locations_covers_all_persons():
    cfg = {
        "slope_by_level": {"grundschule": -0.3, "sekundar_1": -0.15, "sekundar_2": -0.08},
        "slope_by_level_rs7": None,
        "max_radius_km_by_level": {"grundschule": 15.0, "sekundar_1": 30.0, "sekundar_2": 60.0},
        "kindergarten_radius_m": 2000.0, "university_radius_m": 10000.0,
        "max_iterations": 50, "tolerance": 1e-6,
    }
    out = assign_education_locations(
        _persons(), _nds_schools(), _osm_education(), cfg,
        rng=np.random.RandomState(0),
    )
    assert list(out.columns) == ["person_id", "commune_id", "location_id", "geometry"]
    assert set(out["person_id"]) == {1, 2, 3, 4, 5}
    assert len(out) == 5
    by_pid = out.set_index("person_id")["location_id"].to_dict()
    assert by_pid[2] == "g1"
    assert by_pid[3] == "s1"
    assert by_pid[4] == "z1"
    assert by_pid[1] == "edu_k"
    assert by_pid[5] == "edu_u"


def test_slope_vector_for_level_uses_rs7_override_then_falls_back():
    rs7 = pd.Series([72, 74, 99])   # 99 is an RS7 with no override
    by_level_rs7 = {"grundschule": {72: -0.5, 74: -0.2}}
    scalar_by_level = {"grundschule": -0.3}
    out = slope_vector_for_level("grundschule", rs7, by_level_rs7, scalar_by_level)
    assert list(out) == [-0.5, -0.2, -0.3]   # 99 falls back to the scalar


def test_slope_vector_for_level_scalar_when_no_rs7_dict():
    rs7 = pd.Series([72, 74])
    out = slope_vector_for_level("grundschule", rs7, None, {"grundschule": -0.3})
    assert list(out) == [-0.3, -0.3]
