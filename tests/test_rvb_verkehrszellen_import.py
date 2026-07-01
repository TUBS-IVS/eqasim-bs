"""Tests for ``scripts/import_rvb_verkehrszellen.py``.

Covers the pure ``rename_columns`` transformation (column rename, commune_id /
kreis derivation, CRS reprojection) and the ``validate`` checks. The CLI / file
-IO path is exercised by the manual real-data run (local-only proprietary gpkg).
"""
import geopandas as gpd
import pytest
from shapely.geometry import Polygon

from scripts.import_rvb_verkehrszellen import (
    rename_columns, validate, COLUMN_RENAME,
)


def _source_gdf():
    # Two Verkehrszellen in Braunschweig (AGS 3101000 -> commune 03101000,
    # kreis 03101); source CRS is EPSG:32632 (must be reprojected to 25832).
    poly_a = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    poly_b = Polygon([(2, 2), (2, 3), (3, 3), (3, 2)])
    return gpd.GeoDataFrame(
        {
            "Verkehrszelle_Nummer": ["310101901", "310101902"],
            "Verkehrszelle_Name": ["BS Stadtkern 6", "BS Stadtkern 7"],
            "Amtlicher_Gemeindeschlüssel": ["3101000", "3101000"],
            "RegioStaR7_Regionstyp": [72, 74],
        },
        geometry=[poly_a, poly_b], crs="EPSG:32632",
    )


def test_rename_maps_and_derives_columns():
    out = rename_columns(_source_gdf())
    assert {"taz_id", "taz_name", "commune_id", "kreis", "regiostar7",
             "geometry"}.issubset(out.columns)
    assert "Verkehrszelle_Nummer" not in out.columns
    assert out["taz_id"].tolist() == ["310101901", "310101902"]
    # commune_id = "0" + 7-digit AGS; kreis = commune_id[:5]
    assert out["commune_id"].tolist() == ["03101000", "03101000"]
    assert out["kreis"].tolist() == ["03101", "03101"]
    assert out["regiostar7"].tolist() == [72, 74]


def test_rename_reprojects_to_25832():
    out = rename_columns(_source_gdf())
    assert out.crs.to_epsg() == 25832


def test_rename_handles_8digit_ags_two_digit_bundesland():
    # The real gpkg spans the wider VISUM Einflussraum: zones in a two-digit
    # Bundesland (>=10) carry an already-8-digit AGS (e.g. Sachsen-Anhalt
    # 15081026). zfill(8) must leave these UNCHANGED (not prefix a "0"), and
    # kreis = commune_id[:5]. This is the real-data case Task 5 surfaced.
    poly = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    g = gpd.GeoDataFrame(
        {
            "Verkehrszelle_Nummer": ["150810261"],
            "Verkehrszelle_Name": ["SA zone"],
            "Amtlicher_Gemeindeschlüssel": ["15081026"],   # already 8-digit
            "RegioStaR7_Regionstyp": [77],
        },
        geometry=[poly], crs="EPSG:32632",
    )
    out = rename_columns(g)
    assert out["commune_id"].tolist() == ["15081026"]   # unchanged, NOT "015081026"
    assert out["kreis"].tolist() == ["15081"]


def test_rename_raises_on_missing_column():
    g = _source_gdf().drop(columns=["RegioStaR7_Regionstyp"])
    with pytest.raises(ValueError, match="missing expected columns"):
        rename_columns(g)


def test_rename_raises_on_missing_crs():
    # A GeoDataFrame constructed without a CRS has crs is None; the importer must
    # refuse to reproject rather than silently assume the source datum (geopandas
    # set_crs(None) itself raises, so build a CRS-less frame directly instead).
    poly = Polygon([(0, 0), (0, 1), (1, 1), (1, 0)])
    g = gpd.GeoDataFrame(
        {
            "Verkehrszelle_Nummer": ["310101901"],
            "Verkehrszelle_Name": ["BS Stadtkern 6"],
            "Amtlicher_Gemeindeschlüssel": ["3101000"],
            "RegioStaR7_Regionstyp": [72],
        },
        geometry=[poly],
    )
    assert g.crs is None
    with pytest.raises(ValueError, match="no CRS"):
        rename_columns(g)


def test_validate_raises_on_duplicate_taz_id():
    out = rename_columns(_source_gdf())
    out.loc[1, "taz_id"] = out.loc[0, "taz_id"]
    with pytest.raises(ValueError, match="duplicate taz_id"):
        validate(out)


def test_validate_raises_on_out_of_range_rs7():
    out = rename_columns(_source_gdf())
    out.loc[0, "regiostar7"] = 99
    with pytest.raises(ValueError, match="outside 71..77"):
        validate(out)


def test_validate_raises_on_empty_geometry():
    out = rename_columns(_source_gdf())
    out.loc[0, "geometry"] = Polygon()
    with pytest.raises(ValueError, match="empty/missing geometry"):
        validate(out)


def test_validate_raises_on_bad_ags():
    out = rename_columns(_source_gdf())
    out.loc[0, "commune_id"] = "0310100"  # 7 chars instead of 8
    with pytest.raises(ValueError, match="commune_id that is not 8 digits"):
        validate(out)


def test_validate_accepts_clean_input():
    out = rename_columns(_source_gdf())
    validate(out)  # must not raise


def test_main_returns_2_when_source_missing(tmp_path):
    from scripts.import_rvb_verkehrszellen import main
    rc = main(["--source", str(tmp_path / "nope.gpkg"),
               "--out-dir", str(tmp_path / "out")])
    assert rc == 2


def test_main_writes_parquet_and_readme(tmp_path):
    import geopandas as gpd
    from scripts.import_rvb_verkehrszellen import main
    src = tmp_path / "src.gpkg"
    _source_gdf().to_file(src, layer="Verkehrszellen", driver="GPKG")
    out_dir = tmp_path / "out"
    rc = main(["--source", str(src), "--out-dir", str(out_dir)])
    assert rc == 0
    parquet = out_dir / "rvb_verkehrszellen_epsg25832.parquet"
    assert parquet.exists()
    assert (out_dir / "README.md").exists()
    loaded = gpd.read_parquet(parquet)
    assert loaded["taz_id"].tolist() == ["310101901", "310101902"]
    assert loaded["commune_id"].tolist() == ["03101000", "03101000"]
    assert loaded.crs.to_epsg() == 25832
