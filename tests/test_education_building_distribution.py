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


# --- same-unit rule: potentials only if EVERY POI of a unit matched (> 0) -----
NAN = float("nan")


def test_kita_unit_with_all_pois_matched_uses_potentials(capsys):
    gdf = build_kita_facilities(_kitas(), _osm_kg([1.0, 3.0], [9.0, 1.0]),
                                weight_column="potential",
                                area_weight_column="weight")
    assert [round(c) for c in gdf["capacity"]] == [90, 10]
    assert "units on potentials 1/1 (100.0%)" in capsys.readouterr().out


def test_kita_unit_with_an_unmatched_poi_uses_area_weights_for_all(capsys):
    # Mixing 9 places with 3 m2 x floors would give 75 / 25; the unit must use
    # the area weights of BOTH POIs instead (1:3).
    gdf = build_kita_facilities(_kitas(), _osm_kg([1.0, 3.0], [9.0, NAN]),
                                weight_column="potential",
                                area_weight_column="weight")
    assert [round(c) for c in gdf["capacity"]] == [25, 75]
    out = capsys.readouterr().out
    assert "units on area x floors weights 1 (100.0%)" in out
    assert "WARNING" in out  # share above the module threshold


def test_kita_zero_potential_poi_counts_as_unmatched():
    gdf = build_kita_facilities(_kitas(), _osm_kg([1.0, 3.0], [9.0, 0.0]),
                                weight_column="potential",
                                area_weight_column="weight")
    assert [round(c) for c in gdf["capacity"]] == [25, 75]


def test_university_unit_with_an_unmatched_building_uses_area_weights(capsys):
    gdf = build_university_facilities(_hochschulen(),
                                      _osm_uni([1.0, 3.0], [9.0, NAN]),
                                      weight_column="potential",
                                      area_weight_column="weight")
    assert sorted(round(c) for c in gdf["capacity"]) == [25, 75]
    assert "units on area x floors weights 1 (100.0%)" in capsys.readouterr().out


def test_university_unit_with_all_buildings_matched_uses_potentials(capsys):
    gdf = build_university_facilities(_hochschulen(),
                                      _osm_uni([1.0, 3.0], [9.0, 1.0]),
                                      weight_column="potential",
                                      area_weight_column="weight")
    assert sorted(round(c) for c in gdf["capacity"]) == [10, 90]
    out = capsys.readouterr().out
    assert "units on potentials 1/1 (100.0%)" in out and "WARNING" not in out
