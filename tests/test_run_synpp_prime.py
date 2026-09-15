"""Tests for scripts/run_synpp.py prime_from_config (shared-cache prime-on-launch)."""
import importlib.util
import os
import textwrap
import sys

import pytest
import synpp

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "run_synpp.py")


def _load():
    spec = importlib.util.spec_from_file_location("run_synpp_mod", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prime_from_config_disabled_is_noop(tmp_path):
    mod = _load()
    cfg = tmp_path / "c.yml"
    cfg.write_text(textwrap.dedent("""
        working_directory: {wd}/wd
        config:
          cache_share_enabled: false
    """).format(wd=tmp_path).strip(), encoding="utf-8")
    assert mod.prime_from_config(str(cfg)) is None


def test_prime_from_config_primes_requested_stage(tmp_path):
    mod = _load()
    store = tmp_path / "store"
    os.makedirs(store)
    with open(store / "braunschweig.freight.extraction__h9.p", "wb") as f:
        f.write(b"x")
    wd = tmp_path / "wd"
    cfg = tmp_path / "c.yml"
    cfg.write_text(textwrap.dedent("""
        working_directory: {wd}
        config:
          cache_share_store: {store}
          cache_share_stages: [braunschweig.freight.extraction]
    """).format(wd=wd, store=store).strip(), encoding="utf-8")
    rep = mod.prime_from_config(str(cfg))
    assert "braunschweig.freight.extraction__h9" in rep["primed"]
    assert (wd / "braunschweig.freight.extraction__h9.p").exists()


def test_prime_from_config_absent_store_is_safe(tmp_path):
    mod = _load()
    wd = tmp_path / "wd"
    cfg = tmp_path / "c.yml"
    cfg.write_text(textwrap.dedent("""
        working_directory: {wd}
        config:
          cache_share_store: {tmp}/does_not_exist
          cache_share_stages: [braunschweig.freight.extraction]
    """).format(wd=wd, tmp=tmp_path).strip(), encoding="utf-8")
    rep = mod.prime_from_config(str(cfg))
    assert rep["primed"] == []
    assert "braunschweig.freight.extraction" in rep["missing_in_store"]


@pytest.mark.parametrize("enabled", [True, False])
def test_creation_provenance_tracking_obeys_resolved_switch(tmp_path, monkeypatch, enabled):
    mod = _load()
    module_name = "launcher_provenance_probe"
    (tmp_path / (module_name + ".py")).write_text(
        "def execute(context):\n    return 17\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, module_name, raising=False)
    wd = tmp_path / "wd"
    wd.mkdir()
    cfg = tmp_path / "c.yml"
    cfg.write_text(
        f"working_directory: {wd.as_posix()}\nconfig:\n  cache_share_metadata: {str(enabled).lower()}\n",
        encoding="utf-8")
    with mod.track_cache_provenance_from_config(str(cfg)):
        synpp.run([{"descriptor": module_name}], working_directory=str(wd), rerun_required=False)
    assert bool(list(wd.glob("*.metadata.json"))) is enabled


def test_failed_pipeline_does_not_label_partially_completed_entries(tmp_path, monkeypatch):
    mod = _load()
    module_name = "failed_provenance_probe"
    (tmp_path / (module_name + ".py")).write_text(
        "def execute(context):\n    return 17\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    wd = tmp_path / "wd"
    wd.mkdir()
    cfg = tmp_path / "c.yml"
    cfg.write_text(f"working_directory: {wd.as_posix()}\n", encoding="utf-8")
    with pytest.raises(RuntimeError, match="later stage failed"):
        with mod.track_cache_provenance_from_config(str(cfg)):
            synpp.run([{"descriptor": module_name}], working_directory=str(wd), rerun_required=False)
            raise RuntimeError("later stage failed")
    assert not list(wd.glob("*.metadata.json"))


# --- automatic post-run export (export_to_store_from_config) -----------------

def test_export_from_config_disabled_is_noop(tmp_path):
    mod = _load()
    cfg = tmp_path / "c.yml"
    cfg.write_text(textwrap.dedent("""
        working_directory: {wd}/wd
        config:
          cache_share_enabled: false
    """).format(wd=tmp_path).strip(), encoding="utf-8")
    assert mod.export_to_store_from_config(str(cfg)) is None


def test_export_from_config_export_flag_false_is_noop(tmp_path):
    mod = _load()
    cfg = tmp_path / "c.yml"
    cfg.write_text(textwrap.dedent("""
        working_directory: {wd}/wd
        config:
          cache_share_enabled: true
          cache_share_export: false
    """).format(wd=tmp_path).strip(), encoding="utf-8")
    assert mod.export_to_store_from_config(str(cfg)) is None


def test_export_from_config_exports_requested_stage(tmp_path):
    mod = _load()
    wd = tmp_path / "wd"
    os.makedirs(wd)
    with open(wd / "braunschweig.freight.extraction__h9.p", "wb") as f:
        f.write(b"x")
    store = tmp_path / "store"
    cfg = tmp_path / "c.yml"
    cfg.write_text(textwrap.dedent("""
        working_directory: {wd}
        config:
          cache_share_store: {store}
          cache_share_stages: [braunschweig.freight.extraction]
    """).format(wd=wd, store=store).strip(), encoding="utf-8")
    rep = mod.export_to_store_from_config(str(cfg))
    assert "braunschweig.freight.extraction__h9" in rep["exported"]
    assert (store / "braunschweig.freight.extraction__h9.p").exists()


def test_export_from_config_does_not_overwrite_existing_store_entry(tmp_path):
    mod = _load()
    wd = tmp_path / "wd"
    os.makedirs(wd)
    with open(wd / "braunschweig.freight.extraction__h9.p", "wb") as f:
        f.write(b"NEW")
    store = tmp_path / "store"
    os.makedirs(store)
    with open(store / "braunschweig.freight.extraction__h9.p", "wb") as f:
        f.write(b"ORIGINAL")
    cfg = tmp_path / "c.yml"
    cfg.write_text(textwrap.dedent("""
        working_directory: {wd}
        config:
          cache_share_store: {store}
          cache_share_stages: [braunschweig.freight.extraction]
    """).format(wd=wd, store=store).strip(), encoding="utf-8")
    rep = mod.export_to_store_from_config(str(cfg))
    # auto-export uses skip_existing=True -> the store entry is NOT clobbered.
    assert (store / "braunschweig.freight.extraction__h9.p").read_bytes() == b"ORIGINAL"
    assert "braunschweig.freight.extraction__h9" in rep["skipped_present"]
