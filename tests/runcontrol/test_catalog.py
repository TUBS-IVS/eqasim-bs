import json

from braunschweig.runcontrol.collectors import catalog
from braunschweig.runcontrol.models import RunManifest


class FakeTarget:
    kind = "local"
    name = "local"

    def __init__(self, dirs, files):
        self.dirs, self.files = dirs, files
        self.cfg = type("C", (), {"data_dir": "eqasim-data", "logs_dir": "logs"})()

    def listdir(self, path):
        return self.dirs.get(path, [])

    def read_text(self, path, tail_bytes=None):
        return self.files[path]

    def exists(self, path):
        return path in self.files

    def manifest_glob(self):
        return [p for p in self.files if p.endswith(".manifest.json")]


def _manifest():
    return RunManifest(run_id="r1", target="local", label="25pct_allfeat",
                       config_path="config_x.yml", git_commit="abc1234",
                       started_at_iso="2026-07-08T10:00:00", tmux_session=None, pid=42,
                       log_path="logs/rc_r1.log", exit_marker_path="logs/rc_r1.exit").to_json()


def test_scan_merges_manifest_runs_and_legacy_dirs():
    t = FakeTarget(
        dirs={"eqasim-data": [
            {"name": "output_bs_25pct_allfeat", "size": 0, "mtime": 1751970000.0},
            {"name": "cache_bs_100pct_old", "size": 0, "mtime": 1751000000.0},
            {"name": "data", "size": 0, "mtime": 1.0},          # not a run artifact
        ]},
        files={"logs/rc_r1.manifest.json": _manifest()},
    )
    res = catalog.scan(t, db_runs=[])
    assert res.n_manifest == 1 and res.n_legacy == 2
    by_id = {r["run_id"]: r for r in res.runs}
    assert by_id["r1"]["origin"] == "manifest" and by_id["r1"]["git_commit"] == "abc1234"
    legacy = by_id["cache_bs_100pct_old"]
    assert legacy["origin"] == "legacy_dir"
    assert legacy["git_commit"] == "unknown"                     # never invented
    assert "no_manifest" in legacy["flags"]
    assert all(not r["run_id"] == "data" for r in res.runs)


def test_inconsistent_meta_json_is_flagged_not_hidden():
    meta = json.dumps({"sampling_rate": 1.0})                    # dir name says 25pct
    t = FakeTarget(
        dirs={"eqasim-data": [{"name": "output_bs_25pct_x", "size": 0, "mtime": 2.0}]},
        files={"eqasim-data/output_bs_25pct_x/braunschweig_25pct_x_meta.json": meta},
    )
    t.dirs["eqasim-data/output_bs_25pct_x"] = [{"name": "braunschweig_25pct_x_meta.json", "size": 9, "mtime": 2.0}]
    res = catalog.scan(t, db_runs=[])
    (run,) = res.runs
    assert "meta_inconsistent" in run["flags"]
    assert run["sampling_hint"] == "25pct"                       # from dir name, labelled a hint
