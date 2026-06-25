"""Tests for weight_column parameter in kita/university facility builders.

These tests verify that build_kita_facilities and build_university_facilities
correctly switch from footprint-area distribution (weight_column="weight") to
building-potential distribution (weight_column="potential") when requested.
The surrounding-institution branch of build_university_facilities is not tested
here because it is unchanged by this feature.
"""
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from braunschweig.data.schools.kita_facilities import build_kita_facilities
from braunschweig.data.schools.university_facilities import build_university_facilities


def _kitas():
    return pd.DataFrame({"lsn_code": ["101"], "name": ["X"], "plaetze": [100.0]})


def _osm_kg(weights, potentials):
    # commune_id 12-digit; lsn_unit() -> s[2:5]+s[6:9] = "101"+"000" = "101000";
    # to land in unit "101" (kreis3 fallback), use a code whose [2:5] == "101".
    return gpd.GeoDataFrame(
        {"commune_id": ["031010000000", "031010000000"],
         "weight": weights, "potential": potentials},
        geometry=[Point(0, 0), Point(1, 1)], crs="EPSG:25832")


def test_kita_area_weighting_splits_by_area():
    gdf = build_kita_facilities(_kitas(), _osm_kg([1.0, 3.0], [9.0, 1.0]),
                                weight_column="weight")
    assert round(gdf["capacity"].iloc[0]) == 25   # 100 * 1/4
    assert round(gdf["capacity"].iloc[1]) == 75   # 100 * 3/4


def test_kita_potential_weighting_splits_by_potential():
    gdf = build_kita_facilities(_kitas(), _osm_kg([1.0, 3.0], [9.0, 1.0]),
                                weight_column="potential")
    assert round(gdf["capacity"].iloc[0]) == 90   # 100 * 9/10, independent of area
    assert round(gdf["capacity"].iloc[1]) == 10


def _hochschulen():
    return pd.DataFrame({
        "institution": ["TU X"], "scope": ["local"], "ars5": ["03101"],
        "enrollment": [100.0], "lon": [None], "lat": [None],
    })


def _osm_uni(weights, potentials):
    return gpd.GeoDataFrame(
        {"commune_id": ["031010000000", "031010000000"],
         "weight": weights, "potential": potentials},
        geometry=[Point(0, 0), Point(1, 1)], crs="EPSG:25832")


def test_university_potential_weighting_splits_by_potential():
    gdf = build_university_facilities(_hochschulen(),
                                      _osm_uni([1.0, 3.0], [9.0, 1.0]),
                                      weight_column="potential")
    caps = sorted(round(c) for c in gdf["capacity"])
    assert caps == [10, 90]   # 100 split 9:1 by potential
