"""Tests for the startup resource report and its validation gate."""
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

PRODUCTION_CONFIG = {
    "java_memory": "100G",
    "processes": 32,
    "braunschweig.population.popsim.num_workers": 3,
    "matsim_threads": 56,
    "matsim_qsim_threads": 16,
}


def test_report_resolves_every_known_resource_key():
    report = resources.build_report(PRODUCTION_CONFIG, machine=SERVER, env={})
    keys = {r.key for r in report.resolutions}
    assert "java_memory" in keys
    assert "processes" in keys
    assert "braunschweig.population.popsim.num_workers" in keys


def test_report_clamps_the_live_java_memory_defect():
    report = resources.build_report(PRODUCTION_CONFIG, machine=SERVER, env={})
    java = next(r for r in report.resolutions if r.key == "java_memory")
    assert java.effective == "86G"
    assert java.origin == "clamped"


def test_report_warns_but_never_clamps_matsim_threads():
    # matsim_threads / matsim_qsim_threads are unverified for result effects
    # (issue #410), so they must be reported, never adjusted.
    small = resources.MachineResources(
        cores=8, memory_gb=94.0, cores_source="cpu_count", memory_source="psutil",
    )
    report = resources.build_report(PRODUCTION_CONFIG, machine=small, env={})
    assert not any(r.key == "matsim_threads" for r in report.resolutions)
    warning = next(v for v in report.violations if v.key == "matsim_threads")
    assert warning.severity == "warning"
    assert "56" in warning.message


def test_report_errors_when_a_pin_cannot_be_clamped_into_the_machine():
    # A machine so small that even one popsim worker does not fit its memory.
    tiny = resources.MachineResources(
        cores=4, memory_gb=9.0, cores_source="cpu_count", memory_source="psutil",
    )
    report = resources.build_report(PRODUCTION_CONFIG, machine=tiny, env={})
    error = next(v for v in report.violations if v.severity == "error")
    assert "braunschweig.population.popsim.num_workers" in error.key


def test_report_on_a_fitting_machine_has_no_violations():
    big = resources.MachineResources(
        cores=64, memory_gb=256.0, cores_source="sched_getaffinity", memory_source="psutil",
    )
    report = resources.build_report(PRODUCTION_CONFIG, machine=big, env={})
    assert report.violations == ()


def test_format_log_names_machine_sources_and_every_origin():
    report = resources.build_report(PRODUCTION_CONFIG, machine=SERVER, env={})
    text = report.format_log()
    assert "64 cores" in text
    assert "sched_getaffinity" in text
    assert "psutil" in text
    assert "clamped" in text


def test_as_dict_is_json_serialisable_for_the_run_provenance():
    import json
    report = resources.build_report(PRODUCTION_CONFIG, machine=SERVER, env={})
    payload = report.as_dict()
    assert payload["machine"]["cores"] == 64
    assert payload["machine"]["memory_source"] == "psutil"
    assert payload["resolutions"]["java_memory"]["effective"] == "86G"
    json.dumps(payload)   # must not raise
