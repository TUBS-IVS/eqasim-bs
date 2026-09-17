"""A config-value defect must reach the startup gate, not escape as a traceback.

``build_report`` has two failure channels. A machine-FIT mismatch becomes a
``Violation`` that ``enforce_report`` renders into an actionable message naming
the key. A config-VALUE defect used to raise straight out of the resolver, past
``build_report``, past ``enforce_report`` and out of ``scripts/run_synpp.py`` as
a bare traceback: twelve distinct defects took that second channel and none
reached the gate. These tests pin the single channel, so the gate that exists to
give an actionable startup message actually sees every defect it is meant to
report.

Both environment budgets are the deliberate exception: they are read before any
budget exists, so they cannot become a ``Violation`` and stay raises -- but with
a message that names the variable and the accepted spelling.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig import resources  # noqa: E402

SERVER = resources.MachineResources(
    cores=64, memory_gb=94.28, cores_source="sched_getaffinity", memory_source="psutil",
)

#: A configuration that resolves cleanly, so each case below varies exactly one key.
VALID = {
    "java_memory": "50G",
    "processes": 32,
    "braunschweig.population.popsim.num_workers": 2,
    "braunschweig.population.method": "popsim_mid",
    "sampling_rate": 1.0,
}

#: (label, config overlay, the key the operator must be told to fix). Every one of
#: these escaped ``build_report`` as a raw traceback before the single-channel fix.
VALUE_DEFECTS = [
    ("chainsolver pin negative",
     {"braunschweig.chainsolvers.processes": -4},
     "braunschweig.chainsolvers.processes"),
    ("chainsolver pin non-integral",
     {"braunschweig.chainsolvers.processes": 3.7},
     "braunschweig.chainsolvers.processes"),
    ("chainsolver pin not a number",
     {"braunschweig.chainsolvers.processes": "acht"},
     "braunschweig.chainsolvers.processes"),
    ("popsim workers negative",
     {"braunschweig.population.popsim.num_workers": -1},
     "braunschweig.population.popsim.num_workers"),
    ("popsim workers non-integral",
     {"braunschweig.population.popsim.num_workers": 2.5},
     "braunschweig.population.popsim.num_workers"),
    ("java heap zero", {"java_memory": "0G"}, "java_memory"),
    ("java heap unparseable", {"java_memory": "viel"}, "java_memory"),
    ("processes negative", {"processes": -8}, "processes"),
    ("popsim worker memory not a number",
     {"braunschweig.population.popsim.worker_memory_gb": "x"},
     "braunschweig.population.popsim.worker_memory_gb"),
    ("chainsolver worker memory not a number",
     {"braunschweig.chainsolvers.worker_memory_gb": "x"},
     "braunschweig.chainsolvers.worker_memory_gb"),
]


@pytest.mark.parametrize("overlay,expected_key",
                         [(case[1], case[2]) for case in VALUE_DEFECTS],
                         ids=[case[0] for case in VALUE_DEFECTS])
def test_a_config_value_defect_reaches_the_gate_instead_of_raising(overlay, expected_key):
    # build_report must RETURN a report: the defect belongs in the Violation
    # channel, which enforce_report renders, not in the exception channel, which
    # surfaces as a traceback from the launcher.
    report = resources.build_report(dict(VALID, **overlay), machine=SERVER, env={})
    errors = [v for v in report.violations if v.severity == "error"]
    assert {v.key for v in errors} == {expected_key}
    with pytest.raises(resources.ResourceValidationError) as excinfo:
        resources.enforce_report(report)
    assert expected_key in str(excinfo.value)


def test_every_bad_key_is_reported_at_once_not_one_per_attempt():
    # An operator fixing a config should see every unusable key in one run,
    # rather than rediscovering the next one after each correction.
    config = dict(
        VALID,
        java_memory="viel",
        **{"braunschweig.population.popsim.num_workers": -1,
           "braunschweig.chainsolvers.processes": 3.7},
    )
    report = resources.build_report(config, machine=SERVER, env={})
    errors = {v.key for v in report.violations if v.severity == "error"}
    assert errors == {
        "java_memory",
        "braunschweig.population.popsim.num_workers",
        "braunschweig.chainsolvers.processes",
    }


def test_a_bad_processes_value_names_processes_not_the_chainsolver_key():
    # build_report falls back to `processes` when the chainsolver key is unset.
    # Reporting the chainsolver key there tells the operator to fix a key they
    # never set.
    report = resources.build_report(dict(VALID, processes=-8), machine=SERVER, env={})
    errors = [v for v in report.violations if v.severity == "error"]
    assert {v.key for v in errors} == {"processes"}
    assert all("braunschweig.chainsolvers.processes" not in v.message for v in errors)


def test_an_explicit_chainsolver_pin_still_overrides_processes():
    report = resources.build_report(
        dict(VALID, processes=32, **{"braunschweig.chainsolvers.processes": -4}),
        machine=SERVER, env={})
    errors = [v for v in report.violations if v.severity == "error"]
    assert {v.key for v in errors} == {"braunschweig.chainsolvers.processes"}


@pytest.mark.parametrize("raw", ["auto", "0", "-4", "3.5"])
def test_an_unusable_cpu_budget_override_is_rejected_with_an_actionable_message(raw):
    # These are read before any budget exists, so they cannot become a Violation.
    # They must at least name the variable and the accepted spelling -- a bare
    # int() gave "invalid literal for int() with base 10: 'auto'", and '0' was
    # silently accepted while the log claimed "machine: 0 cores".
    with pytest.raises(ValueError) as excinfo:
        resources.resolve_budget(SERVER, env={resources.ENV_CPU_BUDGET: raw})
    assert resources.ENV_CPU_BUDGET in str(excinfo.value)
    assert repr(raw) in str(excinfo.value)


@pytest.mark.parametrize("raw", ["viel", "0G", "-4G"])
def test_an_unusable_memory_budget_override_is_rejected_with_an_actionable_message(raw):
    with pytest.raises(ValueError) as excinfo:
        resources.resolve_budget(SERVER, env={resources.ENV_MEM_BUDGET: raw})
    assert resources.ENV_MEM_BUDGET in str(excinfo.value)


def test_a_valid_configuration_is_unchanged_by_the_single_channel():
    report = resources.build_report(VALID, machine=SERVER, env={})
    assert [v for v in report.violations if v.severity == "error"] == []
    assert {r.key for r in report.resolutions} == {
        "java_memory",
        "processes",
        "braunschweig.population.popsim.num_workers",
        "braunschweig.chainsolvers.processes",
    }
