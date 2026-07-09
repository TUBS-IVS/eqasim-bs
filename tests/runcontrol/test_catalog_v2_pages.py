import json, sys, pathlib
from fastapi.testclient import TestClient
from braunschweig.runcontrol.app import create_app
from braunschweig.runcontrol.daemon import QueueWorker
from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.settings import Settings, TargetConfig
from braunschweig.runcontrol.targets.local import LocalTarget


def _pipeline(stages):
    return json.dumps({f"{s}__{i:032x}": {"config": c, "updated": u, "dependencies": [],
            "info": {}, "module_hash": "h"} for i, (s, u, c) in enumerate(stages)})


def _client(tmp_path):
    data = tmp_path / "eqasim-data"
    (data / "cache_bs_25pct").mkdir(parents=True)
    (data / "cache_bs_25pct" / "pipeline.json").write_text(
        _pipeline([("a", 1000.0, {"sampling_rate": 0.25, "freight_enabled": True})]))
    (tmp_path / "logs").mkdir()
    cfg = TargetConfig(name="local", kind="local", repo=str(tmp_path), runner="scripts/run_synpp.py")
    settings = Settings(db_path=tmp_path / "runs.db", targets={"local": cfg})
    db = Database(settings.db_path)
    targets = {"local": LocalTarget(cfg, python=sys.executable)}
    return TestClient(create_app(settings, db, QueueWorker(db, targets), targets))


def test_catalog_page_has_sort_filter_and_checkboxes(tmp_path):
    c = _client(tmp_path)
    html = c.get("/catalog?target=local").text
    assert "cache_bs_25pct" in html
    assert 'data-sort' in html or 'onclick="sortTable' in html   # sortable headers
    assert 'Compare configs' in html
    assert 'Enrich all' in html


def test_details_partial_renders_effective_config_partial_label(tmp_path):
    c = _client(tmp_path)
    html = c.get("/catalog/local/cache_bs_25pct/details").text
    assert "partial" in html.lower()
    assert "freight_enabled" in html
    assert "Stages" in html or "timeline" in html.lower()


def test_diff_page_renders_two_columns(tmp_path):
    c = _client(tmp_path)
    import pathlib
    data = pathlib.Path(str(tmp_path)) / "eqasim-data"
    (data / "cache_bs_10pct").mkdir()
    (data / "cache_bs_10pct" / "pipeline.json").write_text(
        _pipeline([("a", 1.0, {"sampling_rate": 0.10, "freight_enabled": True})]))
    html = c.get("/catalog/diff", params={"target": "local", "a": "cache_bs_25pct", "b": "cache_bs_10pct"}).text
    assert "cache_bs_25pct" in html and "cache_bs_10pct" in html
    assert "sampling_rate" in html


def test_logs_page_lists_and_links(tmp_path):
    c = _client(tmp_path)
    import pathlib
    (pathlib.Path(str(tmp_path)) / "logs" / "run_20260101_000000.log").write_text("x")
    html = c.get("/logs?target=local").text
    assert "run_20260101_000000.log" in html
    assert "Logs" in html


def test_logs_page_shows_dates_newest_first(tmp_path):
    c = _client(tmp_path)
    import os
    import re
    import pathlib
    logs_dir = pathlib.Path(str(tmp_path)) / "logs"

    # Create two logs with different mtimes: older first, then newer.
    older_path = logs_dir / "run_20260101_000000.log"
    newer_path = logs_dir / "run_20260630_120000.log"

    older_path.write_text("old content")
    newer_path.write_text("new content")

    # Set explicit mtimes (epoch seconds): older file at 2024-01-01, newer at 2024-06-30.
    older_mtime = 1704067200  # 2024-01-01 00:00:00 UTC
    newer_mtime = 1719748800  # 2024-06-30 12:00:00 UTC
    os.utime(str(older_path), (older_mtime, older_mtime))
    os.utime(str(newer_path), (newer_mtime, newer_mtime))

    html = c.get("/logs?target=local").text

    # Both filenames should appear in the response.
    assert "run_20260101_000000.log" in html
    assert "run_20260630_120000.log" in html

    # A formatted date pattern (YYYY-MM-DD HH:MM UTC) should appear.
    assert re.search(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2} UTC", html), "Date format not found in HTML"

    # Newer file's name should appear before older file's name (newest-first order).
    newer_index = html.index("run_20260630_120000.log")
    older_index = html.index("run_20260101_000000.log")
    assert newer_index < older_index, "Newer log file should appear before older log file (newest-first order)"
