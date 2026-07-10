from braunschweig.runcontrol.db import Database
from braunschweig.runcontrol.settings import load_settings


def test_settings_defaults_and_override(tmp_path):
    p = tmp_path / "runcontrol.toml"
    p.write_text('[target.local]\nkind="local"\nrepo="."\n')
    s = load_settings(p)
    assert s.active_run_globs == ["output_*", "cache_*", "popsim_work_*"]
    assert s.autodetect_interval_s == 60
    p2 = tmp_path / "b.toml"
    p2.write_text('active_run_globs=["foo_*"]\nautodetect_interval_s=15\n'
                  '[target.local]\nkind="local"\nrepo="."\n')
    s2 = load_settings(p2)
    assert s2.active_run_globs == ["foo_*"] and s2.autodetect_interval_s == 15


def test_insert_external_run_auto_detected_flag(tmp_path):
    db = Database(tmp_path / "runs.db")
    db.insert_external_run("popsim_work_x", "server", "popsim_work_x", "unknown",
                           None, "eqasim-data/popsim_work_x", 1000.0, "2026-07-10T10:00:00",
                           auto_detected=True)
    row = db.get_run("popsim_work_x")
    assert row["auto_detected"] == 1 and row["external"] == 1
    # default stays 0 for a manual external run
    db.insert_external_run("cache_y", "server", "cache_y", "unknown",
                           None, "eqasim-data/cache_y", 1.0, "2026-07-10T10:00:00")
    assert db.get_run("cache_y")["auto_detected"] == 0
