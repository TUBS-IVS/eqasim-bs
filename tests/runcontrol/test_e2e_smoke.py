"""End-to-end: submit -> launch (real detached process) -> running -> done,
exercised through QueueWorker + LocalTarget + Database together."""
import sys
import time

from braunschweig.runcontrol.daemon import QueueWorker
from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.models import RunSpec
from braunschweig.runcontrol.settings import TargetConfig
from braunschweig.runcontrol.targets.local import LocalTarget

RUNNER = "import sys\nprint('Executing stage demo')\nprint('Finished running demo.')\nsys.exit(0)\n"


def test_full_lifecycle(tmp_path):
    (tmp_path / "logs").mkdir()
    (tmp_path / "fake_runner.py").write_text(RUNNER)
    cfg = TargetConfig(name="local", kind="local", repo=str(tmp_path), runner="fake_runner.py")
    db = Database(tmp_path / "runs.db")
    worker = QueueWorker(db, {"local": LocalTarget(cfg, python=sys.executable)})

    worker.submit(RunSpec("smoke1", "local", "smoke", "cfg.yml"))
    worker.tick()
    assert db.get_run("smoke1")["status"] in ("running", "done")

    deadline = time.time() + 15
    while db.get_run("smoke1")["status"] == "running" and time.time() < deadline:
        time.sleep(0.2)
        worker.tick()
    row = db.get_run("smoke1")
    assert row["status"] == "done" and row["exit_code"] == 0
    # the artifacts a human would look for actually exist
    assert (tmp_path / "logs" / "rc_smoke1.log").exists()
    assert (tmp_path / "logs" / "rc_smoke1.manifest.json").exists()
    assert (tmp_path / "logs" / "rc_smoke1.exit").read_text().strip() == "0"
