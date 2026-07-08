import json
import sys
import time

from fastapi.testclient import TestClient

from braunschweig.runcontrol.app import create_app
from braunschweig.runcontrol.daemon import QueueWorker
from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.settings import Settings, TargetConfig
from braunschweig.runcontrol.targets.local import LocalTarget

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
