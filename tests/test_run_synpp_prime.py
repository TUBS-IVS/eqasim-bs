"""Tests for scripts/run_synpp.py prime_from_config (shared-cache prime-on-launch)."""
import importlib.util
import os
import textwrap

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
