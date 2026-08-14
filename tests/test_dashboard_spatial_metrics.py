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
  - and that both the analysis/validation path (``spatial.py``) and the
    dashboard path (``spatial_metrics.py``) resolve through the same
    underlying loader, not two independent implementations.
"""
import logging

from braunschweig.analysis import spatial
from braunschweig.analysis.dashboard import spatial_metrics


def test_ensure_vg250_returns_none_and_logs_warning_when_archive_missing(monkeypatch, tmp_path, caplog):
    missing_zip = tmp_path / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
    monkeypatch.setattr(spatial, "VG250_ZIP", missing_zip)
    monkeypatch.setattr(spatial, "VG250_CACHE", tmp_path / "cache" / "DE_VG250.gpkg")

    with caplog.at_level(logging.WARNING, logger="braunschweig.analysis.spatial"):
        result = spatial_metrics._ensure_vg250()

    # Tolerant failure mode preserved: caller gets None, not an exception.
    assert result is None
    # ... but the gap is now observable, not silent.
    warnings = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert str(missing_zip) in message
    assert "per-Kreis" in message


def test_load_zgb_kreise_returns_none_when_archive_missing(monkeypatch, tmp_path, caplog):
    """The downstream Kreis-polygon loader must propagate the tolerant None
    rather than raising, so run_metrics.metrics_matsim can skip the per-Kreis
    panel and still render the rest of the dashboard."""
    missing_zip = tmp_path / "vg250-ew_12-31.utm32s.gpkg.ebenen.zip"
    monkeypatch.setattr(spatial, "VG250_ZIP", missing_zip)
    monkeypatch.setattr(spatial, "VG250_CACHE", tmp_path / "cache" / "DE_VG250.gpkg")

    with caplog.at_level(logging.WARNING, logger="braunschweig.analysis.spatial"):
        result = spatial_metrics._load_zgb_kreise()

    assert result is None
    assert any(r.levelno >= logging.WARNING for r in caplog.records)


def test_dashboard_and_analysis_paths_share_one_loader(monkeypatch):
    """Patching the single shared loader must affect both the dashboard's
    _ensure_vg250 and the analysis path's load_vg250_layer -- proving there
    is one implementation behind both callers, not two independent ones."""
    calls: list[bool] = []

    def fake_resolve(*, strict: bool):
        calls.append(strict)
        return None

    monkeypatch.setattr(spatial, "_resolve_vg250_gpkg", fake_resolve)

    # Dashboard (tolerant) caller.
    assert spatial_metrics._ensure_vg250() is None
    # Analysis (tolerant-mode) caller, same underlying function.
    assert spatial.load_vg250_layer("vg250_gem", strict=False) is None

    assert calls == [False, False]


def test_vg250_zip_and_cache_constants_are_shared_not_duplicated():
    """VG250_ZIP / VG250_CACHE must be the same objects on both modules --
    re-exports of the single owner, not two independently-derived paths that
    could silently drift apart after a data-layout change."""
    assert spatial_metrics.VG250_ZIP == spatial.VG250_ZIP
    assert spatial_metrics.VG250_CACHE == spatial.VG250_CACHE
