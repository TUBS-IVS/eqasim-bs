import json

from braunschweig.runcontrol.collectors import enrich


class FakeTarget:
    kind = "local"
    name = "local"

    def __init__(self, files, dirs=None):
        self.files = files
        self.dirs = dirs or {}
        self.cfg = type("C", (), {"data_dir": "eqasim-data", "logs_dir": "logs"})()
        self.reads = 0

    def exists(self, p):
        return p in self.files or p in self.dirs

    def read_text(self, p, tail_bytes=None):
        self.reads += 1
        if p not in self.files:
            raise FileNotFoundError(p)
        return self.files[p]

    def listdir(self, p):
        return self.dirs.get(p, [])


def _pipeline(stages):
    # stages: list of (short_name, updated_epoch, config_dict)
    return json.dumps({
        f"{short}__{i:032x}": {"config": cfg, "updated": upd, "dependencies": [],
                               "info": {}, "module_hash": "h"}
        for i, (short, upd, cfg) in enumerate(stages)
    })


def test_paired_artifact_name():
    assert enrich.paired_artifact_name("output_bs_25pct") == "cache_bs_25pct"
    assert enrich.paired_artifact_name("cache_bs_25pct") == "output_bs_25pct"
    assert enrich.paired_artifact_name("weird_name") is None


def test_newest_activity_mtime_max_over_pair():
    t = FakeTarget(
        files={},
        dirs={
            "eqasim-data/cache_x": [{"name": "a.cache", "size": 1, "mtime": 100.0},
                                    {"name": "pipeline.json", "size": 1, "mtime": 200.0}],
            "eqasim-data/output_x": [{"name": "it.5", "size": 1, "mtime": 500.0}],
        },
    )
    assert enrich.newest_activity_mtime(t, "cache_x") == 500.0
    # queried from the output-dir side, the pairing must still reach the cache dir
    assert enrich.newest_activity_mtime(t, "output_x") == 500.0


def test_newest_activity_mtime_none_when_no_children():
    t = FakeTarget(files={}, dirs={})
    assert enrich.newest_activity_mtime(t, "cache_empty") is None


def test_merge_stage_configs_union_and_conflict():
    pj = json.loads(_pipeline([
        ("a", 1.0, {"sampling_rate": 0.25, "random_seed": 1234}),
        ("b", 2.0, {"random_seed": 1234, "freight_enabled": True}),
        ("c", 3.0, {"sampling_rate": 0.10}),   # conflict on sampling_rate
    ]))
    merged, conflicts, n = enrich.merge_stage_configs(pj)
    assert n == 3
    assert merged["random_seed"] == 1234 and merged["freight_enabled"] is True
    assert "sampling_rate" in conflicts


def test_timeline_first_duration_none_then_deltas():
    pj = json.loads(_pipeline([
        ("a", 1000.0, {}), ("b", 1300.0, {}), ("c", 1350.0, {}),
    ]))
    tl = enrich.timeline_from_pipeline(pj)
    assert [t["stage_short"] for t in tl] == ["a", "b", "c"]
    assert tl[0]["approx_duration_s"] is None
    assert tl[1]["approx_duration_s"] == 300.0 and tl[2]["approx_duration_s"] == 50.0
    assert tl[0]["completed_at_iso"].startswith("19") or tl[0]["completed_at_iso"][:2] == "20"


def test_curated_split_groups_and_rest():
    curated, rest = enrich.curated_split({"sampling_rate": 0.25, "some_exotic": 9})
    keys = {f["key"] for g in curated.values() for f in g}
    assert "sampling_rate" in keys
    assert rest == {"some_exotic": 9}


def test_enrich_artifact_full_and_partial_label():
    meta = json.dumps({"sampling_rate": 0.25, "random_seed": 1234,
                       "created": "2026-06-22T09:34:36+00:00", "commit": "abc1234"})
    pj = _pipeline([("a", 1000.0, {"sampling_rate": 0.25}), ("b", 1300.0, {"freight_enabled": True})])
    t = FakeTarget(
        files={
            "eqasim-data/cache_bs_25pct/pipeline.json": pj,
            "eqasim-data/output_bs_25pct/braunschweig_25pct_meta.json": meta,
        },
        dirs={
            "eqasim-data/output_bs_25pct": [{"name": "braunschweig_25pct_meta.json", "size": 9, "mtime": 2.0},
                                            {"name": "analysis", "size": 0, "mtime": 2.0}],
        },
    )
    e = enrich.enrich_artifact(t, "cache_bs_25pct", "cache")
    assert e.paired_name == "output_bs_25pct"
    assert e.effective_config_stage_count == 2
    assert e.effective_config["freight_enabled"] is True
    assert e.meta["commit"] == "abc1234"
    assert e.run_date_iso.startswith("2026-06-22")
    assert e.presence["analysis"] is True and e.presence["matsim_config"] is False
    assert e.sources["pipeline_json"] == "ok" and e.sources["meta_json"] == "ok"


def test_enrich_artifact_missing_sources_are_honest_not_500():
    t = FakeTarget(files={}, dirs={})
    e = enrich.enrich_artifact(t, "cache_bs_orphan", "cache")
    assert e.effective_config == {} and e.effective_config_stage_count == 0
    assert e.meta is None and e.run_date_iso is None
    assert e.sources["pipeline_json"] == "missing"
    assert "no_paired_output" in e.flags


def test_enrich_artifact_corrupt_pipeline_flagged():
    t = FakeTarget(files={"eqasim-data/cache_x/pipeline.json": "{not json"},
                   dirs={})
    e = enrich.enrich_artifact(t, "cache_x", "cache")
    assert e.sources["pipeline_json"].startswith("error:")
    assert e.effective_config == {}


def test_enrich_flags_meta_inconsistent():
    # Dir name says 25pct but meta.json records sampling_rate 1.0 -- a known
    # server-side issue (RUNS.md); enrich must flag it without any extra I/O
    # beyond the meta.json read it already performs.
    meta = json.dumps({"sampling_rate": 1.0})
    t = FakeTarget(
        files={"eqasim-data/output_bs_25pct_x/braunschweig_25pct_x_meta.json": meta},
        dirs={"eqasim-data/output_bs_25pct_x": [{"name": "braunschweig_25pct_x_meta.json", "size": 9, "mtime": 2.0}]},
    )
    e = enrich.enrich_artifact(t, "output_bs_25pct_x", "output")
    assert "meta_inconsistent" in e.flags


def test_enrich_does_not_flag_consistent_meta():
    meta = json.dumps({"sampling_rate": 0.25})
    t = FakeTarget(
        files={"eqasim-data/output_bs_25pct_y/braunschweig_25pct_y_meta.json": meta},
        dirs={"eqasim-data/output_bs_25pct_y": [{"name": "braunschweig_25pct_y_meta.json", "size": 9, "mtime": 2.0}]},
    )
    e = enrich.enrich_artifact(t, "output_bs_25pct_y", "output")
    assert "meta_inconsistent" not in e.flags
