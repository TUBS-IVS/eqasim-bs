"""``processes`` must be hashed wherever it partitions; volatile only where it is a thread count.

Commit 79f7f492 ("Port upstream eqasim-france fixes identified by exhaustive sweep")
applied ``volatile=True`` to EVERY ``processes`` declaration, with the comment
"Execution detail, not scientific config: changing it must not devalidate cached
stages". At two of the four sites that premise is false: they hand ``processes`` to
``np.array_split`` and then draw one random seed per chunk, so the value decides
which persons land in which chunk AND which seed each chunk is solved with.
Measured on the two read sites' own arithmetic: going from ``processes: 8`` to
``processes: 32`` changes the seed of 19 of 20 persons.

A volatile key is excluded from the synpp stage hash. So at those two stages a
changed ``processes`` produced a different scientific realisation while the cache
happily served the old one -- the silent-stale-result failure the project's cache
discipline exists to prevent (ADR-0126, Decision 3).

These tests pin the classification at its source, and the last one derives it from
the code rather than from a hardcoded list, so a stage that STARTS partitioning in
future cannot quietly inherit the wrong flag.
"""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

#: Stages that hand ``processes`` to ``np.array_split``: the value is SCIENTIFIC there.
PARTITIONING_STAGES = (
    "synthesis.population.matched",
    "synthesis.population.spatial.secondary.locations",
)

#: Stages that only forward ``processes`` as a thread count to a Java tool: operational.
THREAD_COUNT_STAGES = (
    "matsim.scenario.supply.processed",
    "matsim.simulation.prepare",
)

_DECLARES_PROCESSES = re.compile(r"""context\.config\(\s*["']processes["']""")
_DECLARES_PROCESSES_VOLATILE = re.compile(
    r"""context\.config\(\s*["']processes["']\s*,\s*volatile\s*=\s*True""")
_PARTITIONS = re.compile(r"""array_split\(""")


class _RecordingContext:
    """configure()-time context: records declared options and volatile flags.

    Same shape as ``tests/test_java_hang_watchdog.py::_RecordingContext``.
    """

    def __init__(self):
        self.declared = {}
        self.volatile = set()

    def stage(self, name, *args, **kwargs):
        return None

    def config(self, name, *args, **kwargs):
        self.declared[name] = args[0] if args else None
        if kwargs.get("volatile"):
            self.volatile.add(name)
        return self.declared[name]


def _configure(module_name):
    ctx = _RecordingContext()
    importlib.import_module(module_name).configure(ctx)
    return ctx


@pytest.mark.parametrize("module_name", PARTITIONING_STAGES)
def test_a_partitioning_stage_declares_processes_as_hashed(module_name):
    ctx = _configure(module_name)
    assert "processes" in ctx.declared, f"{module_name} no longer declares processes"
    assert "processes" not in ctx.volatile, (
        f"{module_name} declares processes volatile, but it partitions with "
        f"np.array_split and seeds each chunk: a changed value would serve a cached "
        f"result built under a different partition")


@pytest.mark.parametrize("module_name", THREAD_COUNT_STAGES)
def test_a_thread_count_stage_keeps_processes_volatile(module_name):
    # The mirror image: these forward the value to a Java --threads / numOfThreads
    # argument and never partition, so hashing them would cost recomputes for
    # nothing.
    ctx = _configure(module_name)
    assert "processes" in ctx.declared, f"{module_name} no longer declares processes"
    assert "processes" in ctx.volatile, (
        f"{module_name} hashes processes although it only forwards it as a thread "
        f"count; that buys no correctness and costs a recompute on every retune")


def test_no_module_both_partitions_and_hides_processes_from_the_hash():
    """The invariant behind the two tests above, derived from the code itself.

    A future stage that starts calling ``np.array_split(..., processes)`` must not
    inherit the volatile flag, and this catches it without anyone remembering to
    extend a list.
    """
    declaring, offenders = [], []
    for path in sorted(REPO.rglob("*.py")):
        # Filter on the path RELATIVE to the repo: this checkout may itself live
        # under a `.claude/worktrees/...` directory, in which case filtering on the
        # absolute parts would skip every file and pass vacuously.
        relative = path.relative_to(REPO)
        if ".claude" in relative.parts or "tests" in relative.parts:
            continue
        source = path.read_text(encoding="utf-8", errors="replace")
        if not _DECLARES_PROCESSES.search(source):
            continue
        declaring.append(path.relative_to(REPO).as_posix())
        if _DECLARES_PROCESSES_VOLATILE.search(source) and _PARTITIONS.search(source):
            offenders.append(path.relative_to(REPO).as_posix())

    # Guard against a vacuous pass if the scan ever stops finding the modules.
    assert len(declaring) >= 4, f"expected the known processes readers, found {declaring}"
    assert offenders == [], (
        f"these modules declare processes volatile AND partition with array_split, "
        f"so a changed value serves a stale cached realisation: {offenders}")
