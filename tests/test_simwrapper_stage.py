"""Unit tests for the braunschweig.analysis.simwrapper_export synpp stage.

All tests use a lightweight FakeContext so no real synpp runtime or file I/O is
needed.  export_all is monkeypatched throughout so no real dashboard files are
written.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import braunschweig.analysis.simwrapper_export as stage
import braunschweig.analysis.simwrapper.export as _export_mod


# ---------------------------------------------------------------------------
# Minimal fake synpp context
# ---------------------------------------------------------------------------

class FakeContext:
    """Minimal synpp context stub for configure/execute unit tests."""

    def __init__(self, config: dict, paths: dict | None = None,
                stage_results: dict | None = None):
        self._config = config
        self._paths = paths or {}
        self._stage_results = stage_results or {}
        # Records every stage name passed to context.stage().
        self.stages: list[str] = []

    def config(self, key, default=None):
        return self._config.get(key, default)

    def stage(self, name):
        self.stages.append(name)
        return self._stage_results.get(name)

    def path(self, name):
        return self._paths[name]


# ---------------------------------------------------------------------------
# Test 1 — configure without MATSim: synthesis.output registered, NOT matsim.simulation.run
# ---------------------------------------------------------------------------

def test_configure_no_matsim_registers_synthesis_output_only():
    ctx = FakeContext({"simwrapper_include_matsim": False,
                       "simwrapper_export_enabled": True,
                       "output_path": "/tmp/out",
                       "sampling_rate": 0.25})
    stage.configure(ctx)
    assert "synthesis.output" in ctx.stages
    assert "matsim.simulation.run" not in ctx.stages


def test_configure_matsim_absent_in_config_registers_synthesis_output_only():
    """simwrapper_include_matsim defaults to False; same expectation."""
    ctx = FakeContext({"output_path": "/tmp/out", "sampling_rate": 0.25})
    stage.configure(ctx)
    assert "synthesis.output" in ctx.stages
    assert "matsim.simulation.run" not in ctx.stages


# ---------------------------------------------------------------------------
# Test 2 — configure with MATSim: still NO matsim.simulation.run edge (#354)
# ---------------------------------------------------------------------------

def test_configure_with_matsim_declares_no_run_stage_dependency():
    """simwrapper_include_matsim is a SIGNAL ('this run has MATSim outputs'),
    not a dependency: the sim outputs are read from the <output_path>/
    matsim_output archive, so no invocation may pull in the simulation chain
    just to render dashboards (issue #354)."""
    ctx = FakeContext({"simwrapper_include_matsim": True,
                       "simwrapper_export_enabled": True,
                       "output_path": "/tmp/out",
                       "sampling_rate": 0.25})
    stage.configure(ctx)
    assert "synthesis.output" in ctx.stages
    assert "matsim.simulation.run" not in ctx.stages


# ---------------------------------------------------------------------------
# Test 2b — configure with cordon: student_incommuters stage registered (#140)
# ---------------------------------------------------------------------------

def test_configure_cordon_off_does_not_register_student_incommuters():
    """Default (cordon_enabled absent/False): no new stage dependency, so the
    byte-identical baseline dependency graph is preserved."""
    ctx = FakeContext({"output_path": "/tmp/out", "sampling_rate": 0.25})
    stage.configure(ctx)
    assert "braunschweig.synthesis.student_incommuters" not in ctx.stages


def test_configure_cordon_on_registers_student_incommuters():
    ctx = FakeContext({"cordon_enabled": True,
                       "output_path": "/tmp/out",
                       "sampling_rate": 0.25})
    stage.configure(ctx)
    assert "braunschweig.synthesis.student_incommuters" in ctx.stages


# ---------------------------------------------------------------------------
# Test 3 — execute disabled: returns None, does NOT call export_all
# ---------------------------------------------------------------------------

def test_execute_disabled_returns_none_without_calling_export_all(monkeypatch):
    """When the flag is False, execute() must return None and never import/call export_all."""

    def _should_not_be_called(*args, **kwargs):
        raise AssertionError("export_all must not be called when the stage is disabled")

    monkeypatch.setattr(_export_mod, "export_all", _should_not_be_called)

    ctx = FakeContext({"simwrapper_export_enabled": False,
                       "simwrapper_include_matsim": False,
                       "output_path": "/tmp/out",
                       "sampling_rate": 0.25})
    result = stage.execute(ctx)
    assert result is None


# ---------------------------------------------------------------------------
# Test 4 — execute enabled, synthesis-only: sim_cache is None, returns stringified paths
# ---------------------------------------------------------------------------

def test_execute_synthesis_only_calls_export_all_with_no_sim_cache(monkeypatch, tmp_path):
    captured: dict = {}

    def _fake_export_all(output_dir, sim_cache=None, label=None, sample_rate=None,
                         out_subdir="simwrapper", student_frames=None):
        captured["output_dir"] = output_dir
        captured["sim_cache"] = sim_cache
        captured["sample_rate"] = sample_rate
        captured["student_frames"] = student_frames
        return [Path("x"), Path("y")]

    monkeypatch.setattr(_export_mod, "export_all", _fake_export_all)

    out = str(tmp_path)
    ctx = FakeContext({"simwrapper_export_enabled": True,
                       "simwrapper_include_matsim": False,
                       "output_path": out,
                       "sampling_rate": 0.25})
    result = stage.execute(ctx)

    assert captured["sim_cache"] is None, "sim_cache must be None in synthesis-only mode"
    assert captured["sample_rate"] == pytest.approx(0.25)
    assert captured["student_frames"] is None, (
        "cordon off (default) must not thread any student_frames through")
    assert result == ["x", "y"], "execute() must return a list of stringified paths"


# ---------------------------------------------------------------------------
# Test 4b — execute with cordon: student_incommuters stage output is pulled
# and threaded through to export_all as student_frames (#140)
# ---------------------------------------------------------------------------

def test_execute_cordon_on_threads_student_frames_through(monkeypatch, tmp_path):
    captured: dict = {}

    def _fake_export_all(output_dir, sim_cache=None, label=None, sample_rate=None,
                         out_subdir="simwrapper", student_frames=None):
        captured["student_frames"] = student_frames
        return [Path("x")]

    monkeypatch.setattr(_export_mod, "export_all", _fake_export_all)

    sentinel_frames = {"persons": "PERSONS_FRAME", "locations": "LOCATIONS_FRAME"}
    out = str(tmp_path)
    ctx = FakeContext(
        {"simwrapper_export_enabled": True,
         "simwrapper_include_matsim": False,
         "cordon_enabled": True,
         "output_path": out,
         "sampling_rate": 0.25},
        stage_results={"braunschweig.synthesis.student_incommuters": sentinel_frames},
    )
    stage.execute(ctx)

    assert captured["student_frames"] is sentinel_frames
    assert "braunschweig.synthesis.student_incommuters" in ctx.stages


# ---------------------------------------------------------------------------
# Test 5 — execute with MATSim: sim_cache is the matsim_output archive (#354)
# ---------------------------------------------------------------------------

def test_execute_with_matsim_passes_archive_as_sim_cache(monkeypatch, tmp_path):
    """The sim outputs are resolved from the <output_path>/matsim_output
    archive written by matsim.output -- config-derived, never via
    context.path('matsim.simulation.run') (paths stays empty on purpose)."""
    captured: dict = {}

    def _fake_export_all(output_dir, sim_cache=None, label=None, sample_rate=None,
                         out_subdir="simwrapper", student_frames=None):
        captured["sim_cache"] = sim_cache
        captured["sample_rate"] = sample_rate
        return [Path("z")]

    monkeypatch.setattr(_export_mod, "export_all", _fake_export_all)

    out = tmp_path / "output"
    archive = out / "matsim_output"
    archive.mkdir(parents=True)
    # matsim.output asserts this file exists after archiving.
    (archive / "output_events.xml.gz").write_bytes(b"")

    ctx = FakeContext(
        {"simwrapper_export_enabled": True,
         "simwrapper_include_matsim": True,
         "output_path": str(out),
         "sampling_rate": 1.0},
    )
    result = stage.execute(ctx)

    assert captured["sim_cache"] == str(archive)
    assert captured["sample_rate"] == pytest.approx(1.0)
    assert result == ["z"]


def test_execute_with_matsim_but_no_archive_skips_matsim_tabs(monkeypatch, tmp_path):
    """simwrapper_include_matsim=True but no archive on disk: export_all must
    receive sim_cache=None (its MATSim tabs then skip loudly) instead of the
    stage failing or recomputing the simulation."""
    captured: dict = {}

    def _fake_export_all(output_dir, sim_cache=None, label=None, sample_rate=None,
                         out_subdir="simwrapper", student_frames=None):
        captured["sim_cache"] = sim_cache
        return [Path("z")]

    monkeypatch.setattr(_export_mod, "export_all", _fake_export_all)

    ctx = FakeContext(
        {"simwrapper_export_enabled": True,
         "simwrapper_include_matsim": True,
         "output_path": str(tmp_path / "output"),
         "sampling_rate": 1.0},
    )
    stage.execute(ctx)

    assert captured["sim_cache"] is None
