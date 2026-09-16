"""The run start must log the resource report and refuse an impossible machine."""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig import resources  # noqa: E402

SERVER = resources.MachineResources(
    cores=64, memory_gb=94.0, cores_source="sched_getaffinity", memory_source="psutil",
)
TINY = resources.MachineResources(
    cores=4, memory_gb=9.0, cores_source="cpu_count", memory_source="psutil",
)
CONFIG = {
    "java_memory": "100G", "processes": 32,
    "braunschweig.population.popsim.num_workers": 3,
}


def test_enforce_logs_the_report_at_info(caplog):
    report = resources.build_report(CONFIG, machine=SERVER, env={})
    with caplog.at_level(logging.INFO):
        resources.enforce_report(report)
    assert any("[resources] machine:" in r.getMessage() for r in caplog.records)


def test_enforce_raises_on_an_impossible_machine():
    report = resources.build_report(CONFIG, machine=TINY, env={})
    with pytest.raises(resources.ResourceValidationError) as excinfo:
        resources.enforce_report(report)
    assert "num_workers" in str(excinfo.value)


def test_enforce_only_warns_on_a_warning_violation(caplog):
    small_cores = resources.MachineResources(
        cores=8, memory_gb=94.0, cores_source="cpu_count", memory_source="psutil",
    )
    report = resources.build_report({**CONFIG, "matsim_threads": 56},
                                    machine=small_cores, env={})
    with caplog.at_level(logging.WARNING):
        resources.enforce_report(report)          # must NOT raise
    assert any("matsim_threads" in r.getMessage() for r in caplog.records)
