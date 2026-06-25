import geopandas as gpd
from shapely.geometry import Polygon
from braunschweig.data.building_potentials import (
    assign_commune, REQUIRED_COLUMNS,
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


def test_required_columns_constant_covers_all_potentials():
    assert "potential_work" in REQUIRED_COLUMNS
    assert "potential_generic" in REQUIRED_COLUMNS
