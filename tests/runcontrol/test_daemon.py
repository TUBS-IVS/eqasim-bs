from braunschweig.runcontrol.daemon import QueueWorker
from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.models import LaunchHandle, RunSpec, RunStatus


class FakeTarget:
    kind = "local"
    name = "local"

    def __init__(self):
        self.alive = {}
        self.exit_codes = {}
        self.launched = []
        self.stopped = []

    def launch(self, spec):
        self.launched.append(spec.run_id)
        self.alive[spec.run_id] = True
        return LaunchHandle(run_id=spec.run_id, tmux_session=None, pid=1000,
                            log_path=f"logs/rc_{spec.run_id}.log",
                            exit_marker_path=f"logs/rc_{spec.run_id}.exit")

    def is_alive(self, h):
        return self.alive.get(h.run_id, False)

    def exit_code(self, h):
        return self.exit_codes.get(h.run_id)

    def stop(self, h):
        self.stopped.append(h.run_id)
        self.alive[h.run_id] = False
        self.exit_codes[h.run_id] = 130


def _worker(tmp_path):
    db = Database(tmp_path / "runs.db")
    t = FakeTarget()
    return QueueWorker(db, {"local": t}), db, t


def test_submit_then_tick_launches_first_queued(tmp_path):
    w, db, t = _worker(tmp_path)
    w.submit(RunSpec("r1", "local", "one", "c1.yml"))
    w.submit(RunSpec("r2", "local", "two", "c2.yml"))
    w.tick()
    assert t.launched == ["r1"]
    assert db.get_run("r1")["status"] == "running"
    assert db.get_run("r2")["status"] == "queued"
    w.tick()                                    # r1 still alive -> r2 stays queued
    assert t.launched == ["r1"]


def test_finished_run_gets_exit_code_status_and_queue_advances(tmp_path):
    w, db, t = _worker(tmp_path)
    w.submit(RunSpec("r1", "local", "one", "c1.yml"))
    w.submit(RunSpec("r2", "local", "two", "c2.yml"))
    w.tick()
    t.alive["r1"] = False
    t.exit_codes["r1"] = 0
    w.tick()
    assert db.get_run("r1")["status"] == "done" and db.get_run("r1")["exit_code"] == 0
    assert t.launched == ["r1", "r2"]


def test_nonzero_exit_marks_failed(tmp_path):
    w, db, t = _worker(tmp_path)
    w.submit(RunSpec("r1", "local", "one", "c1.yml"))
    w.tick()
    t.alive["r1"] = False
    t.exit_codes["r1"] = 137
    w.tick()
    assert db.get_run("r1")["status"] == "failed"


def test_dead_without_marker_is_unknown_not_invented(tmp_path):
    w, db, t = _worker(tmp_path)
    w.submit(RunSpec("r1", "local", "one", "c1.yml"))
    w.tick()
    t.alive["r1"] = False                       # died, no exit marker
    w.tick()
    assert db.get_run("r1")["status"] == "unknown"
    assert any(e["kind"] == "warning" for e in db.events("r1"))


def test_reconcile_restores_running_state_after_restart(tmp_path):
    db = Database(tmp_path / "runs.db")
    t = FakeTarget()
    db.insert_run(RunSpec("r1", "local", "one", "c1.yml"), RunStatus.RUNNING)
    db.attach_handle("r1", LaunchHandle("r1", None, 1000, "logs/rc_r1.log", "logs/rc_r1.exit"))
    t.alive["r1"] = True
    w = QueueWorker(db, {"local": t})
    w.reconcile()
    assert db.get_run("r1")["status"] == "running"
    t.alive["r1"] = False
    t.exit_codes["r1"] = 0
    w2 = QueueWorker(db, {"local": t})
    w2.reconcile()
    assert db.get_run("r1")["status"] == "done"


def test_stop_run_marks_stopped(tmp_path):
    w, db, t = _worker(tmp_path)
    w.submit(RunSpec("r1", "local", "one", "c1.yml"))
    w.tick()
    w.stop_run("r1")
    assert t.stopped == ["r1"]
    assert db.get_run("r1")["status"] == "stopped"
