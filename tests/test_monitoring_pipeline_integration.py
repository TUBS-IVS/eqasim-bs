"""The recorder runs with every pipeline run, and the watchdog shares its CPU signal.

Two wirings are pinned here (issue #350):

1. **One definition of "is this tree doing work".** The Java hang watchdog
   (issue #330) judged a single process, so a JVM that forks a helper looked idle
   while the helper was computing. It now consumes the same whole-tree reader the
   recorder uses, which is what makes "no CPU in the tree" a trustworthy kill
   criterion instead of a per-process artefact.
2. **Recording is not something anyone has to remember.** ``scripts/run_synpp.py``
   starts the recorder around the whole run, so the series exists for every run --
   including the ones that die, which are the ones whose measurement matters most.
"""
from __future__ import annotations

import importlib.util
import os

import pytest
import yaml

import matsim.runtime.process_watchdog as watchdog
from braunschweig.monitoring import process_tree
from tests.fake_proc import write_process, write_system

_RUN_SYNPP_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "run_synpp.py")


class _Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def idle_jvm_with_busy_child(tmp_path, monkeypatch):
    """A parent at ~0 % CPU whose child is computing -- the shape of a forked helper."""
    proc_root = tmp_path / "proc"
    proc_root.mkdir()
    write_system(proc_root)
    write_process(proc_root, pid=4242, ppid=1, utime_ticks=1000, state="S",
                  cmdline="java -jar eqasim.jar")
    write_process(proc_root, pid=4243, ppid=4242, utime_ticks=1000,
                  cmdline="java worker")
    monkeypatch.setattr(process_tree, "DEFAULT_PROC_ROOT", str(proc_root))
    return proc_root


def _advance_child_cpu(proc_root, ticks):
    write_process(proc_root, pid=4243, ppid=4242, utime_ticks=ticks,
                  cmdline="java worker")


def test_the_hang_watchdog_sees_a_busy_child_of_an_idle_parent_as_working(
        idle_jvm_with_busy_child):
    clock = _Clock()
    hang_watchdog = watchdog.HangWatchdog(4242, hang_timeout_s=900.0,
                                          min_cpu_seconds=1.0, monotonic=clock)

    _advance_child_cpu(idle_jvm_with_busy_child, ticks=100_000)
    clock.advance(60.0)

    assert hang_watchdog.sample() == watchdog.STATE_WORKING


def test_a_per_process_reader_would_have_called_that_same_tree_idle(
        idle_jvm_with_busy_child):
    """Why the shared reader matters: the old signal misses the work entirely."""
    clock = _Clock()
    hang_watchdog = watchdog.HangWatchdog(
        4242, hang_timeout_s=900.0, min_cpu_seconds=1.0, monotonic=clock,
        cpu_seconds_reader=process_tree.read_process_cpu_seconds)

    _advance_child_cpu(idle_jvm_with_busy_child, ticks=100_000)
    clock.advance(60.0)

    assert hang_watchdog.sample() == watchdog.STATE_IDLE


def test_a_tree_with_no_cpu_growth_at_all_is_still_declared_hung(
        idle_jvm_with_busy_child):
    """The guard must keep working: sharing the reader may not weaken it."""
    clock = _Clock()
    hang_watchdog = watchdog.HangWatchdog(4242, hang_timeout_s=900.0,
                                          min_cpu_seconds=1.0, monotonic=clock)

    clock.advance(1000.0)

    assert hang_watchdog.sample() == watchdog.STATE_HUNG


def _load_run_synpp():
    spec = importlib.util.spec_from_file_location("run_synpp_cli", _RUN_SYNPP_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_a_pipeline_run_records_a_series_into_its_working_directory(tmp_path, monkeypatch):
    import braunschweig.logging_setup as logging_setup
    import braunschweig.provenance as provenance
    import synpp

    working_directory = tmp_path / "work"
    log_path = tmp_path / "run.log"
    log_path.write_text("Executing stage braunschweig.data.first\n", encoding="utf-8")
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump({
        "working_directory": str(working_directory),
        "run": ["synthesis.output"],
        "config": {"monitoring_interval_seconds": 0.0, "monitoring_kernel_events": False},
    }), encoding="utf-8")

    executed = []
    monkeypatch.setattr(logging_setup, "setup_logging", lambda **kwargs: str(log_path))
    monkeypatch.setattr(provenance, "log_and_write_run_provenance", lambda path: None)
    monkeypatch.setattr(synpp, "run_from_yaml",
                        lambda *args, **kwargs: executed.append(args))

    run_synpp = _load_run_synpp()
    monkeypatch.setattr(run_synpp, "prime_from_config", lambda path: None)
    monkeypatch.setattr(run_synpp, "export_to_store_from_config", lambda path: None)

    exit_code = run_synpp.main([str(config_path)])

    assert exit_code == 0 and executed
    series_files = list((working_directory / "monitoring").glob("resource_series_*.jsonl"))
    assert len(series_files) == 1
    assert series_files[0].read_text(encoding="utf-8").strip()


def test_the_recorded_series_watches_the_run_log_of_that_run(tmp_path, monkeypatch):
    """The stage tag and the liveness signal both come from the run's own log."""
    import json

    import braunschweig.logging_setup as logging_setup
    import braunschweig.provenance as provenance
    import synpp

    working_directory = tmp_path / "work"
    log_path = tmp_path / "run.log"
    log_path.write_text("Executing stage braunschweig.popsim.batch\n", encoding="utf-8")
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump({
        "working_directory": str(working_directory),
        "config": {"monitoring_interval_seconds": 0.0, "monitoring_kernel_events": False},
    }), encoding="utf-8")

    monkeypatch.setattr(logging_setup, "setup_logging", lambda **kwargs: str(log_path))
    monkeypatch.setattr(provenance, "log_and_write_run_provenance", lambda path: None)
    monkeypatch.setattr(synpp, "run_from_yaml", lambda *args, **kwargs: None)

    run_synpp = _load_run_synpp()
    monkeypatch.setattr(run_synpp, "prime_from_config", lambda path: None)
    monkeypatch.setattr(run_synpp, "export_to_store_from_config", lambda path: None)

    run_synpp.main([str(config_path)])

    series = list((working_directory / "monitoring").glob("resource_series_*.jsonl"))[0]
    first_row = json.loads(series.read_text(encoding="utf-8").splitlines()[0])
    assert first_row["stage"] == "braunschweig.popsim.batch"
    assert first_row["log_size_bytes"] == log_path.stat().st_size


def test_the_summary_artifacts_are_written_when_the_run_ends(tmp_path, monkeypatch):
    import braunschweig.logging_setup as logging_setup
    import braunschweig.provenance as provenance
    import synpp

    working_directory = tmp_path / "work"
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump({
        "working_directory": str(working_directory),
        "config": {"monitoring_interval_seconds": 0.0, "monitoring_kernel_events": False},
    }), encoding="utf-8")

    monkeypatch.setattr(logging_setup, "setup_logging",
                        lambda **kwargs: str(tmp_path / "run.log"))
    monkeypatch.setattr(provenance, "log_and_write_run_provenance", lambda path: None)
    monkeypatch.setattr(synpp, "run_from_yaml", lambda *args, **kwargs: None)

    run_synpp = _load_run_synpp()
    monkeypatch.setattr(run_synpp, "prime_from_config", lambda path: None)
    monkeypatch.setattr(run_synpp, "export_to_store_from_config", lambda path: None)

    run_synpp.main([str(config_path)])

    summaries = list((working_directory / "monitoring").glob("*.summary.md"))
    assert len(summaries) == 1


def test_a_failing_run_still_leaves_the_series_and_the_summary(tmp_path, monkeypatch):
    """A killed or failed run is exactly the one whose resource record is wanted."""
    import braunschweig.logging_setup as logging_setup
    import braunschweig.provenance as provenance
    import synpp

    working_directory = tmp_path / "work"
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump({
        "working_directory": str(working_directory),
        "config": {"monitoring_interval_seconds": 0.0, "monitoring_kernel_events": False},
    }), encoding="utf-8")

    def _fail(*args, **kwargs):
        raise RuntimeError("stage exploded")

    monkeypatch.setattr(logging_setup, "setup_logging",
                        lambda **kwargs: str(tmp_path / "run.log"))
    monkeypatch.setattr(provenance, "log_and_write_run_provenance", lambda path: None)
    monkeypatch.setattr(synpp, "run_from_yaml", _fail)

    run_synpp = _load_run_synpp()
    monkeypatch.setattr(run_synpp, "prime_from_config", lambda path: None)
    monkeypatch.setattr(run_synpp, "export_to_store_from_config", lambda path: None)

    with pytest.raises(RuntimeError):
        run_synpp.main([str(config_path)])

    assert list((working_directory / "monitoring").glob("resource_series_*.jsonl"))
    assert list((working_directory / "monitoring").glob("*.summary.json"))


def test_monitoring_switched_off_in_the_config_leaves_no_trace(tmp_path, monkeypatch):
    import braunschweig.logging_setup as logging_setup
    import braunschweig.provenance as provenance
    import synpp

    working_directory = tmp_path / "work"
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump({
        "working_directory": str(working_directory),
        "config": {"monitoring_enabled": False},
    }), encoding="utf-8")

    monkeypatch.setattr(logging_setup, "setup_logging",
                        lambda **kwargs: str(tmp_path / "run.log"))
    monkeypatch.setattr(provenance, "log_and_write_run_provenance", lambda path: None)
    monkeypatch.setattr(synpp, "run_from_yaml", lambda *args, **kwargs: None)

    run_synpp = _load_run_synpp()
    monkeypatch.setattr(run_synpp, "prime_from_config", lambda path: None)
    monkeypatch.setattr(run_synpp, "export_to_store_from_config", lambda path: None)

    assert run_synpp.main([str(config_path)]) == 0
    assert not (working_directory / "monitoring").exists()
