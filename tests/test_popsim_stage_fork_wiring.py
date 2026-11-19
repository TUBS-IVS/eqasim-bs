"""Wiring of the fork-core checks inside ``braunschweig.popsim.stage.execute`` (issue #645).

Each check has its own unit tests (tool_version, solver_path_gate, folders). These tests pin
WHERE ``execute`` calls them: the order of the early actions, what is passed on to the
batches and the signature, and that the solver-path gate runs after the batches. The steps
in between are stubbed; ``execute`` stops with ``_Stop`` once the spied call was made.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

# append, never insert(0): 'tests' at the front of sys.path would shadow the braunschweig package.
sys.path.append(str(Path(__file__).resolve().parent))

import popsim_control_build_fixture as fixture  # noqa: E402

from braunschweig.popsim import control_spec, stage, tool_version  # noqa: E402


class _Stop(Exception):
    pass


_COMMIT = "3d70f73c00fb47696b56552540c2d858d93ed356"
_LOCK_HASH = "lockhash"


def _values(**overrides):
    values = {
        stage.KEY_POPSIM_VERSION: "0.10.0+bs.1",
        stage.KEY_KEEP_FRACTIONAL_SAMPLE_WEIGHTS: True,
        stage.KEY_INTEGERIZER_DRAW: "pareto",
        stage.KEY_INTEGERIZER_TIMEOUT_S: 600,
        stage.KEY_INTEGERIZER_ROUNDING: "controlled",
        stage.KEY_INTEGERIZER_PENALTY_FLOOR: 100.0,
        stage.KEY_INTEGERIZER_PENALTY_CAP: 100000.0,
        stage.KEY_INTEGERIZER_PARENT_TOTAL_FACTOR: 10.0,
        stage.KEY_INTEGERIZER_CONSERVE_PENALTY: 100000.0,
        stage.KEY_INTEGERIZER_CONTROLLED_METHOD: "search",
        stage.KEY_INTEGERIZER_SEARCH_MAX_SWEEPS: 200,
        stage.KEY_INTEGERIZER_SEARCH_SHORTLIST: 24,
        stage.KEY_STORE_POSITIVE_WEIGHT_ROWS_ONLY: False,
        stage.KEY_FAST_PATHS: False,
        stage.KEY_SOLVER_FALLBACK_ERROR_RATE: 0.2,
        "random_seed": 4711,
    }
    values.update(overrides)
    return values


def _context(values, stage_path=None):
    # Unlisted keys are the boolean/numeric feature flags the stubbed steps never use.
    # ``stage_path`` is the directory ``context.path()`` returns (the stage report file goes there).
    return SimpleNamespace(
        config=lambda key: values.get(key, False), set_info=lambda key, value: None,
        path=lambda: str(stage_path))


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

_SETTINGS_TEXT = "models:\n- setup_data_structures\n- write_data_dictionary\n"


def _stub_early_steps(monkeypatch, calls, settings_path):
    if not Path(settings_path).exists():
        Path(settings_path).write_text(_SETTINGS_TEXT, encoding="utf-8")
    monkeypatch.setattr(
        stage, "_read_stage_paths",
        lambda ctx: ("cells", "mid", "controls", str(settings_path), "logging", "popsimprep", "uv"))

    def check(context, uv_path, project_dir, expected):
        calls.append("environment check")
        return {"commit": _COMMIT}

    monkeypatch.setattr(stage.tool_version, "check_populationsim_environment", check)
    monkeypatch.setattr(stage.tool_version, "lock_content_hash", lambda project_dir: _LOCK_HASH)


@pytest.mark.parametrize("key, bad_value, settings_text, error_match", [
    (stage.KEY_INTEGERIZER_DRAW, "banana", None, "INTEGERIZER_DRAW"),
    (stage.KEY_INTEGERIZER_ROUNDING, "banana", None, "INTEGERIZER_ROUNDING"),
    # The run-time options need fork 0.10.0+bs.2 (the configured version here is bs.1): refused
    # up front, before any seed is built, instead of being ignored silently by the older fork.
    (stage.KEY_STORE_POSITIVE_WEIGHT_ROWS_ONLY, True, None, "store_positive_weight_rows_only"),
    (stage.KEY_FAST_PATHS, True, None, "fast_paths"),
    # The gate only runs after all batches (hours); a typo must fail before the seed build.
    (stage.KEY_SOLVER_FALLBACK_ERROR_RATE, 2.0, None, "solver_fallback_error_rate"),
    # A server-local settings file contradicting the tracked timeout (0.10.0's key name).
    (None, None, _SETTINGS_TEXT + "INTEGIZER_TIMEOUT: 60\n", "INTEGIZER_TIMEOUT"),
    # The per-batch seed is derived by the pipeline: a settings file that sets it is refused
    # up front too, not only per batch after the seed build and the work-dir purge.
    (None, None, _SETTINGS_TEXT + "INTEGERIZER_RANDOM_SEED: 2541077060\n",
     "INTEGERIZER_RANDOM_SEED"),
])
def test_execute_validates_the_integerizer_and_gate_settings_before_the_seed_build(
        monkeypatch, tmp_path, key, bad_value, settings_text, error_match):
    calls = []
    settings_path = tmp_path / "settings.yaml"
    if settings_text is not None:
        settings_path.write_text(settings_text, encoding="utf-8")
    _stub_early_steps(monkeypatch, calls, settings_path)

    def must_not_run(context):
        raise AssertionError("execute() went on after an invalid setting")

    monkeypatch.setattr(stage, "_read_batching_and_scope_config", must_not_run)
    context = _context(_values(**({key: bad_value} if key else {})))
    with pytest.raises(ValueError, match=error_match):
        stage.execute(context)
    assert calls == ["environment check"]


# --- execute: batches, then the solver-path gate -----------------------------------------

@pytest.mark.parametrize("tenure_limit", [False, True], ids=["owner_limit_off", "owner_limit_on"])
def test_execute_runs_the_gate_after_the_batches_and_passes_the_settings_on(
        monkeypatch, tmp_path, tenure_limit):
    # With the owner limit (#657) on, the run uses the catalog controls of tiers 0-2 and the
    # fixture cells; execute must request the dwelling column, apply the rule and report it.
    calls = []
    _stub_early_steps(monkeypatch, calls, tmp_path / "settings.yaml")
    settings = tmp_path / "settings.yaml"
    settings.write_text(_SETTINGS_TEXT + "geographies: [ZENSUS100m]\n", encoding="utf-8")
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
        lambda ctx: (1, np.random.RandomState(1), np.random.RandomState(2),
                     np.random.RandomState(3)))
    monkeypatch.setattr(stage, "_resolve_source", lambda name: _Source())
    tiers, controls_source = (fixture.CONTROL_TIERS, "catalog") if tenure_limit else ((), "csv")
    monkeypatch.setattr(
        stage, "_read_control_config",
        lambda ctx, name: (tiers, (), controls_source, False, False, (), set(), 30, None,
                           "default", False))
    monkeypatch.setattr(
        stage, "_build_control_frame", lambda *a, **k: (pd.DataFrame(), []))
    monkeypatch.setattr(
        stage, "_load_tier3_kreis_controls", lambda *a, **k: (None, None, set()))
    # _resolve_cell_load_columns runs for real: the test checks which columns execute asks for.
    loaded_columns = {}

    def load_control_cells(cells_path, load_cols):
        loaded_columns["columns"] = list(load_cols)
        return fixture.make_cells() if tenure_limit else pd.DataFrame()

    monkeypatch.setattr(stage.mid, "load_control_cells", load_control_cells)
    monkeypatch.setattr(
        stage.mid, "filter_zgb_cells_with_report",
        lambda cells, kreise: (cells, {"cells_seen": 0, "cells_kept": 0, "cells_dropped": 0}))
    monkeypatch.setattr(stage, "_add_aggregated_control_columns", lambda cells, *a, **k: (cells, None))
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
    # The WP3 seed contract check (tested in tests/test_popsim_seed_contract.py) runs before the
    # purge; this test only pins its place in the order.
    monkeypatch.setattr(stage, "_read_total_hh_control", lambda path: "HH_TOTAL")
    monkeypatch.setattr(stage, "_check_seed_contract", lambda *a, **k: calls.append("contract"))

    seen = {}

    def purge(*args, **kwargs):
        calls.append("purge")
        seen["purge_args"] = args

    def batches(*args, **kwargs):
        calls.append("batches")
        seen["batches_args"] = args
        return "merge_report"

    def gate(work_dir, error_rate, expected_rounding="round"):
        calls.append("gate")
        seen["gate"] = (work_dir, error_rate, expected_rounding)
        raise _Stop

    monkeypatch.setattr(stage, "_purge_stale_batches_for_changed_config", purge)
    monkeypatch.setattr(stage, "_run_populationsim_batches", batches)
    def fast_paths(work_dir, enabled):
        calls.append("fast paths")
        seen["fast_paths"] = (work_dir, enabled)
        return {}

    monkeypatch.setattr(stage, "_log_integerizer_feasibility", lambda *a, **k: calls.append("log"))
    monkeypatch.setattr(stage.fast_path_rate, "check_fast_path", fast_paths)
    monkeypatch.setattr(stage.solver_path_gate, "check_solver_paths", gate)

    with pytest.raises(_Stop):
        stage.execute(_context(
            _values(**({stage.KEY_TENURE_OWNER_LIMITED_TO_DWELLINGS: True,
                        stage.KEY_TENURE_MIN_HOUSEHOLDS_OUTSIDE_DWELLINGS: 10.0}
                       if tenure_limit else {})),
            stage_path=tmp_path))

    # The stage always leaves its report file. Off: status disabled, the OFF path loads no
    # dwelling column. On: the dwelling column is requested from the parquet and the rule ran;
    # on the fixture cells it changes two cells (30 households / 2 dwellings and 118 / 0) and
    # moves (30 - 2) + (118 - 0) = 146 owner households to renters.
    entry = json.loads(
        (tmp_path / stage.CENSUS_FILTERED_REPORT_FILE).read_text(encoding="utf-8")
    )["tenure_owner_limited_to_dwellings"]
    dwelling_column = control_spec.OWNER_QUOTE_DWELLING_COLUMN
    if tenure_limit:
        assert dwelling_column in loaded_columns["columns"]
        assert entry["enabled"] is True and entry["status"] == "applied"
        assert entry["report"]["cells_changed"] == 2
        assert entry["report"]["households_moved_owner_to_renter"] == pytest.approx(146.0)
    else:
        assert dwelling_column not in loaded_columns["columns"]
        assert entry["enabled"] is False and entry["status"] == "disabled"
        assert entry["report"] is None
    # The fast-path rate is reported before the gate, which may stop the stage.
    assert calls == [
        "environment check", "contract", "purge", "batches", "log", "fast paths", "gate"]
    assert seen["fast_paths"] == (str(tmp_path / "work"), False)
    expected_overrides = {
        "KEEP_FRACTIONAL_SAMPLE_WEIGHTS": True, "INTEGERIZER_DRAW": "pareto",
        "INTEGERIZER_TIMEOUT": 600, "INTEGERIZER_PATH_RECORD": True,
        "INTEGERIZER_ROUNDING": "controlled", "INTEGERIZER_PENALTY_FLOOR": 100.0,
        "INTEGERIZER_PENALTY_CAP": 100000.0, "INTEGERIZER_PARENT_TOTAL_FACTOR": 10.0,
        "INTEGERIZER_CONSERVE_PENALTY": 100000.0, "INTEGERIZER_CONTROLLED_METHOD": "search",
        "INTEGERIZER_SEARCH_MAX_SWEEPS": 200, "INTEGERIZER_SEARCH_SHORTLIST": 24,
        "STORE_POSITIVE_WEIGHT_ROWS_ONLY": False, "FAST_PATHS": False,
    }
    # The last two positional arguments of the batch runner are the overrides and the seed base.
    assert seen["batches_args"][-2:] == (expected_overrides, 4711)
    # The purge (batch signature) gets the version, the verified commit, the lock hash,
    # overrides and seed base.
    assert seen["purge_args"][-5:] == (
        "0.10.0+bs.1", _COMMIT, _LOCK_HASH, expected_overrides, 4711)
    # The configured rounding mode is what the gate must find in the record.
    assert seen["gate"] == (str(tmp_path / "work"), 0.2, "controlled")
