"""The JVM heap must never exceed the machine, without touching the config."""
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


def test_effective_java_memory_clamps_a_pin_larger_than_the_machine():
    assert resources.effective_java_memory("100G", machine=SERVER, env={}) == "86G"


def test_effective_java_memory_leaves_a_fitting_pin_alone():
    assert resources.effective_java_memory("50G", machine=SERVER, env={}) == "50G"


def test_effective_java_memory_honours_an_explicit_budget():
    assert resources.effective_java_memory(
        "100G", machine=SERVER, env={resources.ENV_MEM_BUDGET: "40G"},
    ) == "40G"
