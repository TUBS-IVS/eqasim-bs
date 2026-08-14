"""The shared spatial helpers must expose the same ZGB-8 map and loaders that
run_mid_validation used before extraction, so the refactor is behaviour-preserving.

Also pins the issue #293 decision: braunschweig.analysis.spatial is the single
owner of VG250 archive access. Its analysis/validation callers (load_kreise,
load_gemeinden, load_vg250_layer(strict=True)) must fail loudly -- raising
FileNotFoundError naming the expected archive path -- when the archive is
missing, because a missing per-Kreis geography would otherwise silently
invalidate the whole validation run (CLAUDE.md "Fallback transparency").
"""
import logging

import pytest

from braunschweig.analysis import spatial
from braunschweig.analysis import run_mid_validation as rmv


def test_zgb8_map_is_shared_and_unchanged():
    assert spatial.ZGB8 == {
        "03101": "SK Braunschweig", "03102": "SK Salzgitter",
        "03103": "SK Wolfsburg", "03151": "LK Gifhorn",
        "03153": "LK Goslar", "03154": "LK Helmstedt",
        "03157": "LK Peine", "03158": "LK Wolfenbüttel",
    }
    # run_mid_validation must now re-export the same object (no divergent copy).
    assert rmv.ZGB8 is spatial.ZGB8


def test_spatial_exposes_loader_callables():
    for name in ("load_kreise", "load_gemeinden", "load_regiostar", "assign_geographies"):
        assert callable(getattr(spatial, name))


def test_spatial_exposes_shared_vg250_loader():
    """The de-duplicated VG250 loader (issue #293) must be a public entry
    point of this module so other analysis modules can share it."""
    assert callable(spatial.load_vg250_layer)
    assert callable(spatial._resolve_vg250_gpkg)


def test_load_kreise_raises_when_archive_missing(monkeypatch, tmp_path):
    """The strict analysis path must fail loudly, naming the expected path."""
    missing_zip = tmp_path / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
    monkeypatch.setattr(spatial, "VG250_ZIP", missing_zip)
    monkeypatch.setattr(spatial, "VG250_CACHE", tmp_path / "cache" / "DE_VG250.gpkg")

    with pytest.raises(FileNotFoundError, match=r"VG250 archive missing.*Re-run the synpp data download"):
        spatial.load_kreise(homes_crs="EPSG:25832")

    with pytest.raises(FileNotFoundError, match=str(missing_zip).replace("\\", "\\\\")):
        spatial.load_gemeinden(homes_crs="EPSG:25832")


def test_load_vg250_layer_strict_default_raises_naming_the_archive_path(monkeypatch, tmp_path):
    missing_zip = tmp_path / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
    monkeypatch.setattr(spatial, "VG250_ZIP", missing_zip)

    with pytest.raises(FileNotFoundError) as excinfo:
        spatial.load_vg250_layer("vg250_gem")  # strict=True is the default

    assert str(missing_zip) in str(excinfo.value)


def test_resolve_vg250_tolerant_mode_returns_none_and_warns(monkeypatch, tmp_path, caplog):
    """The tolerant (strict=False) branch is the dashboard's contract, but it
    is implemented once here -- pin that it also logs an explicit warning
    (not just a silent None), per CLAUDE.md "Fallback transparency"."""
    missing_zip = tmp_path / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
    monkeypatch.setattr(spatial, "VG250_ZIP", missing_zip)

    with caplog.at_level(logging.WARNING, logger="braunschweig.analysis.spatial"):
        result = spatial._resolve_vg250_gpkg(strict=False)

    assert result is None
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    assert str(missing_zip) in warnings[0].getMessage()
    assert "omitted" in warnings[0].getMessage()
