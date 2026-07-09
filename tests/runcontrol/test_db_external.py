from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.models import RunStatus


def test_insert_external_run_is_running_and_external(tmp_path):
    db = Database(tmp_path / "runs.db")
    db.insert_external_run("ext1", "server", "cache_bs_100pct", "unknown",
                           log_path=None, watch_path="eqasim-data/cache_bs_100pct",
                           watch_mtime=1000.0, watch_checked_at="2026-07-09T10:00:00")
    row = db.get_run("ext1")
    assert row["status"] == RunStatus.RUNNING.value
    assert row["external"] == 1
    assert row["watch_path"] == "eqasim-data/cache_bs_100pct"
    assert row["watch_mtime"] == 1000.0
    assert row["log_path"] is None


def test_update_watch(tmp_path):
    db = Database(tmp_path / "runs.db")
    db.insert_external_run("ext1", "server", "l", "c", None, "d", 1000.0, "2026-07-09T10:00:00")
    db.update_watch("ext1", 1500.0, "2026-07-09T10:05:00")
    row = db.get_run("ext1")
    assert row["watch_mtime"] == 1500.0 and row["watch_checked_at"] == "2026-07-09T10:05:00"


def test_new_columns_default_for_normal_runs(tmp_path):
    from braunschweig.runcontrol.models import RunSpec
    db = Database(tmp_path / "runs.db")
    db.insert_run(RunSpec("r1", "local", "l", "c.yml"), RunStatus.QUEUED)
    row = db.get_run("r1")
    assert row["external"] == 0 and row["watch_path"] is None
