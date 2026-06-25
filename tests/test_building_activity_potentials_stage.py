import geopandas as gpd
import pytest
from shapely.geometry import Polygon
from braunschweig.data.building_potentials import (
    assign_commune, load_potentials, REQUIRED_COLUMNS,
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


def _valid_potentials_gdf():
    poly = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    data = {"building_id": [0]}
    for col in REQUIRED_COLUMNS:
        if col != "building_id":
            data[col] = [1.0]
    return gpd.GeoDataFrame(data, geometry=[poly], crs="EPSG:25832")


def test_load_potentials_rejects_negative_values(tmp_path):
    gdf = _valid_potentials_gdf()
    gdf["potential_work"] = [-1.0]
    path = tmp_path / "neg.parquet"
    gdf.to_parquet(path)
    with pytest.raises(ValueError, match="negative values in potential_work"):
        load_potentials(str(path))


def test_load_potentials_rejects_empty_geometry(tmp_path):
    gdf = _valid_potentials_gdf()
    gdf["geometry"] = [Polygon()]
    path = tmp_path / "empty.parquet"
    gdf.to_parquet(path)
    with pytest.raises(ValueError, match="empty/missing geometry"):
        load_potentials(str(path))


def test_load_potentials_accepts_valid_input(tmp_path):
    gdf = _valid_potentials_gdf()
    path = tmp_path / "ok.parquet"
    gdf.to_parquet(path)
    loaded = load_potentials(str(path))
    assert len(loaded) == 1
    assert loaded.crs.to_epsg() == 25832


def test_required_columns_constant_covers_all_potentials():
    assert "potential_work" in REQUIRED_COLUMNS
    assert "potential_generic" in REQUIRED_COLUMNS
