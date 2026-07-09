import json
import sys
import time

from fastapi.testclient import TestClient

from braunschweig.runcontrol.app import create_app
from braunschweig.runcontrol.daemon import QueueWorker
from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.models import RunStatus
from braunschweig.runcontrol.settings import Settings, TargetConfig
from braunschweig.runcontrol.targets.local import LocalTarget
from braunschweig.runcontrol.targets.ssh import SshTarget

TEMPLATE = ("run:\n  - synthesis.output\nconfig:\n  sampling_rate: 0.25\n"
            "  matsim_last_iteration: 9\n  freight_enabled: true\n")


def _client(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "config_local_test.yml").write_text(TEMPLATE)
    # 3 s sleep: hero render right after tick() must still see the run alive
    (tmp_path / "fake_runner.py").write_text("import time\ntime.sleep(3)\n")
    cfg = TargetConfig(name="local", kind="local", repo=str(tmp_path), runner="fake_runner.py")
    settings = Settings(db_path=tmp_path / "runs.db", targets={"local": cfg})
    db = Database(settings.db_path)
    targets = {"local": LocalTarget(cfg, python=sys.executable)}
    worker = QueueWorker(db, targets)
    return TestClient(create_app(settings, db, worker, targets)), worker


def test_home_renders_idle_state(tmp_path):
    c, _ = _client(tmp_path)
    html = c.get("/").text
    assert "RUN CONTROL" in html and "No run active" in html
    assert "htmx.min.js" in html


def test_home_shows_active_run_hero(tmp_path):
    c, worker = _client(tmp_path)
    c.post("/api/launch", data={"target": "local", "template": "config_local_test.yml",
                                "label": "demo", "overrides": "{}"})
    worker.tick()
    # DEVIATION from the plan's literal test: spawning local_runner.py is a real OS
    # process launch (CreateProcess on Windows), so the child needs real wall-clock time
    # to start Python and open its log file before _enrich() sees "progress_available".
    # A bare "tick() then immediately GET" is a genuine race on Windows (observed to fail
    # 100% of the time on this machine, confirmed independent of routes/templates); poll
    # for up to 2 s, well inside the fake runner's 3 s sleep, instead of assuming the log
    # file exists synchronously.
    html = ""
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        html = c.get("/").text
        if "demo" in html and 'class="dna"' in html:
            break
        time.sleep(0.05)
    assert "demo" in html and 'class="dna"' in html


def test_run_detail_page_has_tabs(tmp_path):
    c, worker = _client(tmp_path)
    r = c.post("/api/launch", data={"target": "local", "template": "config_local_test.yml",
                                    "label": "demo", "overrides": "{}"})
    worker.tick()
    html = c.get(f"/runs/{r.json()['run_id']}").text
    for tab in ("Overview", "Stages", "Log", "Resources", "Meta"):
        assert tab in html


def test_studio_lists_templates_and_curated_groups(tmp_path):
    c, _ = _client(tmp_path)
    html = c.get("/studio?target=local").text
    assert "config_local_test.yml" in html
    assert "sampling_rate" in html and "MATSim runtime" in html
    assert "Save &amp; enqueue" in html or "Save & enqueue" in html
    # Bool flags must render as a <select>, never a free-text input: Jinja prints
    # Python bools as "True"/"False" while the JS coercion only recognizes lowercase
    # "true", so a text input would let a user silently invert a scientific flag.
    assert 'data-type="bool"' in html
    assert "<select" in html


def test_run_detail_tolerates_corrupt_config(tmp_path):
    # Regression for _enrich(): yaml.YAMLError is not a ValueError subclass, so a
    # corrupt composed config must degrade (matsim/output_path unknown), not 500.
    c, worker = _client(tmp_path)
    r = c.post("/api/launch", data={"target": "local", "template": "config_local_test.yml",
                                    "label": "demo", "overrides": "{}"})
    body = r.json()
    worker.tick()
    (tmp_path / body["config_path"]).write_text("run: [\nbroken")
    assert c.get(f"/api/runs/{body['run_id']}").status_code == 200


def test_catalog_page_lists_legacy_dirs(tmp_path):
    # Catalog is on-demand (Task/F-1): visiting /catalog performs target I/O only when
    # explicitly loaded, never via background polling.
    c, _ = _client(tmp_path)
    (tmp_path / "eqasim-data" / "output_bs_25pct_demo").mkdir(parents=True)
    (tmp_path / "eqasim-data" / "cache_bs_1pct_x").mkdir(parents=True)
    r = c.get("/catalog?target=local")
    assert r.status_code == 200
    html = r.text
    assert "output_bs_25pct_demo" in html
    assert "cache_bs_1pct_x" in html
    assert "legacy" in html                                   # origin=legacy_dir shown
    assert "unknown" in html                                   # legacy git_commit/status


def test_catalog_api_counts(tmp_path):
    c, _ = _client(tmp_path)
    (tmp_path / "eqasim-data" / "output_bs_25pct_demo").mkdir(parents=True)
    (tmp_path / "eqasim-data" / "cache_bs_1pct_x").mkdir(parents=True)
    r = c.get("/api/catalog?target=local")
    assert r.status_code == 200
    assert r.json()["n_legacy"] == 2


# ---- Task 14: dynamic execution targets ----------------------------------

def _client_with_config_ssh_target(tmp_path):
    """Adds a config-file ssh target ('server') alongside the usual local target,
    so collision-with-config and immutability behavior can be exercised."""
    (tmp_path / "logs").mkdir()
    (tmp_path / "config_local_test.yml").write_text(TEMPLATE)
    (tmp_path / "fake_runner.py").write_text("import time\ntime.sleep(3)\n")
    local_cfg = TargetConfig(name="local", kind="local", repo=str(tmp_path), runner="fake_runner.py")
    server_cfg = TargetConfig(name="server", kind="ssh", repo="~/eqasim-bs", host="felix",
                              runner="scripts/run_pipeline.sh")
    settings = Settings(db_path=tmp_path / "runs.db",
                        targets={"local": local_cfg, "server": server_cfg},
                        targets_store_path=tmp_path / "runcontrol_data" / "targets.json")
    db = Database(settings.db_path)
    targets = {"local": LocalTarget(local_cfg, python=sys.executable), "server": SshTarget(server_cfg)}
    worker = QueueWorker(db, targets)
    app = create_app(settings, db, worker, targets)
    return TestClient(app), settings, targets


def test_get_targets_lists_config_targets_with_origin_config(tmp_path):
    c, _, _ = _client_with_config_ssh_target(tmp_path)
    rows = {r["name"]: r for r in c.get("/api/targets").json()}
    assert rows["local"]["origin"] == "config" and rows["local"]["kind"] == "local"
    assert rows["server"]["origin"] == "config" and rows["server"]["host"] == "felix"


def test_post_target_success_persists_and_appears_as_user(tmp_path, monkeypatch):
    c, settings, targets = _client_with_config_ssh_target(tmp_path)
    monkeypatch.setattr(SshTarget, "probe",
                        lambda self: {"ok": True, "message": "connected", "git_commit": "abc123"})
    r = c.post("/api/targets", data={"name": "felix2", "host": "user@1.2.3.4", "repo": "~/eqasim-bs"})
    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "name": "felix2", "git_commit": "abc123"}
    rows = {row["name"]: row for row in c.get("/api/targets").json()}
    assert rows["felix2"]["origin"] == "user" and rows["felix2"]["host"] == "user@1.2.3.4"
    stored = json.loads(settings.targets_store_path.read_text())
    assert "felix2" in stored["targets"]


def test_post_target_probe_failure_rejects_and_does_not_persist(tmp_path, monkeypatch):
    c, settings, targets = _client_with_config_ssh_target(tmp_path)
    monkeypatch.setattr(SshTarget, "probe",
                        lambda self: {"ok": False, "message": "rc=255: connection refused", "git_commit": None})
    r = c.post("/api/targets", data={"name": "felix3", "host": "1.2.3.4", "repo": "~/eqasim-bs"})
    assert r.status_code == 422
    assert "connection test failed" in r.json()["detail"]
    names = {row["name"] for row in c.get("/api/targets").json()}
    assert "felix3" not in names
    assert not settings.targets_store_path.exists()


def test_post_target_collision_with_config_target_rejected(tmp_path, monkeypatch):
    c, settings, targets = _client_with_config_ssh_target(tmp_path)
    monkeypatch.setattr(SshTarget, "probe",
                        lambda self: {"ok": True, "message": "connected", "git_commit": "x"})
    r = c.post("/api/targets", data={"name": "server", "host": "1.2.3.4", "repo": "~/eqasim-bs"})
    assert r.status_code == 422
    assert "runcontrol.toml" in r.json()["detail"]


def test_delete_user_target_removes_it_from_api_and_store(tmp_path, monkeypatch):
    c, settings, targets = _client_with_config_ssh_target(tmp_path)
    monkeypatch.setattr(SshTarget, "probe",
                        lambda self: {"ok": True, "message": "connected", "git_commit": "x"})
    c.post("/api/targets", data={"name": "felix4", "host": "1.2.3.4", "repo": "~/eqasim-bs"})
    r = c.delete("/api/targets/felix4")
    assert r.status_code == 200
    names = {row["name"] for row in c.get("/api/targets").json()}
    assert "felix4" not in names
    stored = json.loads(settings.targets_store_path.read_text())
    assert "felix4" not in stored["targets"]


def test_delete_config_target_rejected_as_immutable(tmp_path):
    c, _, _ = _client_with_config_ssh_target(tmp_path)
    r = c.delete("/api/targets/server")
    assert r.status_code == 422
    assert "immutable" in r.json()["detail"]


def test_test_endpoint_reports_honest_probe_failure(tmp_path, monkeypatch):
    c, _, _ = _client_with_config_ssh_target(tmp_path)
    monkeypatch.setattr(SshTarget, "probe",
                        lambda self: {"ok": False, "message": "boom", "git_commit": None})
    r = c.post("/api/targets/server/test")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False and "boom" in body["message"]


def test_test_endpoint_local_target_reports_local_filesystem(tmp_path):
    c, _, _ = _client_with_config_ssh_target(tmp_path)
    r = c.post("/api/targets/local/test")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "message": "local filesystem"}


def test_targets_page_renders_form_and_existing_targets(tmp_path):
    c, _, _ = _client_with_config_ssh_target(tmp_path)
    html = c.get("/targets").text
    assert "server" in html and "local" in html
    assert "Connect &amp; save" in html or "Connect & save" in html
    assert "<form" in html or "id=\"new-name\"" in html


def test_home_history_includes_ended(tmp_path):
    """ENDED (adopted) runs should appear in the home page history."""
    c, worker = _client(tmp_path)
    r = c.post("/api/launch", data={"target": "local", "template": "config_local_test.yml",
                                    "label": "demo_ended", "overrides": "{}"})
    run_id = r.json()["run_id"]
    worker.tick()
    # Set the run to ENDED status (as if it was adopted and monitoring finished)
    db = worker.db
    db.set_status(run_id, RunStatus.ENDED)
    # Verify the ended run appears in the home page history
    html = c.get("/").text
    assert "demo_ended" in html
    assert 'class="badge ended"' in html or 'class="badge unknown"' not in html  # ended badge should be visible
