from braunschweig.runcontrol.daemon import QueueWorker
from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.models import RunSpec, RunStatus


class FakeTarget:
    kind = "ssh"
    name = "server"

    def __init__(self, dir_mtime):
        self._m = dir_mtime
        self.cfg = type("C", (), {"data_dir": "eqasim-data", "logs_dir": "logs"})()
        self.stopped = []

    def set_mtime(self, m):
        self._m = m

    def listdir(self, path):
        # only the data_dir listing matters; expose one artifact dir
        if path == "eqasim-data":
            return [{"name": "cache_x", "size": 0, "mtime": self._m}] if self._m is not None else []
        return []

    def stop(self, handle):
        self.stopped.append(handle.run_id)


def _worker(tmp_path, target, window=300, clock=None):
    db = Database(tmp_path / "runs.db")
    w = QueueWorker(db, {"server": target})
    w._window_override = window
    w._clock = clock or (lambda: 1000.0)      # daemon wall clock (epoch seconds), injectable
    return w, db


def _adopt(db, watch_mtime, checked_at):
    db.insert_external_run("ext1", "server", "cache_x", "unknown", None,
                           "eqasim-data/cache_x", watch_mtime, checked_at)


def test_external_stays_running_while_dir_advances(tmp_path):
    t = FakeTarget(dir_mtime=2000.0)
    w, db = _worker(tmp_path, t, clock=lambda: 5000.0)
    _adopt(db, watch_mtime=1000.0, checked_at="2026-07-09T10:00:00")
    w.tick()   # dir mtime 2000 > stored 1000 -> advanced -> running, watch updated
    assert db.get_run("ext1")["status"] == RunStatus.RUNNING.value
    assert db.get_run("ext1")["watch_mtime"] == 2000.0


def test_external_ends_when_dir_stale_beyond_window(tmp_path):
    t = FakeTarget(dir_mtime=2000.0)
    # clock advances so that now - watch_checked_at(as epoch stored via _clock) > window
    calls = [10_000.0, 10_000.0, 10_999.0]   # settle reads clock; make now far ahead
    w, db = _worker(tmp_path, t, window=300, clock=lambda: 10_999.0)
    # seed as already-checked at epoch 10_000 with mtime == current dir mtime (no advance)
    _adopt(db, watch_mtime=2000.0, checked_at=w._iso(10_000.0))
    w.tick()   # dir mtime unchanged (2000==2000) and 10_999-10_000=999 > 300 -> ENDED
    assert db.get_run("ext1")["status"] == RunStatus.ENDED.value
    assert any(e["kind"] in ("status", "warning") for e in db.events("ext1"))


def test_external_running_blocks_queue(tmp_path):
    t = FakeTarget(dir_mtime=2000.0)
    w, db = _worker(tmp_path, t, clock=lambda: 2000.0)
    _adopt(db, watch_mtime=1000.0, checked_at=w._iso(1999.0))
    # a normal queued run on the same target
    db.insert_run(RunSpec("q1", "server", "q", "c.yml"), RunStatus.QUEUED)
    db.enqueue("q1")
    w.tick()
    assert db.get_run("ext1")["status"] == RunStatus.RUNNING.value
    assert db.get_run("q1")["status"] == RunStatus.QUEUED.value   # blocked by the adopted run


def test_stop_external_never_kills_and_ends(tmp_path):
    t = FakeTarget(dir_mtime=2000.0)
    w, db = _worker(tmp_path, t, clock=lambda: 2000.0)
    _adopt(db, watch_mtime=1000.0, checked_at=w._iso(1999.0))
    w.stop_run("ext1")
    assert t.stopped == []                                   # never touched the process
    assert db.get_run("ext1")["status"] == RunStatus.ENDED.value


def test_external_dir_gone_ends(tmp_path):
    t = FakeTarget(dir_mtime=None)                           # listdir no longer lists it
    w, db = _worker(tmp_path, t, clock=lambda: 10_999.0)
    _adopt(db, watch_mtime=2000.0, checked_at=w._iso(10_000.0))
    w.tick()
    assert db.get_run("ext1")["status"] == RunStatus.ENDED.value
