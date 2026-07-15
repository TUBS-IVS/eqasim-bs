"""Tests for the VerBindungen data layer (#124 P1).

Covers the download-script pure helpers, the loader parsing helpers and the
loader stage invariants on small synthetic fixtures. No network, no large data.

Run with::

    python -m pytest tests/test_verbindungen_data.py -v
"""
from __future__ import annotations

import os
import pathlib
import sys

import pandas as pd
import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "scripts"))


# --- download script helpers ------------------------------------------------

def test_needs_download_missing_file(tmp_path):
    from download_verbindungen import needs_download
    target = tmp_path / "absent.csv"
    assert needs_download(str(target), expected_sha256=None, force=False) is True


def test_needs_download_present_no_hash_pinned(tmp_path):
    from download_verbindungen import needs_download
    target = tmp_path / "present.csv"
    target.write_bytes(b"hello")
    # No pinned hash yet (first acquisition): keep the existing file.
    assert needs_download(str(target), expected_sha256=None, force=False) is False


def test_needs_download_hash_mismatch_triggers_redownload(tmp_path):
    from download_verbindungen import needs_download, _sha256_of
    target = tmp_path / "present.csv"
    target.write_bytes(b"hello")
    good = _sha256_of(str(target))
    assert needs_download(str(target), expected_sha256=good, force=False) is False
    assert needs_download(str(target), expected_sha256="0" * 64, force=False) is True
    assert needs_download(str(target), expected_sha256=good, force=True) is True


def test_render_provenance_contains_all_fields():
    from download_verbindungen import render_provenance
    entries = [dict(
        filename="QZM-Berufspendler-VerBindungen-Verkehrszellen.csv",
        offer_id="767413386339078144",
        url="https://mobilithek.info/mdp-api/files/aux/767413386339078144/QZM-Berufspendler-VerBindungen-Verkehrszellen.csv",
        sha256="abc123",
        size_bytes=5426330,
        downloaded_at="2026-07-15T12:00:00",
    )]
    text = render_provenance(entries)
    assert "QZM-Berufspendler-VerBindungen-Verkehrszellen.csv" in text
    assert "767413386339078144" in text
    # The full per-file URL must be rendered literally, not just its components.
    assert ("https://mobilithek.info/mdp-api/files/aux/767413386339078144/"
            "QZM-Berufspendler-VerBindungen-Verkehrszellen.csv") in text
    assert "abc123" in text
    assert "31.12.2019" in text          # reference date note
    assert "LICENSE_FREE_USE_OPEN_DATA" in text


# --- zones loader -----------------------------------------------------------

ZGB_SCOPE = ["03101", "03151"]


def _load_fixture_cells(tmp_path):
    import geopandas as gpd
    from tests.fixtures.verbindungen_fixtures import write_cells_shapefile_zip
    zip_path = write_cells_shapefile_zip(tmp_path)
    return gpd.read_file(f"zip://{zip_path}!verbindungen-verkehrszellen.shp")


def test_parse_ags_list_splits_pads_dedupes():
    from braunschweig.data.verbindungen.zones import parse_ags_list
    assert parse_ags_list("03101000") == ["03101000"]
    assert parse_ags_list("03151001,03151002") == ["03151001", "03151002"]
    assert parse_ags_list("02000000,02000000") == ["02000000"]  # dedupe
    assert parse_ags_list(" 3101000 ") == ["03101000"]          # pad + strip


def test_cell_kreis_id_raises_on_mixed_kreise():
    from braunschweig.data.verbindungen.zones import cell_kreis_id
    assert cell_kreis_id(["03151001", "03151002"]) == "03151"
    with pytest.raises(ValueError):
        cell_kreis_id(["03151001", "03101000"])


def test_build_zones_frames_clips_scope_and_maps_communes(tmp_path):
    from braunschweig.data.verbindungen.zones import build_zones_frames
    from tests.fixtures.verbindungen_fixtures import make_municipalities_gdf
    gdf_raw = _load_fixture_cells(tmp_path)
    df_cells, df_cell_commune, stats = build_zones_frames(
        gdf_raw, make_municipalities_gdf(), scope=ZGB_SCOPE,
        max_fallback_share=0.60,
    )
    # vg250-9 (09999) is out of scope
    assert set(df_cells["cell_id"]) == {"stadtteil-1", "stadtteil-2", "vg250-3"}
    assert df_cells.crs.to_epsg() == 25832
    assert bool(df_cells.set_index("cell_id").loc["stadtteil-1", "is_stadtteil"]) is True
    assert df_cells.set_index("cell_id").loc["vg250-3", "kreis_id"] == "03151"
    # direct AGS matches: 03101000 -> 031010001000 (both stadtteil cells),
    # 03151001 -> 031510000001
    mapping = df_cell_commune.set_index(["cell_id", "commune_id"])
    assert ("stadtteil-1", "031010001000") in mapping.index
    assert ("vg250-3", "031510000001") in mapping.index
    # geometric fallback: AGS 03151002 has no dict match; commune 031510029999
    # lies inside vg250-3 and must be recovered via the fallback with the flag.
    assert ("vg250-3", "031510029999") in mapping.index
    assert bool(mapping.loc[("vg250-3", "031510029999"), "via_fallback"]) is True
    # 3 primary AGS matches (03101000 x2 + 03151001), 1 fallback (03151002).
    assert stats["n_ags_primary"] == 3 and stats["n_ags_fallback"] == 1
    # full commune coverage: every in-scope fixture commune is reachable from
    # at least one cell (nothing silently dropped from the mapping).
    assert set(df_cell_commune["commune_id"]) == {
        "031010001000", "031510000001", "031510029999"}


def test_build_zones_frames_raises_above_fallback_bound(tmp_path):
    from braunschweig.data.verbindungen.zones import build_zones_frames
    from tests.fixtures.verbindungen_fixtures import make_municipalities_gdf
    gdf_raw = _load_fixture_cells(tmp_path)
    with pytest.raises(RuntimeError):
        build_zones_frames(gdf_raw, make_municipalities_gdf(), scope=ZGB_SCOPE,
                           max_fallback_share=0.10)


def test_build_zones_frames_raises_on_uncovered_commune(tmp_path):
    import geopandas as gpd
    from shapely.geometry import box
    from braunschweig.data.verbindungen.zones import build_zones_frames
    from tests.fixtures.verbindungen_fixtures import make_municipalities_gdf
    gdf_raw = _load_fixture_cells(tmp_path)
    # A 4th in-scope commune (Kreis prefix 03151) far OUTSIDE every fixture
    # cell: its AGS-8 (ars_to_ags8('031510099000') = '03151000') matches no
    # cell AGS and its representative point falls in no cell polygon, so it
    # can never appear in df_cell_commune -> the coverage check must raise.
    far_away = gpd.GeoDataFrame(
        {"commune_id": ["031510099000"]},
        geometry=[box(700000.0, 5900000.0, 701000.0, 5901000.0)],
        crs="EPSG:25832",
    )
    df_mun = pd.concat([make_municipalities_gdf(), far_away], ignore_index=True)
    with pytest.raises(RuntimeError, match="coverage"):
        build_zones_frames(gdf_raw, df_mun, scope=ZGB_SCOPE,
                           max_fallback_share=0.60)
