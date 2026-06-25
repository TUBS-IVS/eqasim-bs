"""Tests for ``scripts/import_building_activity_potentials.py``.

Covers the pure ``rename_columns`` transformation and its idempotency on values.
The CLI / file-IO path is exercised by the manual Step 5 real-data run.
"""
import geopandas as gpd
from shapely.geometry import Polygon
from scripts.import_building_activity_potentials import (
    rename_columns, COLUMN_RENAME, POTENTIAL_COLUMNS,
)


def _source_gdf():
    poly = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    return gpd.GeoDataFrame(
        {
            "building_index": [0, 1],
            "gml_id": ["13", "14"],
            "assigned_Workers": [10.0, 0.0],
            "assigned_School": [0.0, 5.0],
            "assigned_University": [0.0, 0.0],
            "assigned_Kindergarten": [0.0, 0.0],
            "assigned_Leisure": [3.0, 0.0],
            "assigned_Retail_Daily": [7.0, 0.0],
            "assigned_Retail_Non-Daily": [1.0, 0.0],
            "potentials": [100.0, 50.0],
            "bosserhof_class_clean": ["normal office", "schools"],
            "volume_m3": [500.0, 300.0],
            "target_taz": ["BS Hondelage 1_192", "BS Hondelage 1_192"],
        },
        geometry=[poly, poly], crs="EPSG:25832",
    )


def test_rename_maps_all_potential_columns():
    out = rename_columns(_source_gdf())
    for std in POTENTIAL_COLUMNS:
        assert std in out.columns, std
    assert "building_id" in out.columns
    assert "assigned_Workers" not in out.columns
    assert out["potential_work"].tolist() == [10.0, 0.0]
    assert out["potential_retail_non_daily"].tolist() == [1.0, 0.0]
    assert out.crs.to_epsg() == 25832


def test_rename_is_idempotent_on_values():
    g = _source_gdf()
    out1 = rename_columns(g)
    out2 = rename_columns(g)
    assert out1["potential_generic"].equals(out2["potential_generic"])
