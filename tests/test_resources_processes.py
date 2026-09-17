"""``processes`` is result-affecting at its two consumers (it decides both the
person-chunk partition and the seed count) and must therefore never be silently
clamped -- see the CRITICAL fix in ADR-0126, Decision 3, and the corrected
worked example in ``docs/codebase/notes/resource-budget.md``. There is no
``effective_processes`` wrapper any more: ``synthesis/population/matched.py``
and ``synthesis/population/spatial/secondary/locations.py`` read
``context.config("processes")`` verbatim, exactly as before this branch. The
only thing this module still does with ``processes`` is WARN in the startup
report when a pin under-uses the machine, which is purely informational.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig import resources  # noqa: E402

SERVER = resources.MachineResources(
    cores=64, memory_gb=94.28, cores_source="sched_getaffinity", memory_source="psutil",
)


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


def test_report_reports_but_never_clamps_a_processes_pin_above_the_budget():
    # BLOCKER 2 (final review, reproduced on a 16-core box): the report said
    # "processes = 14 [clamped]" while the run actually used the pinned 32 --
    # resolve_processes is reporting-only, so the report must never claim a
    # clamp that did not happen. effective must equal the configured pin
    # verbatim; the note states what the budget would have allowed instead.
    small = resources.MachineResources(
        cores=16, memory_gb=94.0, cores_source="cpu_count", memory_source="psutil",
    )
    report = resources.build_report(
        {"java_memory": "50G", "processes": 32,
         "braunschweig.population.popsim.num_workers": 2},
        machine=small, env={},
    )
    processes = next(r for r in report.resolutions if r.key == "processes")
    assert processes.effective == 32      # never reduced to the 14-core budget
    assert processes.origin == "reported"
    assert processes.origin != "clamped"
    assert "14" in processes.note         # what the budget would have allowed

    payload = report.as_dict()
    assert payload["resolutions"]["processes"]["effective"] == 32
    assert payload["resolutions"]["processes"]["origin"] != "clamped"


def test_report_still_warns_on_a_low_processes_pin_on_a_big_machine():
    # The under-use warning (a DIFFERENT signal from the clamp this blocker
    # removes) must keep firing: a pin far below the budget still wastes the
    # machine and that must stay visible.
    big = resources.MachineResources(
        cores=64, memory_gb=256.0, cores_source="sched_getaffinity", memory_source="psutil",
    )
    report = resources.build_report(
        {"java_memory": "50G", "processes": 4,
         "braunschweig.population.popsim.num_workers": 2},
        machine=big, env={},
    )
    warning = next(v for v in report.violations if v.key == "processes")
    assert warning.severity == "warning"
    assert "4" in warning.message and "62" in warning.message


def test_effective_processes_no_longer_exists():
    # The revert (ADR-0126, Decision 3): processes is result-affecting at its
    # two pure read sites, so no effective_* wrapper may exist to clamp it.
    assert not hasattr(resources, "effective_processes")
