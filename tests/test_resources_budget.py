"""Tests for budget derivation and per-key resolution in braunschweig.resources.

Every case injects a MachineResources, so nothing depends on the machine the
suite runs on. The server measured on 2026-09-16 (64 cores, 94 GB) is used as
the realistic case throughout.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig import resources  # noqa: E402

SERVER = resources.MachineResources(
    cores=64, memory_gb=94.0, cores_source="sched_getaffinity", memory_source="psutil",
)
LAPTOP = resources.MachineResources(
    cores=8, memory_gb=16.0, cores_source="cpu_count", memory_source="psutil",
)


def test_budget_reserves_cores_and_memory_for_the_os():
    budget = resources.resolve_budget(SERVER, env={})
    assert budget.cores == 62            # 64 - DEFAULT_CORE_RESERVE
    assert budget.memory_gb == pytest.approx(86.0)   # 94 - DEFAULT_MEMORY_RESERVE_GB
    assert budget.machine is SERVER


def test_budget_never_drops_below_one_core_or_one_gigabyte():
    tiny = resources.MachineResources(
        cores=1, memory_gb=2.0, cores_source="cpu_count", memory_source="psutil",
    )
    budget = resources.resolve_budget(tiny, env={})
    assert budget.cores == 1
    assert budget.memory_gb >= 1.0


def test_environment_overrides_win_over_detection():
    budget = resources.resolve_budget(
        SERVER, env={resources.ENV_CPU_BUDGET: "16", resources.ENV_MEM_BUDGET: "40G"},
    )
    # An explicit budget is what the run may use -- no further reserve is taken
    # off it, because the operator already decided how much of the box to claim.
    assert budget.cores == 16
    assert budget.memory_gb == pytest.approx(40.0)


@pytest.mark.parametrize("value", [None, 0, "", "auto", "AUTO"])
def test_is_auto_recognises_every_sentinel_spelling(value):
    assert resources.is_auto(value) is True


@pytest.mark.parametrize("value", [1, 3, "62", "100G"])
def test_is_auto_rejects_real_values(value):
    assert resources.is_auto(value) is False


def test_java_memory_pin_that_fits_is_passed_through_verbatim():
    budget = resources.resolve_budget(SERVER, env={})
    result = resources.resolve_java_memory("50G", budget)
    assert result.effective == "50G"
    assert result.origin == "pinned"


def test_java_memory_pin_that_exceeds_the_machine_is_clamped():
    # The live 2026-09-16 defect: 100G configured on a 94 GB box.
    budget = resources.resolve_budget(SERVER, env={})
    result = resources.resolve_java_memory("100G", budget)
    assert result.effective == "86G"
    assert result.origin == "clamped"
    assert "100G" in result.note and "86G" in result.note


def test_java_memory_auto_is_derived_from_the_budget():
    budget = resources.resolve_budget(SERVER, env={})
    result = resources.resolve_java_memory("auto", budget)
    assert result.effective == "86G"
    assert result.origin == "derived"


def test_popsim_workers_are_bounded_by_memory_not_by_cores():
    # 86 GB budget / 30 GB per worker -> 2, even though 62 cores are free.
    budget = resources.resolve_budget(SERVER, env={})
    result = resources.resolve_popsim_workers(3, budget, worker_memory_gb=30.0)
    assert result.effective == 2
    assert result.origin == "clamped"


def test_popsim_workers_keep_a_pin_that_fits():
    budget = resources.resolve_budget(SERVER, env={})
    result = resources.resolve_popsim_workers(2, budget, worker_memory_gb=30.0)
    assert result.effective == 2
    assert result.origin == "pinned"


def test_popsim_workers_never_drop_below_one():
    budget = resources.resolve_budget(LAPTOP, env={})
    result = resources.resolve_popsim_workers(3, budget, worker_memory_gb=30.0)
    assert result.effective == 1


def test_popsim_workers_auto_is_bounded_by_cores_as_well():
    small_cores = resources.MachineResources(
        cores=3, memory_gb=500.0, cores_source="cpu_count", memory_source="psutil",
    )
    budget = resources.resolve_budget(small_cores, env={})
    result = resources.resolve_popsim_workers("auto", budget, worker_memory_gb=30.0)
    assert result.effective == 1   # cores 3 - reserve 2 = 1, below the memory bound
    assert result.origin == "derived"


def test_processes_auto_uses_the_core_budget():
    budget = resources.resolve_budget(SERVER, env={})
    result = resources.resolve_processes("auto", budget)
    assert result.effective == 62
    assert result.origin == "derived"


def test_processes_pin_above_the_core_budget_is_clamped():
    budget = resources.resolve_budget(LAPTOP, env={})
    result = resources.resolve_processes(32, budget)
    assert result.effective == 6      # 8 cores - reserve 2
    assert result.origin == "clamped"


def test_a_negative_count_is_rejected_rather_than_treated_as_auto():
    budget = resources.resolve_budget(SERVER, env={})
    with pytest.raises(ValueError):
        resources.resolve_processes(-1, budget)


def test_a_non_positive_java_memory_is_rejected():
    budget = resources.resolve_budget(SERVER, env={})
    with pytest.raises(ValueError):
        resources.resolve_java_memory("0G", budget)


def test_a_fitting_java_memory_pin_is_echoed_verbatim_not_reformatted():
    # Reformatting a pin that nothing clamped would silently shrink it:
    # format_memory_gb("1500M") floors 1.46 GB to "1G".
    budget = resources.resolve_budget(SERVER, env={})
    result = resources.resolve_java_memory("1500M", budget)
    assert result.effective == "1500M"
    assert result.origin == "pinned"
