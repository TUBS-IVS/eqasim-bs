"""``braunschweig.chainsolvers.processes`` is bounded by BOTH the core budget and
the measured per-worker memory footprint of the chainsolver pool (ADR-0126
amendment). The measured basis is the run manifest
``docs/runs/chainsolver-worker-private-memory-2026-09-17.yml``: seven
deduplicated 100% ZGB-8 executions of
``braunschweig.synthesis.locations.secondary_chainsolvers`` recorded on the
felix server between 2026-09-05 and 2026-09-09. See
``docs/codebase/notes/resource-budget.md`` for the mechanism this amends.
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig import resources  # noqa: E402

# The historical 125.78 GB server the seven runs below were actually measured
# on, and the current, smaller 94.28 GB server (budget 86.28 GB after the 8 GB
# reserve) this amendment protects against an OOM on.
OLD_SERVER = resources.MachineResources(
    cores=64, memory_gb=125.78, cores_source="sched_getaffinity", memory_source="psutil",
)
NEW_SERVER = resources.MachineResources(
    cores=64, memory_gb=94.28, cores_source="sched_getaffinity", memory_source="psutil",
)

# The seven deduplicated 100% ZGB-8 executions recorded in the run manifest:
# (label, driver RSS GB at the stage's memory minimum, available-memory drop GB
# summed over the 62 live workers). Driver growth during the stage was 0.0 GB
# in every run, so "driver RSS at min" equals the run's baseline driver RSS.
MEASURED_SHAPES = [
    ("b370_arm0_20260905T193026", 19.60, 37.87),
    ("b370_arm0_20260905T224249", 19.62, 36.26),
    ("b370_arm0_20260907T083207", 21.84, 36.19),
    ("b370_arm0_20260908T002422_pb4_arm4", 30.09, 53.44),
    ("b370_arm0_20260909T172119", 21.83, 44.45),
    ("b370_arm1_20260909T155814", 20.85, 30.80),
    ("b370_arm2_20260909T190904", 20.43, 29.48),
]


@pytest.mark.parametrize("label,driver_rss_gb,available_drop_gb", MEASURED_SHAPES)
def test_measured_shapes_keep_the_pool_within_the_memory_budget(
        label, driver_rss_gb, available_drop_gb):
    # Safety invariant of the rule, true by construction of the floor: whatever
    # the ceiling comes out to, driver + workers * worker_memory_gb must never
    # exceed the machine's memory budget. Checked on BOTH machines so a future
    # change to the arithmetic cannot silently break it on either.
    for machine in (OLD_SERVER, NEW_SERVER):
        budget = resources.resolve_budget(machine=machine, env={})
        workers = resources.effective_chainsolver_workers(
            0, worker_memory_gb=resources.DEFAULT_CHAINSOLVER_WORKER_MEMORY_GB,
            machine=machine, env={}, driver_rss_gb=driver_rss_gb,
        )
        assert (driver_rss_gb + workers * resources.DEFAULT_CHAINSOLVER_WORKER_MEMORY_GB
                <= budget.memory_gb + 1e-9)


def test_measured_shapes_reproduce_62_workers_on_the_125_78gb_server_they_ran_on():
    # These are exactly the driver-RSS baselines of the 62-worker production runs
    # that succeeded; replaying them against the machine they were measured on
    # must reproduce the unchanged 62-worker (core-bound) ceiling.
    for _label, driver_rss_gb, _drop in MEASURED_SHAPES:
        workers = resources.effective_chainsolver_workers(
            0, worker_memory_gb=resources.DEFAULT_CHAINSOLVER_WORKER_MEMORY_GB,
            machine=OLD_SERVER, env={}, driver_rss_gb=driver_rss_gb,
        )
        assert workers == 62


def test_measured_shapes_still_yield_62_on_the_new_94_28gb_server():
    # None of the seven historically measured driver-RSS baselines (max 30.09 GB,
    # the pb4_arm4 run) is by itself heavy enough to push the ceiling below the
    # core budget on the smaller 94.28 GB / 86.28 GB-budget server: even the
    # heaviest, (86.28 - 30.09) / 0.86 = 65, still leaves the pool capped at 62 by
    # CORES, not by memory. This is a reassuring finding from the measured data,
    # not a gap in the mechanism -- see test_the_design_rules_own_worked_examples
    # below for a driver footprint large enough to actually bind (the design
    # rule's own "pb4 shape" hypothetical, at the upper end of the quoted 19-36 GB
    # baseline range, heavier than any of the seven rows actually measured here).
    for _label, driver_rss_gb, _drop in MEASURED_SHAPES:
        workers = resources.effective_chainsolver_workers(
            0, worker_memory_gb=resources.DEFAULT_CHAINSOLVER_WORKER_MEMORY_GB,
            machine=NEW_SERVER, env={}, driver_rss_gb=driver_rss_gb,
        )
        assert workers == 62


@pytest.mark.parametrize("driver_rss_gb,expected_workers", [
    # "Typical server runs" worked example: (86.28 - 25) / 0.86 = 71 -> capped at
    # 62 by cores -> unchanged.
    (25.0, 62),
    # The "pb4 shape" worked example: (86.28 - 36) / 0.86 = 58 workers instead of
    # an OOM. 36 GB is the upper end of the quoted 19-36 GB baseline driver-RSS
    # range -- heavier than any of the seven MEASURED_SHAPES rows above (whose
    # max is 30.09 GB), which is exactly why none of those rows alone bind on the
    # new server while this hypothetical does.
    (36.0, 58),
])
def test_the_design_rules_own_worked_examples(driver_rss_gb, expected_workers):
    workers = resources.effective_chainsolver_workers(
        0, worker_memory_gb=resources.DEFAULT_CHAINSOLVER_WORKER_MEMORY_GB,
        machine=NEW_SERVER, env={}, driver_rss_gb=driver_rss_gb,
    )
    assert workers == expected_workers


def test_auto_sentinel_is_derived_from_the_ceiling():
    resolution = resources.resolve_chainsolver_workers(
        0, resources.resolve_budget(machine=NEW_SERVER, env={}),
        worker_memory_gb=0.86, driver_rss_gb=25.0,
    )
    assert resolution.origin == "derived"
    assert resolution.effective == 62


def test_a_fitting_pin_is_used_verbatim():
    resolution = resources.resolve_chainsolver_workers(
        10, resources.resolve_budget(machine=NEW_SERVER, env={}),
        worker_memory_gb=0.86, driver_rss_gb=25.0,
    )
    assert resolution.origin == "pinned"
    assert resolution.effective == 10


def test_a_pin_above_the_ceiling_is_clamped():
    resolution = resources.resolve_chainsolver_workers(
        999, resources.resolve_budget(machine=NEW_SERVER, env={}),
        worker_memory_gb=0.86, driver_rss_gb=25.0,
    )
    assert resolution.origin == "clamped"
    assert resolution.effective == 62


def test_an_unmeasurable_driver_rss_warns_and_falls_back_to_the_core_bound(monkeypatch, caplog):
    # CLAUDE.md: no silent fallbacks. An injected RSS reader that cannot measure
    # anything must be visible in the log, and the run must still proceed
    # (core-bound only) rather than crash.
    monkeypatch.setattr(resources, "current_process_rss_gb", lambda: None)
    with caplog.at_level(logging.WARNING):
        workers = resources.effective_chainsolver_workers(
            0, worker_memory_gb=0.86, machine=NEW_SERVER, env={}, driver_rss_gb=None,
        )
    assert workers == 62   # core-bound only: driver_rss_gb treated as 0.0
    # "could not be measured" is unique to the fallback-detection warning: the
    # routine deviation warning for an origin != "pinned" resolution (fired
    # regardless, since configured=0 is the auto sentinel here) also mentions
    # "RSS" in its note, so a bare "RSS" substring would pass even if the
    # fallback-detection warning itself were removed -- exactly the failure
    # mode the mutation check below is meant to catch.
    assert any("could not be measured" in record.getMessage()
              for record in caplog.records)


def test_non_positive_worker_memory_gb_raises():
    with pytest.raises(ValueError):
        resources.effective_chainsolver_workers(
            0, worker_memory_gb=0.0, machine=NEW_SERVER, env={}, driver_rss_gb=25.0)
    with pytest.raises(ValueError):
        resources.effective_chainsolver_workers(
            0, worker_memory_gb=-1.0, machine=NEW_SERVER, env={}, driver_rss_gb=25.0)


def test_negative_driver_rss_raises():
    with pytest.raises(ValueError):
        resources.effective_chainsolver_workers(
            0, worker_memory_gb=0.86, machine=NEW_SERVER, env={}, driver_rss_gb=-1.0)
