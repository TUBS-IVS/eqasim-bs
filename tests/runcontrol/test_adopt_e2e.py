import sys
from fastapi.testclient import TestClient
from braunschweig.runcontrol.app import create_app
from braunschweig.runcontrol.daemon import QueueWorker
from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.settings import Settings, TargetConfig
from braunschweig.runcontrol.targets.local import LocalTarget


def test_adopt_lifecycle(tmp_path):
    data = tmp_path / "eqasim-data"
    (data / "cache_bs_25pct").mkdir(parents=True)
    (data / "cache_bs_25pct" / "pipeline.json").write_text("{}")
    (tmp_path / "logs").mkdir()
    cfg = TargetConfig(name="local", kind="local", repo=str(tmp_path), runner="scripts/run_synpp.py")
    settings = Settings(db_path=tmp_path / "runs.db", targets={"local": cfg}, adopt_alive_window_s=300)
    db = Database(settings.db_path)
    targets = {"local": LocalTarget(cfg, python=sys.executable)}
    worker = QueueWorker(db, targets)
    app = create_app(settings, db, worker, targets)
    c = TestClient(app)

    body = c.post("/api/catalog/local/cache_bs_25pct/adopt").json()
    rid = body["run_id"]
    assert db.get_run(rid)["status"] == "running"

    # Freeze the daemon clock far past the window with no dir change -> ENDED.
    base = db.get_run(rid)["watch_mtime"]
    worker._clock = lambda: 2_000_000_000.0
    worker.tick()
    assert db.get_run(rid)["status"] == "ended"
