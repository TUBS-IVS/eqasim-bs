from fnmatch import fnmatch
from braunschweig.runcontrol.daemon import QueueWorker
from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.models import RunSpec, RunStatus


class FakeTarget:
    kind = "ssh"
    name = "server"

    def __init__(self, files_by_dir):
        # files_by_dir: {reldir: [(mtime, relpath), ...]}
        self.files_by_dir = files_by_dir
        self.cfg = type("C", (), {"data_dir": "eqasim-data", "logs_dir": "logs"})()
        self.stopped = []

    def newest_files(self, reldir, maxdepth=4, limit=200):
        return sorted(self.files_by_dir.get(reldir, []), key=lambda x: x[0], reverse=True)[:limit]

    def stop(self, h):
        self.stopped.append(h.run_id)


def _worker(tmp_path, target, clock=lambda: 5000.0, globs=None, interval=60, window=1800):
    db = Database(tmp_path / "runs.db")
    w = QueueWorker(db, {"server": target})
    w._clock = clock
    w._active_run_globs = globs or ["output_*", "cache_*", "popsim_work_*"]
    w._autodetect_interval = interval
    w._settings_window = window
    return w, db


def test_autodetect_creates_external_for_fresh_glob_root(tmp_path):
    # newest file overall at 5000; popsim root fresh (4990), a stale cache root (100)
    t = FakeTarget({"eqasim-data": [
        (5000.0, "popsim_work_allfeat_opt/batch_8/output/populationsim.log"),
        (4990.0, "popsim_work_allfeat_opt/batch_1/output/mem.csv"),
        (100.0, "cache_bs_old/pipeline.json"),
    ]})
    w, db = _worker(tmp_path, t, window=1800)
    w.autodetect("server")
    r = db.get_run("popsim_work_allfeat_opt")
    assert r is not None and r["external"] == 1 and r["auto_detected"] == 1
    assert r["status"] == RunStatus.RUNNING.value
    assert db.get_run("cache_bs_old") is None       # stale (5000-100 > 1800) -> not detected


def test_autodetect_skips_non_glob_roots(tmp_path):
    t = FakeTarget({"eqasim-data": [(5000.0, "random_scratch/x.txt")]})
    w, db = _worker(tmp_path, t)
    w.autodetect("server")
    assert db.get_run("random_scratch") is None


def test_autodetect_no_duplicate_when_active(tmp_path):
    t = FakeTarget({"eqasim-data": [(5000.0, "popsim_work_x/b/f.log")]})
    w, db = _worker(tmp_path, t)
    w.autodetect("server")
    w._last_autodetect = {}                          # bypass throttle to force a second scan
    w.autodetect("server")
    rows = [r for r in db.list_runs() if r["run_id"] == "popsim_work_x"]
    assert len(rows) == 1                            # not duplicated


def test_autodetect_throttled(tmp_path):
    calls = {"n": 0}
    class CountTarget(FakeTarget):
        def newest_files(self, reldir, maxdepth=4, limit=200):
            calls["n"] += 1
            return super().newest_files(reldir, maxdepth, limit)
    t = CountTarget({"eqasim-data": [(5000.0, "cache_x/pipeline.json")]})
    w, db = _worker(tmp_path, t, interval=60)
    w.autodetect("server"); w.autodetect("server")   # second within interval -> skipped
    assert calls["n"] == 1


def test_depth_aware_liveness_alive_on_deep_write(tmp_path):
    # watch dir top level static; a deep descendant advances -> stays RUNNING
    t = FakeTarget({"eqasim-data/popsim_work_x": [(6000.0, "batch_3/output/mem.csv")]})
    w, db = _worker(tmp_path, t, clock=lambda: 9999.0, window=1800)
    db.insert_external_run("popsim_work_x", "server", "popsim_work_x", "unknown", None,
                           "eqasim-data/popsim_work_x", 5000.0, w._iso(1000.0), auto_detected=True)
    w.tick()   # deep descendant 6000 > stored 5000 -> advance -> RUNNING
    assert db.get_run("popsim_work_x")["status"] == RunStatus.RUNNING.value
    assert db.get_run("popsim_work_x")["watch_mtime"] == 6000.0


def test_autodetect_never_reactivates_launched_run(tmp_path):
    # A finished daemon-launched (external=0) run happens to share a glob-matching
    # dir name; a fresh file under that dir must NOT flip its honest terminal state.
    t = FakeTarget({"eqasim-data": [(5000.0, "cache_bs_x/pipeline.json")]})
    w, db = _worker(tmp_path, t, window=1800)
    db.insert_run(RunSpec("cache_bs_x", "server", "cache_bs_x", "c.yml"), RunStatus.QUEUED)
    db.set_status("cache_bs_x", RunStatus.DONE, exit_code=0)
    w.autodetect("server")
    r = db.get_run("cache_bs_x")
    assert r["status"] == RunStatus.DONE.value       # terminal state preserved
    assert r["external"] == 0                         # never flipped to monitor-only external
    assert r["exit_code"] == 0                        # honest exit code preserved


def test_autodetect_reactivates_terminal_external_run(tmp_path):
    # An ENDED EXTERNAL run with the same name IS re-adopted to RUNNING when fresh
    # activity reappears (its terminal state carried no real exit code to protect).
    t = FakeTarget({"eqasim-data": [(5000.0, "cache_bs_x/pipeline.json")]})
    w, db = _worker(tmp_path, t, window=1800)
    db.insert_external_run("cache_bs_x", "server", "cache_bs_x", "unknown", None,
                           "eqasim-data/cache_bs_x", 1.0, w._iso(0.0))
    db.set_status("cache_bs_x", RunStatus.ENDED)
    w.autodetect("server")
    r = db.get_run("cache_bs_x")
    assert r["status"] == RunStatus.RUNNING.value    # re-adopted
    assert r["external"] == 1
    assert r["watch_mtime"] == 5000.0
