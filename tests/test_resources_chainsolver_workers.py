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
# summed over the 62 live workers).
#
# SAMPLING-POINT CAVEAT (recorded in the run manifest, and the reason none of
# these rows verifies the bound against history): the code reads the driver's
# RSS at the FORK POINT -- effective_chainsolver_workers is called inside
# _solve_problem_set after plans_for_cs is built and before the pool is
# created. The recorder never sampled that point. It captured a per-run
# "driver RSS at the stage's memory minimum" (19.60-30.09 GB, the column
# below) and a before-stage baseline that ranged 19-36 GB. Neither is the
# fork-point quantity, so replaying these rows exercises the ARITHMETIC on
# realistic magnitudes; it does not establish what the bound would have done
# to the seven historical runs.
MEASURED_SHAPES = [
    ("b370_arm0_20260905T193026", 19.60, 37.87),
    ("b370_arm0_20260905T224249", 19.62, 36.26),
    ("b370_arm0_20260907T083207", 21.84, 36.19),
    ("b370_arm0_20260908T002422_pb4_arm4", 30.09, 53.44),
    ("b370_arm0_20260909T172119", 21.83, 44.45),
    ("b370_arm1_20260909T155814", 20.85, 30.80),
    ("b370_arm2_20260909T190904", 20.43, 29.48),
]


# NOTE: a former test here asserted only that
# driver + workers * worker_memory_gb <= budget for each measured shape. That
# holds by construction of the floor for every non-degenerate input, so it could
# not fail; the two tests below assert the concrete expected worker counts on
# both machines instead, and test_the_design_rules_own_worked_examples pins the
# case where the memory bound actually binds.


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
    # Replayed at the DURING-STAGE driver RSS the recorder captured (max 30.09 GB,
    # the pb4_arm4 run), none of the seven rows pushes the ceiling below the core
    # budget on the smaller 94.28 GB / 86.28 GB-budget server: even the heaviest,
    # (86.28 - 30.09) / 0.86 = 65, still leaves the pool capped at 62 by CORES.
    #
    # This does NOT establish that the bound would have left the seven historical
    # runs alone: the quantity the code reads is the driver's RSS at the FORK
    # POINT, which the recorder never sampled (see the caveat on MEASURED_SHAPES
    # and the run manifest's sampling-point row). On this machine the bound drops
    # below 62 from a driver RSS of about 32.96 GB upwards (86.28 - 62 * 0.86),
    # which is INSIDE the 19-36 GB before-stage baseline range the recorder did
    # capture. Whether any historical run would have been throttled is therefore
    # UNVERIFIED; the outstanding evidence is the server smoke, which logs the
    # fork-point value directly.
    for _label, driver_rss_gb, _drop in MEASURED_SHAPES:
        workers = resources.effective_chainsolver_workers(
            0, worker_memory_gb=resources.DEFAULT_CHAINSOLVER_WORKER_MEMORY_GB,
            machine=NEW_SERVER, env={}, driver_rss_gb=driver_rss_gb,
        )
        assert workers == 62


@pytest.mark.parametrize("driver_rss_gb,expected_workers", [
    # "Typical server runs": (86.28 - 25) / 0.86 = 71 -> capped at 62 by cores.
    (25.0, 62),
    # Just below where the memory bound starts to bind: 86.28 - 62 * 0.86 = 32.96
    # GB, so a 32.9 GB driver still leaves room for the full core-bound pool.
    (32.9, 62),
    # Just above it: the bound now binds and the pool drops below the core budget.
    (33.0, 61),
    # At the TOP of the 19-36 GB before-stage baseline range the recorder
    # captured: (86.28 - 36) / 0.86 = 58 workers. This is inside the measured
    # baseline range, not beyond it -- so a run whose fork-point RSS sits here
    # WOULD be throttled. Since the recorder never sampled the fork point, this
    # is what the bound does at that footprint, not a claim about any of the
    # seven recorded runs.
    (36.0, 58),
])
def test_the_bound_binds_from_about_33gb_of_driver_rss(driver_rss_gb, expected_workers):
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
