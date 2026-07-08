import sys
import time

from braunschweig.runcontrol.models import RunSpec
from braunschweig.runcontrol.settings import TargetConfig
from braunschweig.runcontrol.targets.local import LocalTarget

FAKE_RUNNER = """
import sys, time
print("Executing stage demo")
time.sleep(0.3)
sys.exit(0)
"""


def _target(tmp_path, runner_body=FAKE_RUNNER):
    (tmp_path / "logs").mkdir()
    runner = tmp_path / "fake_runner.py"
    runner.write_text(runner_body)
    cfg = TargetConfig(name="local", kind="local", repo=str(tmp_path), runner="fake_runner.py")
    # short escalate window so the stop test does not wait 30 s on Windows
    return LocalTarget(cfg, python=sys.executable, stop_escalate_seconds=1.0)


def _wait_dead(t, h, timeout=10.0):
    deadline = time.time() + timeout
    while t.is_alive(h) and time.time() < deadline:
        time.sleep(0.05)
    assert not t.is_alive(h), "runner did not finish in time"


def test_launch_runs_detached_writes_log_manifest_and_exit_marker(tmp_path):
    t = _target(tmp_path)
    h = t.launch(RunSpec(run_id="r1", target="local", label="demo", config_path="cfg.yml"))
    assert h.pid is not None and h.tmux_session is None
    _wait_dead(t, h)
    assert t.exit_code(h) == 0
    assert "Executing stage demo" in t.read_text(h.log_path)
    manifests = t.manifest_glob()
    assert any("rc_r1" in m for m in manifests)


def test_exit_code_none_while_alive_and_nonzero_on_failure(tmp_path):
    t = _target(tmp_path, "import sys, time\ntime.sleep(0.2)\nsys.exit(3)\n")
    h = t.launch(RunSpec(run_id="r2", target="local", label="fail", config_path="cfg.yml"))
    assert t.exit_code(h) is None            # marker not written yet
    _wait_dead(t, h)
    assert t.exit_code(h) == 3


def test_stop_terminates_process_group(tmp_path):
    t = _target(tmp_path, "import time\ntime.sleep(60)\n")
    h = t.launch(RunSpec(run_id="r3", target="local", label="long", config_path="cfg.yml"))
    assert t.is_alive(h)
    t.stop(h)
    _wait_dead(t, h)


def test_read_text_tail_and_listdir(tmp_path):
    t = _target(tmp_path)
    (tmp_path / "big.txt").write_text("A" * 100 + "TAIL")
    assert t.read_text("big.txt", tail_bytes=4) == "TAIL"
    names = [e["name"] for e in t.listdir(".")]
    assert "big.txt" in names and all({"name", "size", "mtime"} <= set(e) for e in t.listdir("."))
