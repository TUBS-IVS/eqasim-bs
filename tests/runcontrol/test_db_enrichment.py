from braunschweig.runcontrol.db import Database


def test_put_then_get_hit_on_matching_mtime(tmp_path):
    db = Database(tmp_path / "runs.db")
    db.put_enrichment("local:cache_x", 100.0, {"effective_config_stage_count": 3})
    got = db.get_enrichment("local:cache_x", 100.0)
    assert got == {"effective_config_stage_count": 3}


def test_get_miss_on_stale_mtime(tmp_path):
    db = Database(tmp_path / "runs.db")
    db.put_enrichment("local:cache_x", 100.0, {"a": 1})
    assert db.get_enrichment("local:cache_x", 200.0) is None   # dir changed -> stale
    assert db.get_enrichment("local:other", 100.0) is None     # unknown key


def test_put_upserts(tmp_path):
    db = Database(tmp_path / "runs.db")
    db.put_enrichment("k", 1.0, {"v": "old"})
    db.put_enrichment("k", 2.0, {"v": "new"})
    assert db.get_enrichment("k", 2.0) == {"v": "new"}
    assert db.get_enrichment("k", 1.0) is None
