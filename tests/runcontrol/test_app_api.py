import json
import sys
import time

from fastapi.testclient import TestClient

from braunschweig.runcontrol.app import create_app
from braunschweig.runcontrol.daemon import QueueWorker
from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.settings import Settings, TargetConfig
from braunschweig.runcontrol.targets.local import LocalTarget

TEMPLATE = """\
run:
  - synthesis.output
config:
  sampling_rate: 0.25
  matsim_last_iteration: 9
"""


def _client(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "config_local_test.yml").write_text(TEMPLATE)
    # 3 s sleep: long enough that status checks right after tick() still see "running"
    (tmp_path / "fake_runner.py").write_text("import time\ntime.sleep(3)\n")
    cfg = TargetConfig(name="local", kind="local", repo=str(tmp_path), runner="fake_runner.py")
    settings = Settings(db_path=tmp_path / "runs.db", targets={"local": cfg})
    db = Database(settings.db_path)
    targets = {"local": LocalTarget(cfg, python=sys.executable)}
    worker = QueueWorker(db, targets)
    app = create_app(settings, db, worker, targets)
    return TestClient(app), db, worker


def test_templates_listed_and_inspectable(tmp_path):
    c, _, _ = _client(tmp_path)
    names = [t["name"] for t in c.get("/api/templates?target=local").json()]
    assert "config_local_test.yml" in names
    ins = c.get("/api/templates/config_local_test.yml/inspect?target=local").json()
    assert ins["run_list"] == ["synthesis.output"]
    assert any(f["key"] == "sampling_rate" and f["value"] == 0.25
               for f in ins["groups"]["General"])


def test_launch_writes_config_and_queues_run(tmp_path):
    c, db, worker = _client(tmp_path)
    r = c.post("/api/launch", data={
        "target": "local", "template": "config_local_test.yml", "label": "prod",
        "overrides": json.dumps({"matsim_last_iteration": 199})})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["config_path"].startswith("config_gui_prod_")
    assert (tmp_path / body["config_path"]).exists()          # named YAML really on the target
    assert db.get_run(body["run_id"])["status"] == "queued"


def test_launch_rejects_uncurated_override(tmp_path):
    c, _, _ = _client(tmp_path)
    r = c.post("/api/launch", data={
        "target": "local", "template": "config_local_test.yml", "label": "x",
        "overrides": json.dumps({"evil_flag": 1})})
    assert r.status_code == 422
    assert "curated" in r.json()["detail"]


def test_template_inspect_rejects_traversal(tmp_path):
    # Template files live flat in the repo root, so a name containing ".." must be
    # rejected before it ever reaches read_text() (F-3, security). A literal "/" or "\\"
    # segment (e.g. "..%2Fsecret.yml") gets dot-segment-normalized away by the HTTP
    # client before the request is even sent, so it never reaches the route at all
    # (verified: yields a plain 404, not our validator) -- "..secret.yml" is a single
    # path segment that reaches _safe_relname() unmodified and still contains "..".
    c, _, _ = _client(tmp_path)
    r = c.get("/api/templates/..secret.yml/inspect?target=local")
    assert r.status_code == 422
    assert "invalid template name" in r.json()["detail"]


def test_status_and_run_detail_and_log(tmp_path):
    c, db, worker = _client(tmp_path)
    c.post("/api/launch", data={"target": "local", "template": "config_local_test.yml",
                                "label": "one", "overrides": "{}"})
    worker.tick()                                             # launch it
    s = c.get("/api/status").json()
    assert s["active_run"]["label"] == "one"
    run_id = s["active_run"]["run_id"]
    detail = c.get(f"/api/runs/{run_id}").json()
    assert detail["status"] in ("running", "done")
    # The local runner is a freshly spawned detached process; on Windows,
    # CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS creation can take a few hundred
    # ms before the child opens its log file. Poll with a bounded deadline instead
    # of asserting immediately -- this is process-startup latency, not app.py
    # behavior, and the deadline keeps the test deterministic (bounded worst case).
    deadline = time.monotonic() + 2.0
    log = c.get(f"/api/runs/{run_id}/log?tail_bytes=1000")
    while log.status_code == 404 and time.monotonic() < deadline:
        time.sleep(0.1)
        log = c.get(f"/api/runs/{run_id}/log?tail_bytes=1000")
    assert log.status_code == 200


def test_stop_endpoint_and_queue_reorder(tmp_path):
    c, db, worker = _client(tmp_path)
    for label in ("a", "b"):
        c.post("/api/launch", data={"target": "local", "template": "config_local_test.yml",
                                    "label": label, "overrides": "{}"})
    ids = db.queue_ids()
    r = c.post("/api/queue/reorder", json=list(reversed(ids)))
    assert r.status_code == 200 and db.queue_ids() == list(reversed(ids))
    r = c.post(f"/api/runs/{ids[0]}/stop")
    assert r.status_code == 200
    assert db.get_run(ids[0])["status"] == "stopped"
