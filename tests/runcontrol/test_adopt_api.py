import sys, json, pathlib
from fastapi.testclient import TestClient
from braunschweig.runcontrol.app import create_app
from braunschweig.runcontrol.daemon import QueueWorker
from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.models import RunStatus
from braunschweig.runcontrol.settings import Settings, TargetConfig
from braunschweig.runcontrol.targets.local import LocalTarget


def _client(tmp_path):
    data = tmp_path / "eqasim-data"
    (data / "cache_bs_25pct").mkdir(parents=True)
    (data / "cache_bs_25pct" / "pipeline.json").write_text("{}")
    (tmp_path / "logs").mkdir()
    cfg = TargetConfig(name="local", kind="local", repo=str(tmp_path), runner="scripts/run_synpp.py")
    settings = Settings(db_path=tmp_path / "runs.db", targets={"local": cfg})
    db = Database(settings.db_path)
    targets = {"local": LocalTarget(cfg, python=sys.executable)}
    return TestClient(create_app(settings, db, QueueWorker(db, targets), targets)), db


def test_adopt_creates_external_running_run(tmp_path):
    c, db = _client(tmp_path)
    r = c.post("/api/catalog/local/cache_bs_25pct/adopt")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["watch_path"].endswith("cache_bs_25pct")
    row = db.get_run(body["run_id"])
    assert row["external"] == 1 and row["status"] == "running"


def test_adopt_appears_as_active_on_status(tmp_path):
    c, _ = _client(tmp_path)
    c.post("/api/catalog/local/cache_bs_25pct/adopt")
    s = c.get("/api/status").json()
    assert s["active_run"] is not None and s["active_run"]["external"] == 1


def test_readopt_active_is_rejected(tmp_path):
    c, _ = _client(tmp_path)
    c.post("/api/catalog/local/cache_bs_25pct/adopt")
    r2 = c.post("/api/catalog/local/cache_bs_25pct/adopt")
    assert r2.status_code == 422 and "already adopted" in r2.json()["detail"]


def test_adopt_rejects_bad_name(tmp_path):
    c, _ = _client(tmp_path)
    assert c.post("/api/catalog/local/x;rm/adopt").status_code == 422


def test_readopt_after_terminal_reactivates(tmp_path):
    c, db = _client(tmp_path)
    body = c.post("/api/catalog/local/cache_bs_25pct/adopt").json()
    run_id = body["run_id"]
    db.set_status(run_id, RunStatus.ENDED)                # simulate a finished adopted run
    r = c.post("/api/catalog/local/cache_bs_25pct/adopt")
    assert r.status_code == 200, r.text                   # re-adopt cleanly, not a 500
    row = db.get_run(run_id)
    assert row["status"] == "running" and row["external"] == 1
    assert row["finished_at"] is None
    assert row["auto_detected"] == 0                      # manual adopt must not masquerade as auto-detected
