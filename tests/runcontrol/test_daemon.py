from braunschweig.runcontrol.daemon import QueueWorker
from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.models import LaunchHandle, RunManifest, RunSpec, RunStatus


class FakeTarget:
    kind = "local"
    name = "local"

    def __init__(self):
        self.alive = {}
        self.exit_codes = {}
        self.launched = []
        self.stopped = []
        self.manifests = {}          # relpath -> RunManifest JSON text
        self.alive_raises = set()    # run_ids whose is_alive raises
        self.manifest_glob_raises = False

    def launch(self, spec):
        self.launched.append(spec.run_id)
        self.alive[spec.run_id] = True
        return LaunchHandle(run_id=spec.run_id, tmux_session=None, pid=1000,
                            log_path=f"logs/rc_{spec.run_id}.log",
                            exit_marker_path=f"logs/rc_{spec.run_id}.exit")

    def is_alive(self, h):
        if h.run_id in self.alive_raises:
            raise RuntimeError("target unreachable")
        return self.alive.get(h.run_id, False)

    def exit_code(self, h):
        return self.exit_codes.get(h.run_id)

    def stop(self, h):
        self.stopped.append(h.run_id)
        self.alive[h.run_id] = False
        self.exit_codes[h.run_id] = 130

    def manifest_glob(self):
        if self.manifest_glob_raises:
            raise RuntimeError("target unreachable")
        return sorted(self.manifests)

    def exists(self, relpath):
        return relpath in self.manifests

    def read_text(self, relpath, tail_bytes=None):
        return self.manifests[relpath]


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


def test_tick_survives_settle_exception(tmp_path):
    w, db, t = _worker(tmp_path)
    w.submit(RunSpec("r1", "local", "one", "c1.yml"))
    w.submit(RunSpec("r2", "local", "two", "c2.yml"))
    w.tick()                                    # launches r1
    t.alive_raises.add("r1")
    w.tick()                                    # must not raise, must not launch on top of r1
    assert any(e["kind"] == "error" for e in db.events("r1"))
    assert db.get_run("r2")["status"] == "queued"
    assert t.launched == ["r1"]


def test_reconcile_reenqueues_stranded_queued_run(tmp_path):
    db = Database(tmp_path / "runs.db")
    t = FakeTarget()
    # Crash window A: run inserted as QUEUED but the queue row is gone (crash after dequeue).
    db.insert_run(RunSpec("r1", "local", "one", "c1.yml"), RunStatus.QUEUED)
    w = QueueWorker(db, {"local": t})
    w.reconcile()
    assert db.queue_ids() == ["r1"]
    assert any(e["kind"] == "warning" for e in db.events("r1"))


def test_reconcile_recovers_handle_from_manifest(tmp_path):
    db = Database(tmp_path / "runs.db")
    t = FakeTarget()
    # Crash window B: LAUNCHING persisted, handle not; the on-host manifest is the durable truth.
    db.insert_run(RunSpec("r1", "local", "one", "c1.yml"), RunStatus.LAUNCHING)
    manifest = RunManifest(run_id="r1", target="local", label="one", config_path="c1.yml",
                           git_commit="unknown", started_at_iso="2026-07-08T00:00:00+00:00",
                           tmux_session=None, pid=1000,
                           log_path="logs/rc_r1.log", exit_marker_path="logs/rc_r1.exit")
    t.manifests["logs/rc_r1.manifest.json"] = manifest.to_json()
    t.alive["r1"] = True
    w = QueueWorker(db, {"local": t})
    w.reconcile()
    row = db.get_run("r1")
    assert row["log_path"] == "logs/rc_r1.log"
    assert row["status"] == "running"


def test_reconcile_unknown_when_no_handle_no_manifest(tmp_path):
    db = Database(tmp_path / "runs.db")
    t = FakeTarget()
    db.insert_run(RunSpec("r1", "local", "one", "c1.yml"), RunStatus.LAUNCHING)
    db.insert_run(RunSpec("r2", "local", "two", "c2.yml"), RunStatus.QUEUED)
    db.enqueue("r2")
    w = QueueWorker(db, {"local": t})
    w.reconcile()
    assert db.get_run("r1")["status"] == "unknown"
    assert any(e["kind"] == "warning" for e in db.events("r1"))
    w.tick()                                    # unresolved ghost blocks the queue
    assert db.get_run("r2")["status"] == "queued"
    assert t.launched == []


def test_stop_unknown_run_without_handle_unblocks_queue(tmp_path):
    db = Database(tmp_path / "runs.db")
    t = FakeTarget()
    db.insert_run(RunSpec("r1", "local", "one", "c1.yml"), RunStatus.LAUNCHING)
    db.insert_run(RunSpec("r2", "local", "two", "c2.yml"), RunStatus.QUEUED)
    db.enqueue("r2")
    w = QueueWorker(db, {"local": t})
    w.reconcile()                               # r1 -> unknown (no handle, no manifest)
    w.stop_run("r1")                            # human resolves the ghost
    assert db.get_run("r1")["status"] == "stopped"
    assert t.stopped == []                      # target never called with a null handle
    w.tick()
    assert t.launched == ["r2"]
    assert db.get_run("r2")["status"] == "running"


def test_reconcile_survives_recovery_exception(tmp_path):
    db = Database(tmp_path / "runs.db")
    t = FakeTarget()
    db.insert_run(RunSpec("r1", "local", "one", "c1.yml"), RunStatus.LAUNCHING)
    t.manifest_glob_raises = True
    w = QueueWorker(db, {"local": t})
    w.reconcile()                               # must not raise
    assert any(e["kind"] == "error" for e in db.events("r1"))


def test_reconcile_purges_stale_queue_entries(tmp_path):
    db = Database(tmp_path / "runs.db")
    t = FakeTarget()
    db.insert_run(RunSpec("r1", "local", "one", "c1.yml"), RunStatus.RUNNING)
    db.attach_handle("r1", LaunchHandle("r1", None, 1000, "logs/rc_r1.log", "logs/rc_r1.exit"))
    db.enqueue("r1")                            # stale queue entry for an already-running run
    t.alive["r1"] = True
    w = QueueWorker(db, {"local": t})
    w.reconcile()
    assert "r1" not in db.queue_ids()
    assert db.get_run("r1")["status"] == "running"
