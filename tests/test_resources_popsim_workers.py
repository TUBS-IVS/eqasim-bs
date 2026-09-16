"""PopulationSim workers are bounded by memory, and clamping is announced."""
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


def test_three_configured_workers_become_two_on_the_shrunken_server():
    assert resources.effective_popsim_workers(
        3, worker_memory_gb=30.0, machine=SERVER, env={}) == 2


def test_clamping_is_logged_as_a_warning(caplog):
    # CLAUDE.md: a fallback/clamp that fires silently is the failure mode this
    # whole mechanism exists to prevent.
    with caplog.at_level(logging.WARNING):
        resources.effective_popsim_workers(3, worker_memory_gb=30.0, machine=SERVER, env={})
    # getMessage() renders the %-args; record.message only exists after a handler
    # formatted the record, which is an implementation detail of caplog.
    assert any("num_workers" in record.getMessage() for record in caplog.records)


def test_a_fitting_pin_is_neither_changed_nor_warned_about(caplog):
    with caplog.at_level(logging.WARNING):
        result = resources.effective_popsim_workers(
            2, worker_memory_gb=30.0, machine=SERVER, env={})
    assert result == 2
    assert not [r for r in caplog.records if "num_workers" in r.getMessage()]
