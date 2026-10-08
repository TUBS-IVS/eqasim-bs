"""The Pendler tab reads the pre-portal commutes while they exist (eqasim-bs#442).

``<prefix>commutes.gpkg`` is built from the written activities, where the workplace of a far commuter is an
``outside`` activity, so the commuter tab's "synthesis home->work assignment" would miss those persons. The loader
prefers ``<prefix>commutes_pre_portal.gpkg`` through the shared resolver (staleness rule, logged source) and keeps the
old behaviour when it is absent.
"""
from __future__ import annotations

import logging
import os
import sys
import warnings
from pathlib import Path

import geopandas as gpd
from shapely.geometry import LineString

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.analysis.simwrapper import commuter_tabs  # noqa: E402


def _write_commutes(path: Path, person_ids, layer=None) -> None:
    lines = gpd.GeoDataFrame({"person_id": person_ids},
                             geometry=[LineString([(0, 0), (1000.0 * (i + 1), 0)]) for i in range(len(person_ids))])
    if layer is None:
        lines.to_file(path, driver="GPKG")
    else:
        lines.to_file(path, layer=layer, driver="GPKG")


def _write_pre_portal(path: Path, person_ids) -> None:
    _write_commutes(path, person_ids)
    _write_commutes(path, [], layer="education")


def test_loader_prefers_the_pre_portal_commutes_and_logs_the_source(tmp_path, caplog):
    _write_commutes(tmp_path / "bs_commutes.gpkg", [3])
    _write_pre_portal(tmp_path / "bs_commutes_pre_portal.gpkg", [1, 3])
    with caplog.at_level(logging.INFO), warnings.catch_warnings():
        warnings.simplefilter("error")  # the multi-layer file must be read with an explicit layer
        commutes = commuter_tabs._load_commutes(str(tmp_path))
    assert list(commutes["person_id"]) == [1, 3]
    assert any("bs_commutes_pre_portal.gpkg" in record.getMessage() for record in caplog.records)


def test_loader_keeps_reading_commutes_gpkg_without_the_pre_portal_file(tmp_path):
    _write_commutes(tmp_path / "bs_commutes.gpkg", [3])
    commutes = commuter_tabs._load_commutes(str(tmp_path))
    assert list(commutes["person_id"]) == [3]


def test_loader_ignores_a_stale_pre_portal_file(tmp_path, caplog):
    _write_pre_portal(tmp_path / "bs_commutes_pre_portal.gpkg", [1, 3])
    plain = tmp_path / "bs_commutes.gpkg"
    _write_commutes(plain, [3])
    old = plain.stat().st_mtime - 3600.0
    os.utime(tmp_path / "bs_commutes_pre_portal.gpkg", (old, old))
    with caplog.at_level(logging.WARNING):
        commutes = commuter_tabs._load_commutes(str(tmp_path))
    assert list(commutes["person_id"]) == [3]
    assert any("older" in record.getMessage() and "ignored" in record.getMessage() for record in caplog.records)


def test_loader_without_any_commutes_file_still_returns_none(tmp_path):
    assert commuter_tabs._load_commutes(str(tmp_path)) is None
    assert commuter_tabs._load_commutes(None) is None
