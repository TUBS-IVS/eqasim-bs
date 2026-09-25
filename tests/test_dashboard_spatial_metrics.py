"""Pins the issue #293 decision for the dashboard's VG250/per-Kreis cluster.

Before this fix, ``braunschweig.analysis.dashboard.spatial_metrics`` located,
extracted and read the VG250 archive independently of
``braunschweig.analysis.spatial``, with its own caching strategy and its own
opinion on what a missing archive means (``_ensure_vg250`` returned ``None``
with no log line at all). Both callers now go through the single shared
loader in ``braunschweig.analysis.spatial``.

These tests pin:
  - the dashboard's tolerant failure mode (returns ``None``) is preserved,
  - but it is no longer silent: a missing archive now logs an explicit
    ``warning`` naming the archive path and the metrics that will be
    omitted (CLAUDE.md "Fallback transparency" -- a gap that produces no log
    line reads to the operator as "nothing to report"),
  - and that the dashboard and the analysis/validation path share one extracted
    cache that is written once and refreshed when the archive is newer.
"""
import logging
import os
import zipfile

from braunschweig.analysis import spatial
from braunschweig.analysis.dashboard import spatial_metrics


def test_load_zgb_kreise_returns_none_and_warns_once_when_archive_missing(
        monkeypatch, tmp_path, caplog):
    """The Kreis-polygon loader run_metrics calls propagates the tolerant None
    rather than raising, so the per-Kreis panel is skipped and the rest of the
    dashboard still renders -- with exactly one warning naming what is lost."""
    missing_zip = tmp_path / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
    monkeypatch.setattr(spatial, "VG250_ZIP", missing_zip)
    monkeypatch.setattr(spatial, "VG250_CACHE", tmp_path / "cache" / "DE_VG250.gpkg")

    with caplog.at_level(logging.WARNING, logger="braunschweig.analysis.spatial"):
        result = spatial_metrics._load_zgb_kreise()

    assert result is None
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert str(missing_zip) in message
    assert "per-Kreis" in message and "omitted" in message


def test_dashboard_and_analysis_paths_share_one_refreshing_cache(monkeypatch, tmp_path):
    """Both callers get the SAME extracted file; it is extracted once, and again
    only when the archive is newer than the cache (a data refresh is picked up)."""
    archive = tmp_path / "vg250.zip"
    cache = tmp_path / "cache" / "DE_VG250.gpkg"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(spatial.VG250_INNER, b"first")
    monkeypatch.setattr(spatial, "VG250_ZIP", archive)
    monkeypatch.setattr(spatial, "VG250_CACHE", cache)

    dashboard_path = spatial_metrics._ensure_vg250()
    analysis_path = spatial._resolve_vg250_gpkg(strict=True)
    assert dashboard_path == analysis_path == cache
    assert cache.read_bytes() == b"first"

    # A second call reuses the cache: make the cache clearly newer, then check it is kept.
    os.utime(cache, (archive.stat().st_mtime + 100, archive.stat().st_mtime + 100))
    kept_mtime = cache.stat().st_mtime
    spatial_metrics._ensure_vg250()
    assert cache.stat().st_mtime == kept_mtime

    # A refreshed archive (newer than the cache) is extracted again.
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr(spatial.VG250_INNER, b"second")
    os.utime(archive, (kept_mtime + 100, kept_mtime + 100))
    spatial._resolve_vg250_gpkg(strict=True)
    assert cache.read_bytes() == b"second"
