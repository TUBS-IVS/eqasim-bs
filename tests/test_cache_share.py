"""Tests for braunschweig.cache_share (shared synpp stage-cache export/prime).

The module copies synpp's native ``<module>__<hash>.p`` (+ ``.cache/``) artifacts
between a working_directory and a shared store. It never recomputes synpp's hash:
synpp validates the hash itself on load, so a primed entry whose hash does not match
the target config is simply ignored (recomputed) -- never corruption.
"""
import os

from braunschweig import cache_share


def _make_entry(d, name, with_cache=True):
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, name + ".p"), "wb") as f:
        f.write(b"x")
    if with_cache:
        os.makedirs(os.path.join(d, name + ".cache"), exist_ok=True)
        with open(os.path.join(d, name + ".cache", "f.txt"), "w", encoding="utf-8") as f:
            f.write("c")


def test_find_stage_entries(tmp_path):
    wd = tmp_path / "wd"
    _make_entry(str(wd), "braunschweig.freight.extraction__abc123")
    _make_entry(str(wd), "other.stage__def456", with_cache=False)
    found = cache_share.find_stage_entries(str(wd), "braunschweig.freight.extraction")
    assert found == ["braunschweig.freight.extraction__abc123"]


def test_find_stage_entries_does_not_match_prefix_siblings(tmp_path):
    # "german_wide" must NOT match "german_wide_xl"; the "__" separator guards this.
    wd = tmp_path / "wd"
    _make_entry(str(wd), "braunschweig.data.freight.german_wide__h1", with_cache=False)
    _make_entry(str(wd), "braunschweig.data.freight.german_wide_xl__h2", with_cache=False)
    found = cache_share.find_stage_entries(str(wd), "braunschweig.data.freight.german_wide")
    assert found == ["braunschweig.data.freight.german_wide__h1"]


def test_export_then_prime_roundtrip(tmp_path):
    wd = tmp_path / "wd"
    store = tmp_path / "store"
    target = tmp_path / "target"
    _make_entry(str(wd), "braunschweig.freight.extraction__abc123")
    rep = cache_share.export(str(wd), ["braunschweig.freight.extraction"], str(store))
    assert "braunschweig.freight.extraction__abc123" in rep["exported"]
    assert (store / "braunschweig.freight.extraction__abc123.p").exists()
    assert (store / "braunschweig.freight.extraction__abc123.cache" / "f.txt").exists()
    rep2 = cache_share.prime(str(target), ["braunschweig.freight.extraction"], str(store), recompute=[])
    assert "braunschweig.freight.extraction__abc123" in rep2["primed"]
    assert (target / "braunschweig.freight.extraction__abc123.p").exists()
    assert (target / "braunschweig.freight.extraction__abc123.cache" / "f.txt").exists()


def test_export_skips_module_without_cache_entry(tmp_path):
    wd = tmp_path / "wd"
    store = tmp_path / "store"
    rep = cache_share.export(str(wd), ["absent.module"], str(store))
    assert rep["exported"] == []
    assert rep["skipped"] == ["absent.module"]


def test_export_skip_existing_does_not_overwrite_same_hash(tmp_path):
    # skip_existing=True: an entry already in the store (same <module>__<hash>) is
    # left untouched, never re-copied -> the auto-export never clobbers the store.
    wd = tmp_path / "wd"
    store = tmp_path / "store"
    _make_entry(str(wd), "braunschweig.freight.extraction__abc123")
    os.makedirs(store)
    with open(store / "braunschweig.freight.extraction__abc123.p", "wb") as f:
        f.write(b"ORIGINAL")
    rep = cache_share.export(
        str(wd), ["braunschweig.freight.extraction"], str(store), skip_existing=True,
    )
    assert (store / "braunschweig.freight.extraction__abc123.p").read_bytes() == b"ORIGINAL"
    assert "braunschweig.freight.extraction__abc123" in rep["skipped_present"]
    assert "braunschweig.freight.extraction__abc123" not in rep["exported"]


def test_export_skip_existing_adds_new_hash_alongside(tmp_path):
    # A different config/content has a different hash -> different filename -> the new
    # entry is stored ALONGSIDE the old one (nothing overwritten).
    wd = tmp_path / "wd"
    store = tmp_path / "store"
    _make_entry(str(store), "braunschweig.freight.extraction__OLDHASH", with_cache=False)
    _make_entry(str(wd), "braunschweig.freight.extraction__NEWHASH")
    rep = cache_share.export(
        str(wd), ["braunschweig.freight.extraction"], str(store), skip_existing=True,
    )
    assert (store / "braunschweig.freight.extraction__OLDHASH.p").exists()
    assert (store / "braunschweig.freight.extraction__NEWHASH.p").exists()
    assert "braunschweig.freight.extraction__NEWHASH" in rep["exported"]


def test_export_default_overwrites_existing(tmp_path):
    # Default skip_existing=False keeps the current (overwrite) CLI behaviour.
    wd = tmp_path / "wd"
    store = tmp_path / "store"
    _make_entry(str(wd), "m__h1", with_cache=False)
    os.makedirs(store)
    with open(store / "m__h1.p", "wb") as f:
        f.write(b"OLD")
    rep = cache_share.export(str(wd), ["m"], str(store))
    assert (store / "m__h1.p").read_bytes() == b"x"  # overwritten
    assert "m__h1" in rep["exported"]


def test_prime_skips_recompute_and_star(tmp_path):
    store = tmp_path / "store"
    target = tmp_path / "target"
    _make_entry(str(store), "braunschweig.freight.extraction__abc123")
    r1 = cache_share.prime(str(target), ["braunschweig.freight.extraction"], str(store),
                           recompute=["braunschweig.freight.extraction"])
    assert r1["forced"] == ["braunschweig.freight.extraction"]
    assert not (target / "braunschweig.freight.extraction__abc123.p").exists()
    r2 = cache_share.prime(str(target), ["braunschweig.freight.extraction"], str(store), recompute=["*"])
    assert r2["forced"] == ["braunschweig.freight.extraction"]
    assert not (target / "braunschweig.freight.extraction__abc123.p").exists()


def test_prime_skips_present_and_reports_missing(tmp_path):
    store = tmp_path / "store"
    target = tmp_path / "target"
    _make_entry(str(target), "braunschweig.freight.extraction__abc123")  # already present
    _make_entry(str(store), "braunschweig.freight.extraction__abc123")
    rep = cache_share.prime(str(target), ["braunschweig.freight.extraction", "absent.module"],
                            str(store), recompute=[])
    assert "braunschweig.freight.extraction__abc123" in rep["skipped_present"]
    assert "absent.module" in rep["missing_in_store"]
    assert rep["primed"] == []
