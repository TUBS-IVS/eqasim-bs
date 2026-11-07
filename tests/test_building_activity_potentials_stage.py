"""Loader and commune join of ``braunschweig.data.building_potentials``.

The loader reads the ``buildings`` layer of the redistribution GeoPackage
(``08_redistribution.gpkg``) directly; these tests write a small synthetic file
with the same schema.
"""
import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Polygon, box

from braunschweig.data.building_potentials import (
    POTENTIAL_COLUMNS, assign_commune, load_potentials,
    normalise_bosserhof_class,
)


def _zones():
    big = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])
    return gpd.GeoDataFrame({"commune_id": ["03101000"]},
                            geometry=[big], crs="EPSG:25832")


def _buildings():
    inside = Polygon([(1, 1), (1, 2), (2, 2), (2, 1)])
    outside = Polygon([(20, 20), (20, 21), (21, 21), (21, 20)])
    return gpd.GeoDataFrame(
        {"building_id": [0, 1], "potential_work": [5.0, 9.0]},
        geometry=[inside, outside], crs="EPSG:25832",
    )


def test_assign_commune_primary_and_fallback_counts():
    gdf, primary, fallback = assign_commune(_buildings(), _zones())
    assert primary == 1          # the inside building matched a polygon
    assert fallback == 1         # the outside building took nearest fallback
    assert gdf.loc[gdf["building_id"] == 0, "commune_id"].iloc[0] == "03101000"
    assert gdf["commune_id"].notna().all()


def test_assign_commune_uses_representative_point_for_concave_footprint():
    # A U-shaped (horseshoe) footprint whose centroid (~ (3, 2.375)) lies in the
    # notch, i.e. OUTSIDE the polygon. With a plain centroid join this building
    # would miss the zone and take the nearest-zone fallback; representative_point
    # is guaranteed inside the footprint, so it matches the zone directly.
    horseshoe = Polygon([
        (0, 0), (0, 6), (1, 6), (1, 1), (5, 1), (5, 6), (6, 6), (6, 0),
    ])
    assert not horseshoe.contains(horseshoe.centroid)
    zones = gpd.GeoDataFrame({"commune_id": ["03101000"]},
                             geometry=[horseshoe], crs="EPSG:25832")
    buildings = gpd.GeoDataFrame(
        {"building_id": [0], "potential_work": [5.0]},
        geometry=[horseshoe], crs="EPSG:25832",
    )
    gdf, primary, fallback = assign_commune(buildings, zones)
    assert primary == 1
    assert fallback == 0
    assert gdf.loc[gdf["building_id"] == 0, "commune_id"].iloc[0] == "03101000"


# --- GeoPackage loader (new schema) -----------------------------------------

_SOURCE_VALUE_COLUMNS = [
    "assigned_Workers", "assigned_School", "assigned_University",
    "assigned_Kindergarten", "assigned_Retail_Daily", "assigned_Retail_Non_Daily",
    "assigned_Leisure", "value_business", "value_errands", "value_sports",
    "value_meetup", "value_lessons",
]


def _source_gdf(crs="EPSG:25832"):
    """Three buildings; ids deliberately unsorted and OSM-style ids with a slash."""
    data = {
        "building_id": ["way/12", "DENIAL01000002A1", "DENIAL01000001B2"],
        "zone_no": [3.0, np.nan, 7.0],
        "name": ["a", "b", "c"],
        "label_en": ["x", "y", "z"],
        "bosserhof_class": ["Industrial operations / Production",
                            "retail (small-scale)", "Crafts and Trades"],
        "storeys": [2.0, 1.0, 3.0],
        "floor_area_m2": [100.0, 50.0, 80.0],
        "value_assumed": ["a", "b", "c"],
    }
    for i, col in enumerate(_SOURCE_VALUE_COLUMNS):
        data[col] = [1.0 + i, 2.0 + i, 3.0 + i]
    geoms = [box(0, 0, 1, 1), box(2, 0, 3, 1), box(4, 0, 5, 1)]
    return gpd.GeoDataFrame(data, geometry=geoms, crs=crs)


def _write(gdf, tmp_path, layer="buildings"):
    path = tmp_path / "08_redistribution.gpkg"
    gdf.to_file(path, layer=layer, driver="GPKG")
    return str(path)


def test_load_potentials_maps_schema_and_assigns_deterministic_ids(tmp_path):
    loaded = load_potentials(_write(_source_gdf(), tmp_path))

    # ids: contiguous ints in order of the sorted source id, source id kept
    assert loaded["building_id"].tolist() == [0, 1, 2]
    assert loaded["source_building_id"].tolist() == [
        "DENIAL01000001B2", "DENIAL01000002A1", "way/12"]
    assert loaded.crs.to_epsg() == 25832
    # primary mapping of every potential column (values are 1+i / 2+i / 3+i
    # for way/12 / DENIAL..2A1 / DENIAL..1B2 respectively)
    row = loaded.set_index("source_building_id").loc["way/12"]
    # hand-written expectation: source column index i -> value 1+i for way/12
    expected = {
        "potential_work": 1.0, "potential_school": 2.0, "potential_university": 3.0,
        "potential_kindergarten": 4.0, "potential_retail_daily": 5.0,
        "potential_retail_non_daily": 6.0, "potential_leisure": 7.0,
        "potential_business": 8.0, "potential_errands": 9.0, "potential_sports": 10.0,
        "potential_meetup": 11.0, "potential_lessons": 12.0,
    }
    for internal, value in expected.items():
        assert row[internal] == pytest.approx(value), internal
    # normalised class matches the keys of the bosserhof mapping CSVs
    classes = loaded.set_index("source_building_id")["bosserhof_class_clean"]
    assert classes["way/12"] == "industrial operations production"
    assert classes["DENIAL01000002A1"] == "retail small scale"
    assert classes["DENIAL01000001B2"] == "crafts and trades"
    assert set(POTENTIAL_COLUMNS) <= set(loaded.columns)
    assert "potential_generic" not in loaded.columns


def test_normalise_bosserhof_class():
    out = normalise_bosserhof_class(pd.Series(["Retail (small-scale)", " A  /  B "]))
    assert out.tolist() == ["retail small scale", "a b"]


def _expect_failure(tmp_path, gdf, match, **kwargs):
    with pytest.raises(ValueError, match=match):
        load_potentials(_write(gdf, tmp_path, **kwargs))


def test_load_rejects_missing_source_column(tmp_path):
    _expect_failure(tmp_path, _source_gdf().drop(columns=["value_errands"]),
                    "value_errands")


def test_load_rejects_missing_layer(tmp_path):
    _expect_failure(tmp_path, _source_gdf(), "buildings", layer="pois")


def test_load_rejects_wrong_crs(tmp_path):
    _expect_failure(tmp_path, _source_gdf(crs="EPSG:3857"), "EPSG:25832")


def test_load_rejects_missing_crs(tmp_path):
    gdf = _source_gdf()
    gdf = gdf.set_crs(None, allow_override=True)
    _expect_failure(tmp_path, gdf, "CRS")


def test_load_rejects_nan_potential(tmp_path):
    gdf = _source_gdf()
    gdf.loc[0, "assigned_Workers"] = np.nan
    _expect_failure(tmp_path, gdf, "NaN.*assigned_Workers")


def test_load_rejects_negative_potential(tmp_path):
    gdf = _source_gdf()
    gdf.loc[1, "value_errands"] = -1.0
    _expect_failure(tmp_path, gdf, "negative.*value_errands")


def test_load_rejects_duplicate_source_ids(tmp_path):
    gdf = _source_gdf()
    gdf.loc[1, "building_id"] = "way/12"
    _expect_failure(tmp_path, gdf, "unique")


def test_load_rejects_missing_file(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        load_potentials(str(tmp_path / "absent.gpkg"))
