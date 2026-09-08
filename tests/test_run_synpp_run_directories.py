"""Tests for scripts/run_synpp.py ensure_run_directories.

Upstream documentation/meta_output.py opens ``<output_path>/<prefix>meta.json`` for
writing without creating ``output_path``. A fresh output_path never exists, so that stage
aborted three consecutive 100 % proof runs minutes after launch (Phase B 2026-09-05, arm 3
2026-09-06, arm 4 2026-09-07). The stage is upstream and deliberately unmodified here, so
the launcher must create the directory. The first test below is the regression pin: it
writes a config with a non-existent output_path and asserts the directory exists
afterwards, so it fails if the call or the helper is removed.
"""
import importlib.util
import os
import textwrap

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "run_synpp.py")


def _load():
    spec = importlib.util.spec_from_file_location("run_synpp_dirs_mod", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _write_config(tmp_path, working_directory=None, output_path=None):
    lines = []
    if working_directory is not None:
        lines.append(f"working_directory: {working_directory}")
    lines.append("config:")
    if output_path is not None:
        lines.append(f"  output_path: {output_path}")
    else:
        lines.append("  sampling_rate: 1.0")
    cfg = tmp_path / "c.yml"
    cfg.write_text("\n".join(lines), encoding="utf-8")
    return cfg


def test_creates_output_path_that_does_not_exist(tmp_path):
    """The regression pin for the meta_output abort."""
    mod = _load()
    out = tmp_path / "output_fresh"
    wd = tmp_path / "cache_fresh"
    cfg = _write_config(tmp_path, working_directory=wd, output_path=out)

    assert not out.exists()
    created = mod.ensure_run_directories(str(cfg))

    assert out.is_dir(), "output_path must exist after the launcher ran"
    assert wd.is_dir(), "working_directory must exist after the launcher ran"
    assert sorted(created) == sorted([str(wd), str(out)])


def test_existing_directories_are_left_alone_and_not_reported_as_created(tmp_path):
    mod = _load()
    out = tmp_path / "output_there"
    wd = tmp_path / "cache_there"
    os.makedirs(out)
    os.makedirs(wd)
    marker = out / "keep.txt"
    marker.write_text("kept", encoding="utf-8")
    cfg = _write_config(tmp_path, working_directory=wd, output_path=out)

    created = mod.ensure_run_directories(str(cfg))

    assert created == [], "an existing directory must not be reported as created"
    assert marker.read_text(encoding="utf-8") == "kept", "existing content must survive"


def test_missing_output_path_key_is_not_an_error(tmp_path):
    """A config without output_path (e.g. a popsim-only run) must still launch."""
    mod = _load()
    wd = tmp_path / "cache_only"
    cfg = _write_config(tmp_path, working_directory=wd, output_path=None)

    created = mod.ensure_run_directories(str(cfg))

    assert created == [str(wd)]
    assert wd.is_dir()


def test_reports_created_present_and_unconfigured_counts(caplog):
    """The log line must name all three groups, so a run record shows what was created."""
    mod = _load()
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        out = os.path.join(tmp, "o")
        cfg_path = os.path.join(tmp, "c.yml")
        with open(cfg_path, "w", encoding="utf-8") as fh:
            fh.write(textwrap.dedent(f"""
                config:
                  output_path: {out}
            """).strip())
        with caplog.at_level("INFO", logger="braunschweig"):
            mod.ensure_run_directories(cfg_path)

    messages = [r.getMessage() for r in caplog.records]
    line = [m for m in messages if "[run_dirs]" in m]
    assert line, f"expected a [run_dirs] log line, got {messages}"
    assert "created 1" in line[0]
    assert "not configured 1 (working_directory)" in line[0]
