"""The JVM heap leaves room for the synpp driver process (finding C2 of production run prod100_20261031).

Synthesis and MATSim ran in ONE ``scripts/run_synpp.py`` invocation, so the driver still held memory when the
JVM was started, but the clamp compared ``java_memory`` with the machine budget alone and never subtracted it
(measurements: ``docs/runs/prod100-java-heap-driver-rss-2026-11-04.yml``).

Every machine figure is injected (budget through ``EQASIM_MEM_BUDGET``, the driver RSS by replacing the
reader), so no test reads the real machine. See ``docs/codebase/notes/resource-budget.md``.
"""
from __future__ import annotations

import logging
import sys
import types
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import matsim.runtime.java as java  # noqa: E402
from braunschweig import java_heap, resources  # noqa: E402

# The production server: 94.28 GiB total (the run resource recorder's memory_total_kb) -> an 86.28 GiB
# budget after the 8 GiB reserve.
SERVER = resources.MachineResources(
    cores=64, memory_gb=94.28, cores_source="sched_getaffinity", memory_source="psutil",
)


class _RecordingContext:
    """The slice of a synpp ``ExecuteContext`` that ``java.run`` touches."""

    def __init__(self, work_dir, java_memory):
        self._work_dir = str(work_dir)
        self._config = {
            "java_binary": "java",
            "java_memory": java_memory,
            java.KEY_HANG_TIMEOUT: 0,
            java.KEY_HANG_MIN_CPU: 1.0,
        }

    def stage(self, name):
        return None

    def path(self, name=None):
        return self._work_dir

    def config(self, key):
        return self._config[key]


def _launch_java(monkeypatch, tmp_path, *, java_memory, budget_gb, driver_rss_gb):
    """Run ``java.run`` against a recorded command line and return it with the context it ran in.

    The budget is pinned through the operator override (``EQASIM_MEM_BUDGET`` / ``EQASIM_CPU_BUDGET``,
    taken verbatim) and the driver RSS through the reader, so the figures are exactly the ones named
    in the call.
    """
    monkeypatch.setenv(resources.ENV_MEM_BUDGET, str(budget_gb))
    monkeypatch.setenv(resources.ENV_CPU_BUDGET, "4")
    monkeypatch.setattr(resources, "current_process_rss_gb", lambda: driver_rss_gb)

    launched = []

    class _Popen:
        def __init__(self, command_line, cwd=None):
            launched.append(list(command_line))

    # java.py reaches subprocess and shutil through these two names only; replacing them in the module
    # keeps the real subprocess module untouched.
    monkeypatch.setattr(java, "sp", types.SimpleNamespace(
        Popen=_Popen, CalledProcessError=RuntimeError))
    monkeypatch.setattr(java, "shutil", types.SimpleNamespace(which=lambda binary: "/opt/jdk/bin/java"))
    monkeypatch.setattr(java.process_watchdog, "wait_with_hang_watchdog",
                        lambda process, **kwargs: 0)

    context = _RecordingContext(tmp_path, java_memory)
    java.run(context, "org.example.Main", ["--flag"], class_path="tool.jar")
    assert len(launched) == 1
    return launched[0], context


def _xmx(command_line):
    flags = [argument for argument in command_line if argument.startswith("-Xmx")]
    assert len(flags) == 1, command_line
    return flags[0]


def test_java_run_leaves_the_heap_room_for_the_driver_the_run_already_holds(monkeypatch, tmp_path):
    # Budget 86 GiB, driver 15.5 GiB: 70.5 GiB are left, so the 100G pin of the production config is cut to
    # 70G. The unfixed clamp only saw the budget and launched the JVM with -Xmx86G.
    command_line, context = _launch_java(monkeypatch, tmp_path, java_memory="100G",
                                         budget_gb=86, driver_rss_gb=15.5)
    assert _xmx(command_line) == "-Xmx70G"
    # java_memory is part of the matsim.runtime.java stage hash: the clamp must never write back.
    assert context.config("java_memory") == "100G"


@pytest.mark.parametrize("configured, driver_rss_gb, expected", [
    # A driver that holds nothing leaves the machine-only clamp of before C2 untouched.
    pytest.param("100G", 0.0, "86G", id="idle_driver_changes_nothing"),
    # The driver RSS recorded for prod100_20261031 (see the manifest): 86.28 - 14.75 = 71.53 GiB are left,
    # floored to whole gigabytes.
    pytest.param("100G", 14.75, "71G", id="driver_of_prod100"),
    # 75G fits the 86.28 GiB machine budget, so the old clamp kept it; it does not fit what is left.
    pytest.param("75G", 14.75, "71G", id="pin_below_the_budget_is_cut_too"),
    pytest.param("60G", 14.75, "60G", id="pin_that_fits_is_left_alone"),
    pytest.param("1500M", 14.75, "1500M", id="string_pin_is_echoed_verbatim"),
    # A bare number means gigabytes; echoing str(32) would hand the JVM 32 BYTES.
    pytest.param(32, 14.75, "32G", id="numeric_pin_is_formatted_as_gigabytes"),
    pytest.param("auto", 14.75, "71G", id="auto_takes_everything_that_is_left"),
    # The driver alone exceeds the budget: nothing sensible is left, the 1 GB floor of the budget applies.
    pytest.param("100G", 90.0, "1G", id="driver_beyond_the_budget_floors_at_1G"),
])
def test_effective_java_heap_subtracts_the_driver_rss_from_the_budget(
        configured, driver_rss_gb, expected):
    assert java_heap.effective_java_heap(
        configured, machine=SERVER, env={}, driver_rss_gb=driver_rss_gb) == expected


def test_the_driver_rss_is_measured_when_not_injected_and_every_figure_is_logged(monkeypatch, caplog):
    # PRIMARY path: the reader answers, so the heap is the driver-aware 71G (the fallback would give 86G)
    # and no fallback warning appears. One line carries the budget, the driver RSS and the result.
    monkeypatch.setattr(resources, "current_process_rss_gb", lambda: 14.75)
    with caplog.at_level(logging.INFO, logger=java_heap.logger.name):
        heap = java_heap.effective_java_heap("100G", machine=SERVER, env={})
    assert heap == "71G"
    [record] = caplog.records
    message = record.getMessage()
    assert all(figure in message for figure in ("86.28", "14.75", "71G")), message
    assert "could not be measured" not in message


def test_an_unmeasurable_driver_rss_warns_and_bounds_the_heap_by_the_machine_budget_alone(
        monkeypatch, caplog):
    # CLAUDE.md: no silent fallbacks. The behaviour before C2 stays available, but it is loud.
    monkeypatch.setattr(resources, "current_process_rss_gb", lambda: None)
    with caplog.at_level(logging.INFO, logger=java_heap.logger.name):
        heap = java_heap.effective_java_heap("100G", machine=SERVER, env={})
    assert heap == "86G"
    [record] = caplog.records
    assert record.levelno == logging.WARNING
    assert "could not be measured" in record.getMessage()


def test_a_negative_driver_rss_is_rejected_instead_of_inflating_the_heap():
    with pytest.raises(ValueError, match="driver_rss_gb"):
        java_heap.effective_java_heap("100G", machine=SERVER, env={}, driver_rss_gb=-1.0)
