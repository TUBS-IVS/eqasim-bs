from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.models import LaunchHandle, RunManifest, RunSpec, RunStatus


def _spec(i=1):
    return RunSpec(run_id=f"r{i}", target="local", label=f"run{i}", config_path=f"config_{i}.yml")


def test_insert_get_and_status_transition(tmp_path):
    db = Database(tmp_path / "runs.db")
    db.insert_run(_spec(), RunStatus.QUEUED)
    run = db.get_run("r1")
    assert run["status"] == "queued" and run["label"] == "run1"
    db.set_status("r1", RunStatus.FAILED, exit_code=137)
    run = db.get_run("r1")
    assert run["status"] == "failed" and run["exit_code"] == 137
    assert db.get_run("missing") is None


def test_attach_handle_persists_session_and_log(tmp_path):
    db = Database(tmp_path / "runs.db")
    db.insert_run(_spec(), RunStatus.LAUNCHING)
    h = LaunchHandle(run_id="r1", tmux_session="rc_r1", pid=None,
                     log_path="logs/rc_r1.log", exit_marker_path="logs/rc_r1.exit")
    db.attach_handle("r1", h)
    run = db.get_run("r1")
    assert run["tmux_session"] == "rc_r1" and run["log_path"] == "logs/rc_r1.log"


def test_queue_fifo_and_reorder(tmp_path):
    db = Database(tmp_path / "runs.db")
    for i in (1, 2, 3):
        db.insert_run(_spec(i), RunStatus.QUEUED)
        db.enqueue(f"r{i}")
    assert db.queue_ids() == ["r1", "r2", "r3"]
    db.reorder_queue(["r3", "r1", "r2"])
    assert db.dequeue_next() == "r3"
    assert db.queue_ids() == ["r1", "r2"]
    db.remove_from_queue("r2")
    assert db.queue_ids() == ["r1"]


def test_events_are_appended_in_order(tmp_path):
    db = Database(tmp_path / "runs.db")
    db.insert_run(_spec(), RunStatus.QUEUED)
    db.add_event("r1", "status", "queued -> launching")
    db.add_event("r1", "error", "boom")
    kinds = [e["kind"] for e in db.events("r1")]
    assert kinds == ["status", "error"]


def test_manifest_json_roundtrip():
    m = RunManifest(run_id="r1", target="server", label="l", config_path="c.yml",
                    git_commit="abc1234", started_at_iso="2026-07-08T10:00:00",
                    tmux_session="rc_r1", pid=None,
                    log_path="logs/rc_r1.log", exit_marker_path="logs/rc_r1.exit")
    assert RunManifest.from_json(m.to_json()) == m
