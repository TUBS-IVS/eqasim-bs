"""The JVM heap must never exceed the machine, without touching the config."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig import resources  # noqa: E402

# 94.28 GB total (the run resource recorder's memory_total_kb; ``free -g``
# truncates the same machine to "94") -> 86.28 GB budget after the 8 GB reserve.
SERVER = resources.MachineResources(
    cores=64, memory_gb=94.28, cores_source="sched_getaffinity", memory_source="psutil",
)


@pytest.mark.parametrize("pin, expected", [
    pytest.param("100G", "86G", id="clamps_a_pin_larger_than_the_machine"),
    pytest.param("50G", "50G", id="leaves_a_fitting_pin_alone"),
    # A sub-gigabyte-granular string must NOT be reformatted (it would round
    # 1500M down to 1G).
    pytest.param("1500M", "1500M", id="echoes_a_string_pin_verbatim"),
])
def test_effective_java_memory_of_a_pin(pin, expected):
    assert resources.effective_java_memory(pin, machine=SERVER, env={}) == expected


def test_effective_java_memory_honours_an_explicit_budget():
    assert resources.effective_java_memory(
        "100G", machine=SERVER, env={resources.ENV_MEM_BUDGET: "40G"},
    ) == "40G"


def test_effective_java_memory_formats_a_numeric_pin_as_gigabytes():
    # parse_memory_gb reads a bare number as GIGABYTES, so `java_memory: 32`
    # means 32 GB -- but echoing str(32) produced "-Xmx32", which the JVM reads
    # as 32 BYTES and which fails to start the VM. The formatted "32G" is the
    # same quantity the resolver validated against the budget.
    assert resources.effective_java_memory(32, machine=SERVER, env={}) == "32G"
    assert resources.effective_java_memory(32.0, machine=SERVER, env={}) == "32G"


