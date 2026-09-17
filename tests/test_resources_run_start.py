"""``enforce_report`` logs a resource report and refuses an impossible machine.

SCOPE, stated precisely: every test in this file calls
``braunschweig.resources.enforce_report`` DIRECTLY. Nothing here imports or runs
``scripts/run_synpp.py``, so this file does NOT cover the run start itself --
that the launcher builds a report, enforces it and passes it into the run
provenance is pinned by
``tests/test_run_synpp_arity.py::test_main_hands_the_resource_report_to_the_provenance_record``
and by ``tests/test_run_provenance.py``'s resource-report cases. The earlier
docstring here ("the run start must ...") claimed coverage this file never had.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig import resources  # noqa: E402

# The run server as the run resource recorder measures it (94.28 GB total;
# ``free -g`` truncates it to "94"), so the budget is 94.28 - 8 = 86.28 GB.
SERVER = resources.MachineResources(
    cores=64, memory_gb=94.28, cores_source="sched_getaffinity", memory_source="psutil",
)
TINY = resources.MachineResources(
    cores=4, memory_gb=9.0, cores_source="cpu_count", memory_source="psutil",
)
CONFIG = {
    "java_memory": "100G", "processes": 32,
    "braunschweig.population.popsim.num_workers": 3,
    "braunschweig.population.method": "popsim_mid",
    # The 100 % scale DEFAULT_POPSIM_WORKER_MEMORY_GB was measured at -- the only
    # scale at which a mismatch against it is an ERROR (build_report, final review
    # B2). Without it these cases would only warn.
    "sampling_rate": 1.0,
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


def test_enforce_does_not_raise_below_the_measured_sampling_rate():
    # The scale gate (final review B2): the same impossible combination at 1 %
    # must not abort, because the 30 GB/worker figure is a 100 %-scale
    # measurement and nothing has measured the footprint at 1 %.
    report = resources.build_report(dict(CONFIG, sampling_rate=0.01),
                                    machine=TINY, env={})
    resources.enforce_report(report)  # must NOT raise


def test_enforce_does_not_raise_when_popsim_is_not_selected():
    # IMPORTANT 2 (final review): the same impossible num_workers/memory
    # combination must not abort a run that never selects a PopulationSim
    # workflow (e.g. a MATSim-only overlay, which never sets population.method
    # at all).
    config = {k: v for k, v in CONFIG.items()
             if k != "braunschweig.population.method"}
    report = resources.build_report(config, machine=TINY, env={})
    resources.enforce_report(report)  # must NOT raise


def test_enforce_only_warns_on_a_warning_violation(caplog):
    small_cores = resources.MachineResources(
        cores=8, memory_gb=94.0, cores_source="cpu_count", memory_source="psutil",
    )
    report = resources.build_report({**CONFIG, "matsim_threads": 56},
                                    machine=small_cores, env={})
    with caplog.at_level(logging.WARNING):
        resources.enforce_report(report)          # must NOT raise
    assert any("matsim_threads" in r.getMessage() for r in caplog.records)


def test_enforce_logs_each_warning_exactly_once(caplog):
    # Final review minor: format_log() embeds a "WARNING: <message>" line at
    # INFO for a human-readable full report, and enforce_report ALSO logs
    # every warning violation at WARNING level. Both together must not mean
    # the same message is logged twice at WARNING severity.
    small_cores = resources.MachineResources(
        cores=8, memory_gb=94.0, cores_source="cpu_count", memory_source="psutil",
    )
    report = resources.build_report({**CONFIG, "matsim_threads": 56},
                                    machine=small_cores, env={})
    with caplog.at_level(logging.WARNING):
        resources.enforce_report(report)
    matches = [r for r in caplog.records
              if r.levelno == logging.WARNING and "matsim_threads" in r.getMessage()]
    assert len(matches) == 1
