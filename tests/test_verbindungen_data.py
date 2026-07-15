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


def test_parse_ags_list_drops_dbf_truncation_fragments():
    # The DBF ags_0 field truncates at 254 chars; a cut-off trailing segment
    # must be DROPPED, never zero-padded into a fake AGS code.
    from braunschweig.data.verbindungen.zones import parse_ags_list
    assert parse_ags_list("03151001,03") == ["03151001"]
    assert parse_ags_list("07233055,07233") == ["07233055"]  # 5-digit fragment


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
    # vg250-9 (09999) and vg250-99 (09999, DBF-truncated ags_0) are out of
    # scope; the truncated cell must be clipped away, not crash the stage.
    assert set(df_cells["cell_id"]) == {"stadtteil-1", "stadtteil-2", "vg250-3"}
    assert "vg250-99" not in set(df_cells["cell_id"])
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


# --- work_od loader ----------------------------------------------------------

def test_clip_qzm_keeps_internal_and_reports_boundary(tmp_path):
    from braunschweig.data.verbindungen.work_od import clip_qzm_to_cells, read_qzm_csv
    from tests.fixtures.verbindungen_fixtures import write_qzm_csv
    p = tmp_path / "qzm.csv"
    write_qzm_csv(p, [
        ("stadtteil-1", "stadtteil-1", 100),
        ("stadtteil-1", "vg250-3", 20),
        ("stadtteil-1", "vg250-999", 30),   # outbound (dest outside)
        ("vg250-999", "vg250-3", 40),       # inbound (origin outside)
        ("vg250-777", "vg250-888", 50),     # fully external
    ])
    df = read_qzm_csv(str(p))
    clipped, stats = clip_qzm_to_cells(df, {"stadtteil-1", "stadtteil-2", "vg250-3"})
    assert len(clipped) == 2
    assert clipped["commuters"].sum() == 120
    assert stats["outbound_commuters"] == 30
    assert stats["inbound_commuters"] == 40
    assert list(clipped.columns) == ["origin_cell_id", "destination_cell_id", "commuters"]


def test_clip_qzm_raises_on_empty_internal_result(tmp_path):
    from braunschweig.data.verbindungen.work_od import clip_qzm_to_cells, read_qzm_csv
    from tests.fixtures.verbindungen_fixtures import write_qzm_csv
    p = tmp_path / "qzm.csv"
    write_qzm_csv(p, [("vg250-777", "vg250-888", 50)])
    df = read_qzm_csv(str(p))
    with pytest.raises(RuntimeError):
        clip_qzm_to_cells(df, {"stadtteil-1"})


def test_read_qzm_rejects_censoring_violation(tmp_path):
    from braunschweig.data.verbindungen.work_od import read_qzm_csv
    from tests.fixtures.verbindungen_fixtures import write_qzm_csv
    p = tmp_path / "qzm.csv"
    write_qzm_csv(p, [("stadtteil-1", "stadtteil-1", 5)])  # < 10 impossible upstream
    with pytest.raises(RuntimeError):
        read_qzm_csv(str(p))


# --- margins loader ----------------------------------------------------------

def test_read_statisch_parses_semicolon_and_dominanz(tmp_path):
    from braunschweig.data.verbindungen.margins import read_statisch_csv
    from tests.fixtures.verbindungen_fixtures import write_statisch_csv
    p = tmp_path / "wo.csv"
    write_statisch_csv(p, [
        {"WO_verb_zell_id": "stadtteil-1", "SvB_aGeB": 5240},
        {"WO_verb_zell_id": "vg250-3", "SvB_aGeB": "*"},
    ])
    df = read_statisch_csv(str(p), value_name="workers_at_home")
    df = df.set_index("cell_id")
    assert int(df.loc["stadtteil-1", "workers_at_home"]) == 5240
    assert pd.isna(df.loc["vg250-3", "workers_at_home"])


def test_read_statisch_rejects_unparseable_values(tmp_path):
    # '*' is the ONLY documented Dominanz suppression marker; any other
    # unparseable token is a data-quality regression and must fail loudly
    # (naming the file and the offending token), never be silently NA'd.
    from braunschweig.data.verbindungen.margins import read_statisch_csv
    from tests.fixtures.verbindungen_fixtures import write_statisch_csv
    p = tmp_path / "wo.csv"
    write_statisch_csv(p, [
        {"WO_verb_zell_id": "stadtteil-1", "SvB_aGeB": 5240},
        {"WO_verb_zell_id": "vg250-3", "SvB_aGeB": "abc"},
    ])
    with pytest.raises(RuntimeError) as exc_info:
        read_statisch_csv(str(p), value_name="workers_at_home")
    assert "abc" in str(exc_info.value)
    assert "wo.csv" in str(exc_info.value)


def test_margins_outer_merge_covers_all_cells(tmp_path):
    from braunschweig.data.verbindungen.margins import build_margins_frame, read_statisch_csv
    from tests.fixtures.verbindungen_fixtures import write_statisch_csv
    wo, ao = tmp_path / "wo.csv", tmp_path / "ao.csv"
    write_statisch_csv(wo, [{"WO_verb_zell_id": "stadtteil-1", "SvB_aGeB": 100}])
    # AO file uses the same id column name convention with AO prefix
    write_statisch_csv(ao, [{"WO_verb_zell_id": "stadtteil-2", "SvB_aGeB": 50}])
    df = build_margins_frame(
        read_statisch_csv(str(wo), "workers_at_home"),
        read_statisch_csv(str(ao), "workers_at_workplace"),
        cell_ids=["stadtteil-1", "stadtteil-2", "vg250-3"],
    )
    df = df.set_index("cell_id")
    assert int(df.loc["stadtteil-1", "workers_at_home"]) == 100
    assert pd.isna(df.loc["vg250-3", "workers_at_home"])
    assert int(df.loc["stadtteil-2", "workers_at_workplace"]) == 50
    assert len(df) == 3


# --- config wiring -----------------------------------------------------------

def test_popsim_mid_config_runs_verbindungen_validation():
    import yaml
    with open(REPO_ROOT / "config_popsim_mid_braunschweig.yml", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    assert "braunschweig.analysis.verbindungen_validation" in cfg["run"]
