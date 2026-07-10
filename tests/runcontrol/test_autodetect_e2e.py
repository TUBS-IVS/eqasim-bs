import sys
from fastapi.testclient import TestClient
from braunschweig.runcontrol.app import create_app
from braunschweig.runcontrol.daemon import QueueWorker
from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.settings import Settings, TargetConfig
from braunschweig.runcontrol.targets.local import LocalTarget


def test_autodetected_run_shows_detected_badge_on_home(tmp_path):
    base = tmp_path / "eqasim-data" / "popsim_work_allfeat_opt" / "batch_1" / "output"
    base.mkdir(parents=True)
    (base / "populationsim.log").write_text("running")
    (tmp_path / "logs").mkdir()
    cfg = TargetConfig(name="local", kind="local", repo=str(tmp_path), runner="scripts/run_synpp.py")
    settings = Settings(db_path=tmp_path / "runs.db", targets={"local": cfg})
    db = Database(settings.db_path)
    targets = {"local": LocalTarget(cfg, python=sys.executable)}
    worker = QueueWorker(db, targets)
    worker._active_run_globs = settings.active_run_globs
    worker._autodetect_interval = 0        # no throttle in the test
    app = create_app(settings, db, worker, targets)
    c = TestClient(app)

    worker.autodetect("local")             # detect the popsim run
    assert db.get_run("popsim_work_allfeat_opt")["auto_detected"] == 1
    html = c.get("/").text
    assert "popsim_work_allfeat_opt" in html
    assert "detected" in html.lower()


def test_autodetected_run_shows_honesty_note_on_run_detail(tmp_path):
    # The run-detail page must carry the same "not launched by runcontrol" honesty
    # note as the home-page hero, so a user landing directly on /runs/{id} for an
    # auto-detected run is not misled into thinking runcontrol launched it.
    base = tmp_path / "eqasim-data" / "popsim_work_allfeat_opt" / "batch_1" / "output"
    base.mkdir(parents=True)
    (base / "populationsim.log").write_text("running")
    (tmp_path / "logs").mkdir()
    cfg = TargetConfig(name="local", kind="local", repo=str(tmp_path), runner="scripts/run_synpp.py")
    settings = Settings(db_path=tmp_path / "runs.db", targets={"local": cfg})
    db = Database(settings.db_path)
    targets = {"local": LocalTarget(cfg, python=sys.executable)}
    worker = QueueWorker(db, targets)
    worker._active_run_globs = settings.active_run_globs
    worker._autodetect_interval = 0        # no throttle in the test
    app = create_app(settings, db, worker, targets)
    c = TestClient(app)

    worker.autodetect("local")             # detect the popsim run
    html = c.get("/runs/popsim_work_allfeat_opt").text
    assert "auto-detected from filesystem activity; not launched by runcontrol" in html
