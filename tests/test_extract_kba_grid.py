"""Tests for extract_ev_grid() in scripts/extract_kba_fleet.py.

Builds a tiny GeoDataFrame in EPSG:3857 with 3 cells and writes it to a
temporary .gpkg file:

- Cell A: centroid inside the ZGB bbox (lon 10.0-11.7, lat 51.5-52.9),
  no suppression.
- Cell B: centroid inside the ZGB bbox, suppressed (ZS_Anteil_ == "-").
- Cell C: centroid far outside the ZGB bbox (near Munich), must be clipped.

Assertions:
- Only cells A and B survive the clip (2 rows).
- ev_share is a fraction (elektro_an / 100).
- suppressed flag is correct (True for B, False for A).
- All bound columns present and equal the cell geometry.bounds in EPSG:3857.
- stichtag is "2026-04-01" for every row.
- Suppressed cell count is logged (no-silent-fallback rule).
"""
import math

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import box

import scripts.extract_kba_fleet as ex


def _make_cell(lon_center, lat_center, crs_3857_size=5000.0):
    """Build a 5 km x 5 km box in EPSG:3857 whose centroid is near (lon, lat).

    We place the box at the approximate EPSG:3857 coordinates for the given
    WGS-84 centroid.  An equirectangular approximation is sufficient for test
    construction because the clip uses the centroid's reprojected 4326 coords.
    """
    # Approximate EPSG:3857 coords from WGS-84 (good enough for test placement)
    earth_circumference = 20037508.342789244
    x = lon_center * earth_circumference / 180.0
    # Mercator Y from latitude
    import math
    lat_rad = math.radians(lat_center)
    y = math.log(math.tan(math.pi / 4.0 + lat_rad / 2.0)) * earth_circumference / math.pi
    half = crs_3857_size / 2.0
    return box(x - half, y - half, x + half, y + half)


@pytest.fixture()
def ev_grid_gpkg(tmp_path):
    """Write a 3-cell EV-grid gpkg; return the path."""
    # Cell A: Braunschweig area (~10.53 E, 52.27 N) — inside, not suppressed
    geom_a = _make_cell(10.53, 52.27)
    # Cell B: Wolfsburg area (~10.79 E, 52.43 N) — inside, suppressed
    geom_b = _make_cell(10.79, 52.43)
    # Cell C: Munich area (~11.58 E, 48.14 N) — outside ZGB bbox (lat < 51.5)
    geom_c = _make_cell(11.58, 48.14)

    gdf = gpd.GeoDataFrame(
        {
            "id_5km": ["5kmN2695E4340", "5kmN2700E4360", "5kmN2560E4400"],
            "elektro_an": [5.2, 3.8, 7.1],
            "ZS_Anteil_": ["ok", "-", "ok"],
            "berichtsj": [2026, 2026, 2026],
        },
        geometry=[geom_a, geom_b, geom_c],
        crs="EPSG:3857",
    )

    path = tmp_path / "kba_ev_grid_5km_2026.gpkg"
    gdf.to_file(str(path), driver="GPKG")
    return path


def test_clip_drops_outside_cell(ev_grid_gpkg):
    """Cell C (Munich, lat ~48.14) must be dropped by the ZGB bbox clip."""
    df = ex.extract_ev_grid(ev_grid_gpkg)
    assert len(df) == 2, f"Expected 2 cells after clip, got {len(df)}"


def test_cell_ids_correct(ev_grid_gpkg):
    """Surviving cells must have the expected id_5km values."""
    df = ex.extract_ev_grid(ev_grid_gpkg)
    assert set(df["cell_id"]) == {"5kmN2695E4340", "5kmN2700E4360"}


def test_ev_share_is_fraction(ev_grid_gpkg):
    """ev_share must equal elektro_an / 100 (fraction, not percent)."""
    df = ex.extract_ev_grid(ev_grid_gpkg)
    row_a = df[df["cell_id"] == "5kmN2695E4340"].iloc[0]
    assert abs(row_a["ev_share"] - 5.2 / 100.0) < 1e-9, (
        f"ev_share expected {5.2 / 100.0}, got {row_a['ev_share']}"
    )


def test_suppressed_flag_true_for_dash_cell(ev_grid_gpkg):
    """Cell B with ZS_Anteil_ == '-' must have suppressed == True."""
    df = ex.extract_ev_grid(ev_grid_gpkg)
    row_b = df[df["cell_id"] == "5kmN2700E4360"].iloc[0]
    assert bool(row_b["suppressed"]) is True, "suppressed flag should be True for '-' cell"


def test_suppressed_flag_false_for_normal_cell(ev_grid_gpkg):
    """Cell A with ZS_Anteil_ == 'ok' must have suppressed == False."""
    df = ex.extract_ev_grid(ev_grid_gpkg)
    row_a = df[df["cell_id"] == "5kmN2695E4340"].iloc[0]
    assert bool(row_a["suppressed"]) is False, "suppressed flag should be False for non-dash cell"


def test_stichtag_is_2026_04_01(ev_grid_gpkg):
    """Every row must carry stichtag == '2026-04-01'."""
    df = ex.extract_ev_grid(ev_grid_gpkg)
    assert list(df["stichtag"].unique()) == ["2026-04-01"]


def test_bounds_columns_present(ev_grid_gpkg):
    """All four bound columns (minx, miny, maxx, maxy) must be present."""
    df = ex.extract_ev_grid(ev_grid_gpkg)
    for col in ("minx", "miny", "maxx", "maxy"):
        assert col in df.columns, f"Column {col!r} missing"


def test_bounds_match_geometry(ev_grid_gpkg):
    """minx/miny/maxx/maxy must match the cell geometry bounds in EPSG:3857."""
    import geopandas as gpd
    gdf = gpd.read_file(str(ev_grid_gpkg))
    # Reproject centroids to WGS-84 to identify cells
    cent = gdf.geometry.to_crs(4326).centroid
    inside = (cent.x >= 10.0) & (cent.x <= 11.7) & (cent.y >= 51.5) & (cent.y <= 52.9)
    gdf_inside = gdf[inside].copy()

    df = ex.extract_ev_grid(ev_grid_gpkg)
    for cell_id, row in df.set_index("cell_id").iterrows():
        orig = gdf_inside[gdf_inside["id_5km"] == cell_id].iloc[0]
        bounds = orig.geometry.bounds  # (minx, miny, maxx, maxy)
        assert abs(row["minx"] - bounds[0]) < 1.0, f"minx mismatch for {cell_id}"
        assert abs(row["miny"] - bounds[1]) < 1.0, f"miny mismatch for {cell_id}"
        assert abs(row["maxx"] - bounds[2]) < 1.0, f"maxx mismatch for {cell_id}"
        assert abs(row["maxy"] - bounds[3]) < 1.0, f"maxy mismatch for {cell_id}"


def test_required_columns_present(ev_grid_gpkg):
    """All 8 required output columns must be present."""
    df = ex.extract_ev_grid(ev_grid_gpkg)
    required = {"cell_id", "stichtag", "ev_share", "minx", "miny", "maxx", "maxy", "suppressed"}
    assert required.issubset(set(df.columns)), (
        f"Missing columns: {required - set(df.columns)}"
    )


def test_suppressed_count_logged(ev_grid_gpkg, caplog):
    """A log message must report the suppressed-cell count (no-silent-fallback rule)."""
    import logging
    with caplog.at_level(logging.INFO, logger="extract_kba_fleet"):
        ex.extract_ev_grid(ev_grid_gpkg)
    combined = " ".join(caplog.messages)
    assert "suppressed" in combined.lower(), (
        "No log message mentioning suppressed cells found"
    )
