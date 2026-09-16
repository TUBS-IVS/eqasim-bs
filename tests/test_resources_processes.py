"""The generic worker count is clamped down, and under-use is made visible."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig import resources  # noqa: E402

SERVER = resources.MachineResources(
    cores=64, memory_gb=94.0, cores_source="sched_getaffinity", memory_source="psutil",
)
LAPTOP = resources.MachineResources(
    cores=8, memory_gb=16.0, cores_source="cpu_count", memory_source="psutil",
)


def test_processes_pin_is_clamped_on_a_smaller_machine():
    assert resources.effective_processes(32, machine=LAPTOP, env={}) == 6


def test_processes_pin_that_fits_is_untouched():
    assert resources.effective_processes(32, machine=SERVER, env={}) == 32


def test_clamping_is_logged_as_a_warning(caplog):
    # CLAUDE.md: a clamp that fires silently is the failure mode this whole
    # mechanism exists to prevent.
    with caplog.at_level(logging.WARNING):
        resources.effective_processes(32, machine=LAPTOP, env={})
    assert any("processes" in record.getMessage() for record in caplog.records)


def test_a_fitting_pin_is_neither_changed_nor_warned_about(caplog):
    with caplog.at_level(logging.WARNING):
        result = resources.effective_processes(32, machine=SERVER, env={})
    assert result == 32
    assert not [r for r in caplog.records if "processes" in r.getMessage()]


def test_report_flags_a_processes_pin_that_wastes_the_machine():
    report = resources.build_report(
        {"java_memory": "50G", "processes": 32,
         "braunschweig.population.popsim.num_workers": 2},
        machine=SERVER, env={},
    )
    note = next(v for v in report.violations if v.key == "processes")
    assert note.severity == "warning"
    assert "32" in note.message and "62" in note.message


def test_report_does_not_flag_a_processes_pin_that_uses_the_machine():
    report = resources.build_report(
        {"java_memory": "50G", "processes": 60,
         "braunschweig.population.popsim.num_workers": 2},
        machine=SERVER, env={},
    )
    assert not [v for v in report.violations if v.key == "processes"]
