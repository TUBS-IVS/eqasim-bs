"""The shared spatial helpers must expose the same ZGB-8 map and loaders that
run_mid_validation used before extraction, so the refactor is behaviour-preserving.

Also pins the issue #293 decision: braunschweig.analysis.spatial is the single
owner of VG250 archive access. Its analysis/validation callers (load_kreise,
load_gemeinden, load_vg250_layer(strict=True)) must fail loudly -- raising
FileNotFoundError naming the expected archive path -- when the archive is
missing, because a missing per-Kreis geography would otherwise silently
invalidate the whole validation run (CLAUDE.md "Fallback transparency").
"""
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


@pytest.mark.parametrize("load", [
    pytest.param(lambda: spatial.load_kreise(homes_crs="EPSG:25832"), id="load_kreise"),
    pytest.param(lambda: spatial.load_gemeinden(homes_crs="EPSG:25832"), id="load_gemeinden"),
    pytest.param(lambda: spatial.load_vg250_layer("vg250_gem"), id="load_vg250_layer-strict-default"),
])
def test_strict_loaders_raise_naming_the_archive_and_the_fix(monkeypatch, tmp_path, load):
    """The strict analysis path must fail loudly, naming the expected path and the fix.
    (The dashboard's tolerant branch is pinned in tests/test_dashboard_spatial_metrics.py.)"""
    missing_zip = tmp_path / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
    monkeypatch.setattr(spatial, "VG250_ZIP", missing_zip)
    monkeypatch.setattr(spatial, "VG250_CACHE", tmp_path / "cache" / "DE_VG250.gpkg")

    with pytest.raises(FileNotFoundError) as excinfo:
        load()

    assert str(missing_zip) in str(excinfo.value)
    assert "Re-run the synpp data download" in str(excinfo.value)
