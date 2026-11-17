"""Wiring of the fork-core checks inside ``braunschweig.popsim.stage.execute`` (issue #645).

Each check has its own unit tests (tool_version, solver_path_gate, folders). These tests pin
WHERE ``execute`` calls them: the order of the early actions, what is passed on to the
batches and the signature, and that the solver-path gate runs after the batches. The steps
in between are stubbed; ``execute`` stops with ``_Stop`` once the spied call was made.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import stage, tool_version


class _Stop(Exception):
    pass


_COMMIT = "3d70f73c00fb47696b56552540c2d858d93ed356"


def _values(**overrides):
    values = {
        stage.KEY_POPSIM_VERSION: "0.10.0+bs.1",
        stage.KEY_KEEP_FRACTIONAL_SAMPLE_WEIGHTS: True,
        stage.KEY_INTEGERIZER_DRAW: "pareto",
        stage.KEY_INTEGERIZER_TIMEOUT_S: 600,
        stage.KEY_SOLVER_FALLBACK_ERROR_RATE: 0.2,
        "random_seed": 4711,
    }
    values.update(overrides)
    return values


def _context(values):
    # Unlisted keys are the boolean/numeric feature flags the stubbed steps never use.
    return SimpleNamespace(
        config=lambda key: values.get(key, False), set_info=lambda key, value: None)


# --- read_stage_paths: relative project dir ----------------------------------------------

def test_relative_popsimprep_dir_is_resolved_against_the_repo_root(monkeypatch):
    # The process working directory must not matter.
    monkeypatch.chdir(Path(__file__).parent)
    values = {key: key for key in (
        stage.KEY_CELLS, stage.KEY_MID, stage.KEY_CONTROLS, stage.KEY_SETTINGS,
        stage.KEY_LOGGING, stage.KEY_UV)}
    values[stage.KEY_POPSIMPREP] = "environments/populationsim"
    paths = stage._read_stage_paths(_context(values))
    expected = tool_version.REPO_ROOT / "environments" / "populationsim"
    assert Path(paths[5]) == expected and expected.is_dir()


def test_popsimprep_dir_that_is_no_directory_names_the_key(tmp_path):
    values = {key: key for key in (
        stage.KEY_CELLS, stage.KEY_MID, stage.KEY_CONTROLS, stage.KEY_SETTINGS,
        stage.KEY_LOGGING, stage.KEY_UV)}
    values[stage.KEY_POPSIMPREP] = str(tmp_path / "missing")
    with pytest.raises(RuntimeError, match="popsimprep_dir") as excinfo:
        stage._read_stage_paths(_context(values))
    assert "not a directory" in str(excinfo.value)


def test_resolve_project_dir_rejects_an_empty_value():
    with pytest.raises(RuntimeError, match="popsimprep_dir"):
        tool_version.resolve_project_dir("", key=stage.KEY_POPSIMPREP)


# --- execute: order of the early actions -------------------------------------------------

def _stub_early_steps(monkeypatch, calls):
    monkeypatch.setattr(
        stage, "_read_stage_paths",
        lambda ctx: ("cells", "mid", "controls", "settings", "logging", "popsimprep", "uv"))

    def check(context, uv_path, project_dir, expected):
        calls.append("environment check")
        return {"commit": _COMMIT}

    monkeypatch.setattr(stage.tool_version, "check_populationsim_environment", check)


@pytest.mark.parametrize("key, bad_value, error_match", [
    (stage.KEY_INTEGERIZER_DRAW, "banana", "INTEGERIZER_DRAW"),
    # The gate only runs after all batches (hours); a typo must fail before the seed build.
    (stage.KEY_SOLVER_FALLBACK_ERROR_RATE, 2.0, "solver_fallback_error_rate"),
])
def test_execute_validates_the_integerizer_and_gate_settings_before_the_seed_build(
        monkeypatch, key, bad_value, error_match):
    calls = []
    _stub_early_steps(monkeypatch, calls)

    def must_not_run(context):
        raise AssertionError("execute() went on after an invalid setting")

    monkeypatch.setattr(stage, "_read_batching_and_scope_config", must_not_run)
    context = _context(_values(**{key: bad_value}))
    with pytest.raises(ValueError, match=error_match):
        stage.execute(context)
    assert calls == ["environment check"]


# --- execute: batches, then the solver-path gate -----------------------------------------

def test_execute_runs_the_gate_after_the_batches_and_passes_the_settings_on(
        monkeypatch, tmp_path):
    calls = []
    _stub_early_steps(monkeypatch, calls)
    settings = tmp_path / "settings.yaml"
    settings.write_text("geographies: [ZENSUS100m]\n", encoding="utf-8")
    monkeypatch.setattr(
        stage, "_read_stage_paths",
        lambda ctx: ("cells", "mid", "controls", str(settings), "logging", "popsimprep", "uv"))

    class _Source:
        name = "mid"

        @staticmethod
        def seed_columns():
            return SimpleNamespace(household_id="H_ID")

    monkeypatch.setattr(
        stage, "_read_batching_and_scope_config",
        lambda ctx: (10, 1, str(tmp_path / "work"), [], "mid", False, False))
    monkeypatch.setattr(
        stage, "_create_seeded_rngs",
        lambda ctx: (1, np.random.RandomState(1), np.random.RandomState(2)))
    monkeypatch.setattr(stage, "_resolve_source", lambda name: _Source())
    monkeypatch.setattr(
        stage, "_read_control_config",
        lambda ctx, name: ((), (), "csv", False, False, (), set(), 30, None, "default", False))
    monkeypatch.setattr(
        stage, "_build_control_frame", lambda *a, **k: (pd.DataFrame(), []))
    monkeypatch.setattr(
        stage, "_load_tier3_kreis_controls", lambda *a, **k: (None, None, set()))
    monkeypatch.setattr(stage, "_resolve_cell_load_columns", lambda *a, **k: [])
    monkeypatch.setattr(stage.mid, "load_control_cells", lambda *a, **k: pd.DataFrame())
    monkeypatch.setattr(stage.mid, "filter_zgb_cells", lambda cells, kreise: cells)
    monkeypatch.setattr(stage, "_add_aggregated_control_columns", lambda cells, *a, **k: cells)
    monkeypatch.setattr(stage, "_inject_employment_grid_columns", lambda c, cells, *a, **k: cells)
    monkeypatch.setattr(stage, "_inject_ownership_grid_columns", lambda c, cells, *a, **k: cells)
    monkeypatch.setattr(
        stage, "_build_populationsim_seed",
        lambda *a, **k: (pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()))
    monkeypatch.setattr(stage, "_prepare_batch_runner", lambda *a, **k: "run_one")
    monkeypatch.setattr(
        stage, "_derive_kreis_attribute_control_targets",
        lambda context, cells, active, prior, table, cmap, names, *a, **k: (table, cmap))
    monkeypatch.setattr(stage.folders, "validate_settings_geographies", lambda *a, **k: None)

    seen = {}

    def purge(*args, **kwargs):
        calls.append("purge")
        seen["purge_args"] = args

    def batches(*args, **kwargs):
        calls.append("batches")
        seen["batches_args"] = args
        return "merge_report"

    def gate(work_dir, error_rate):
        calls.append("gate")
        seen["gate"] = (work_dir, error_rate)
        raise _Stop

    monkeypatch.setattr(stage, "_purge_stale_batches_for_changed_config", purge)
    monkeypatch.setattr(stage, "_run_populationsim_batches", batches)
    monkeypatch.setattr(stage, "_log_integerizer_feasibility", lambda *a, **k: calls.append("log"))
    monkeypatch.setattr(stage.solver_path_gate, "check_solver_paths", gate)

    with pytest.raises(_Stop):
        stage.execute(_context(_values()))

    assert calls == ["environment check", "purge", "batches", "log", "gate"]
    expected_overrides = {
        "KEEP_FRACTIONAL_SAMPLE_WEIGHTS": True, "INTEGERIZER_DRAW": "pareto",
        "INTEGERIZER_TIMEOUT": 600, "INTEGERIZER_PATH_RECORD": True,
    }
    # The last two positional arguments of the batch runner are the overrides and the seed base.
    assert seen["batches_args"][-2:] == (expected_overrides, 4711)
    # The purge (batch signature) gets the version, the verified commit, overrides and seed base.
    assert seen["purge_args"][-4:] == ("0.10.0+bs.1", _COMMIT, expected_overrides, 4711)
    assert seen["gate"] == (str(tmp_path / "work"), 0.2)
