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
    # The canonical config's actual value (configs/base_bs.yml); this is what
    # makes an impossible num_workers/memory combination an ERROR below -- see
    # test_report_does_not_error_when_popsim_is_not_the_selected_method for the
    # gate this exercises.
    "braunschweig.population.method": "popsim_mid",
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


def test_report_does_not_crash_when_matsim_threads_is_the_auto_sentinel():
    # This module's whole purpose w.r.t. matsim_threads/matsim_qsim_threads is
    # to never touch them (issue #410); a non-numeric configured value (e.g.
    # an overlay that reuses the "auto" spelling for these keys) must degrade
    # to a warning, not crash int() and abort the resource gate over a key it
    # deliberately never resolves.
    config = dict(PRODUCTION_CONFIG, matsim_threads="auto")
    report = resources.build_report(config, machine=SERVER, env={})
    warning = next(v for v in report.violations if v.key == "matsim_threads")
    assert warning.severity == "warning"
    assert "auto" in warning.message


def test_report_errors_when_a_pin_cannot_be_clamped_into_the_machine():
    # A machine so small that even one popsim worker does not fit its memory,
    # and a config that actually selects a PopulationSim workflow.
    tiny = resources.MachineResources(
        cores=4, memory_gb=9.0, cores_source="cpu_count", memory_source="psutil",
    )
    report = resources.build_report(PRODUCTION_CONFIG, machine=tiny, env={})
    error = next(v for v in report.violations if v.severity == "error")
    assert "braunschweig.population.popsim.num_workers" in error.key


def test_report_does_not_error_when_popsim_is_not_the_selected_method():
    # IMPORTANT 2 (final review): a run that never selects a PopulationSim
    # workflow (simple_ipf_open, or the key simply absent, as in a MATSim-only
    # overlay) cannot start a PopulationSim worker, so the same impossible
    # num_workers/memory combination must not abort it -- only warn.
    tiny = resources.MachineResources(
        cores=4, memory_gb=9.0, cores_source="cpu_count", memory_source="psutil",
    )
    config = dict(PRODUCTION_CONFIG, **{"braunschweig.population.method": "simple_ipf_open"})
    report = resources.build_report(config, machine=tiny, env={})
    assert not [v for v in report.violations if v.severity == "error"]
    warning = next(v for v in report.violations
                   if v.key == "braunschweig.population.popsim.num_workers")
    assert warning.severity == "warning"

    # The same holds when the key is entirely absent (a MATSim-only overlay
    # such as configs/overlays/test_matsim.yml never sets population.method).
    config_without_method = {k: v for k, v in PRODUCTION_CONFIG.items()
                             if k != "braunschweig.population.method"}
    report = resources.build_report(config_without_method, machine=tiny, env={})
    assert not [v for v in report.violations if v.severity == "error"]


def test_report_on_a_fitting_machine_has_no_violations():
    big = resources.MachineResources(
        cores=64, memory_gb=256.0, cores_source="sched_getaffinity", memory_source="psutil",
    )
    # processes is raised from the real production pin (32) to 60 here only:
    # 32 sits below PROCESSES_UNDERUSE_FRACTION * budget.cores (0.75 * 62 = 46.5)
    # and would otherwise trip the new under-use warning this test must stay free of.
    config = dict(PRODUCTION_CONFIG, processes=60)
    report = resources.build_report(config, machine=big, env={})
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
