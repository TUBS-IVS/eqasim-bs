# tests/test_analysis_suite.py
from __future__ import annotations
import json
import sys
from pathlib import Path
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.analysis import analysis_suite as AS  # noqa: E402


class FakeContext:
    """Minimal synpp context stand-in for stage unit tests."""
    def __init__(self, config, paths=None):
        self._config = dict(config)
        self._paths = dict(paths or {})
        self.declared_stages = []

    def config(self, key, *default):
        if key in self._config:
            return self._config[key]
        if default:
            return default[0]
        raise KeyError(key)

    def stage(self, name):
        self.declared_stages.append(name)
        return None

    def path(self, name):
        return self._paths[name]


def _base_config(output_path, **overrides):
    cfg = {
        "analysis_suite_enabled": True,
        "output_path": str(output_path),
        "sampling_rate": 0.01,
        "simwrapper_include_matsim": False,
        "braunschweig.population.popsim.work_dir": None,
        "braunschweig.population.method": None,
        "data_path": str(output_path),  # any dir; mid_dir won't exist -> skip
        "analysis_working_directory": None,
    }
    cfg.update(overrides)
    return cfg


def _write_min_output(tmp_path):
    (tmp_path / "run_persons.csv").write_text("person_id\n1\n")
    return tmp_path


def test_disabled_is_noop(tmp_path):
    ctx = FakeContext(_base_config(tmp_path, analysis_suite_enabled=False))
    assert AS.execute(ctx) is None
    assert not (tmp_path / "analysis").exists()


def test_malformed_output_raises(tmp_path):
    # enabled but no *_persons.csv in output_path -> hard error
    ctx = FakeContext(_base_config(tmp_path))
    with pytest.raises(FileNotFoundError):
        AS.execute(ctx)


def _install_pop_spy(monkeypatch, calls, raise_exc=None):
    import braunschweig.analysis.population_validation.run_population_validation as R
    monkeypatch.setattr(R, "_parse_args", lambda argv: {"argv": argv})
    def fake_run(ns):
        calls.append(ns)
        if raise_exc:
            raise raise_exc
    monkeypatch.setattr(R, "run", fake_run)


def test_population_validation_runs_by_default(tmp_path, monkeypatch):
    _write_min_output(tmp_path)
    calls = []
    _install_pop_spy(monkeypatch, calls)
    ctx = FakeContext(_base_config(tmp_path))
    AS.execute(ctx)
    assert len(calls) == 1
    assert "--run-output-dir" in calls[0]["argv"]
    summary = json.loads((tmp_path / "analysis" / "analysis_suite_summary.json").read_text())
    assert "population_validation" in summary["ran"]


def test_population_validation_flag_off(tmp_path, monkeypatch):
    _write_min_output(tmp_path)
    calls = []
    _install_pop_spy(monkeypatch, calls)
    ctx = FakeContext(_base_config(tmp_path, analysis_population_validation=False))
    AS.execute(ctx)
    assert calls == []
    summary = json.loads((tmp_path / "analysis" / "analysis_suite_summary.json").read_text())
    assert any(s["analysis"] == "population_validation" for s in summary["skipped"])


def test_sub_analysis_failure_is_caught(tmp_path, monkeypatch):
    _write_min_output(tmp_path)
    calls = []
    _install_pop_spy(monkeypatch, calls, raise_exc=RuntimeError("boom"))
    ctx = FakeContext(_base_config(tmp_path))
    result = AS.execute(ctx)  # must NOT raise
    assert result is not None
    summary = json.loads((tmp_path / "analysis" / "analysis_suite_summary.json").read_text())
    assert any(f["analysis"] == "population_validation" for f in summary["failed"])
