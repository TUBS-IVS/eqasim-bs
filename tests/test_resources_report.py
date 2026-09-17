"""Tests for the startup resource report and its validation gate."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig import resources  # noqa: E402

# The run server as the run resource recorder measures it: memory_total_kb reads
# 94.28 GB (``free -g`` truncates the same machine to "94"), so the derived budget
# is 94.28 - 8 = 86.28 GB. Recorded in
# docs/runs/chainsolver-worker-private-memory-2026-09-17.yml.
SERVER = resources.MachineResources(
    cores=64, memory_gb=94.28, cores_source="sched_getaffinity", memory_source="psutil",
)

PRODUCTION_CONFIG = {
    "java_memory": "100G",
    "processes": 32,
    "braunschweig.chainsolvers.processes": 0,
    "braunschweig.population.popsim.num_workers": 3,
    "matsim_threads": 56,
    "matsim_qsim_threads": 16,
    # The canonical config's actual value (configs/base_bs.yml); this is what
    # makes an impossible num_workers/memory combination an ERROR below -- see
    # test_report_does_not_error_when_popsim_is_not_the_selected_method for the
    # method gate and test_report_only_warns_below_the_measured_sampling_rate for
    # the scale gate this exercises.
    "braunschweig.population.method": "popsim_mid",
    # The canonical 100 % production scale (configs/overlays/test_100pct.yml) --
    # the scale DEFAULT_POPSIM_WORKER_MEMORY_GB was measured at, and therefore the
    # only scale at which a mismatch against it may abort a run.
    "sampling_rate": 1.0,
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


# ---------------------------------------------------------------------------
# The SCALE dimension of the PopulationSim memory gate (final review B2).
# DEFAULT_POPSIM_WORKER_MEMORY_GB is a 100%-scale measurement, so it may only
# ABORT a run at the scale it was measured at. Below that scale the same
# mismatch is a warning that names the unmeasured scale.
# ---------------------------------------------------------------------------

DEVELOPER_MACHINE = resources.MachineResources(
    cores=16, memory_gb=32.0, cores_source="cpu_count", memory_source="psutil",
)


def test_report_only_warns_below_the_measured_sampling_rate():
    # A 1 % popsim fixture on a 32 GB machine: 32 - 8 = 24 GB budget < 30 GB, so
    # the mismatch fires -- but the 30 GB figure has never been measured at 1 %,
    # so it must not abort a run that worked before this gate existed.
    config = dict(PRODUCTION_CONFIG, sampling_rate=0.01)
    report = resources.build_report(config, machine=DEVELOPER_MACHINE, env={})
    assert not [v for v in report.violations if v.severity == "error"]
    warning = next(v for v in report.violations
                   if v.key == "braunschweig.population.popsim.num_workers")
    assert warning.severity == "warning"
    # The caveat has to be stated plainly, not merely implied by the severity.
    assert "100%-SCALE measurement" in warning.message
    assert "has NOT been measured" in warning.message


def test_report_errors_at_the_measured_sampling_rate():
    # The same machine and the same mismatch at the scale the figure describes.
    report = resources.build_report(PRODUCTION_CONFIG, machine=DEVELOPER_MACHINE, env={})
    error = next(v for v in report.violations if v.severity == "error")
    assert error.key == "braunschweig.population.popsim.num_workers"


def test_report_treats_a_missing_sampling_rate_as_not_full_scale():
    config = {k: v for k, v in PRODUCTION_CONFIG.items() if k != "sampling_rate"}
    report = resources.build_report(config, machine=DEVELOPER_MACHINE, env={})
    assert not [v for v in report.violations if v.severity == "error"]


def test_report_errors_at_any_scale_once_the_operator_asserts_the_figure():
    # Setting braunschweig.population.popsim.worker_memory_gb explicitly IS the
    # operator asserting that the per-worker figure applies to this run, so the
    # mismatch becomes fatal again regardless of sampling rate.
    config = dict(PRODUCTION_CONFIG, sampling_rate=0.01)
    config[resources.KEY_WORKER_MEMORY_GB] = 30.0
    report = resources.build_report(config, machine=DEVELOPER_MACHINE, env={})
    error = next(v for v in report.violations if v.severity == "error")
    assert error.key == "braunschweig.population.popsim.num_workers"


@pytest.mark.parametrize("fixture_name", [
    "config_popsim_mid_braunschweig.yml",
    "config_smoke_popsim_mid_mini.yml",
])
def test_committed_popsim_fixtures_still_start_on_a_32gb_machine(fixture_name):
    # Regression for the concrete defect: both committed 1 % popsim fixtures
    # (population.method popsim_mid, num_workers 3) aborted before any stage ran
    # on a machine below ~38 GB. Reads the REAL fixture file, so a future edit to
    # it is covered too.
    import yaml
    fixture_path = REPO / "configs" / "fixtures" / fixture_name
    with open(fixture_path, encoding="utf-8") as handle:
        config = (yaml.safe_load(handle) or {}).get("config", {}) or {}
    assert config.get("braunschweig.population.method") == "popsim_mid", (
        f"{fixture_name} no longer selects popsim_mid; this regression test is "
        "pinned to the popsim path and must be updated deliberately")
    report = resources.build_report(config, machine=DEVELOPER_MACHINE, env={})
    resources.enforce_report(report)   # must NOT raise
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


def test_report_includes_a_reporting_only_chainsolver_processes_entry():
    # ADR-0126 amendment: the chainsolver pool IS memory-bounded now, but the
    # bound needs the live driver RSS, only known once the stage starts, so the
    # startup report can only preview what the CORE budget alone allows --
    # origin must be "reported", never "pinned"/"clamped"/"derived", so a reader
    # never mistakes this preview for the value the stage actually used.
    report = resources.build_report(PRODUCTION_CONFIG, machine=SERVER, env={})
    chainsolver = next(
        r for r in report.resolutions if r.key == "braunschweig.chainsolvers.processes")
    assert chainsolver.origin == "reported"
    # The canonical config leaves this key at the auto sentinel 0, so the preview
    # is the whole core budget.
    assert chainsolver.effective == SERVER.cores - 2


def test_report_previews_a_positive_chainsolver_pin_and_not_the_core_budget():
    # Final review B6: the preview hardcoded the core budget and ignored the pin,
    # so `braunschweig.chainsolvers.processes: 8` was logged and written into
    # run_provenance_<stamp>.json as 62. That destroys the evidence of the server
    # A/B this mechanism owes (shards: 62, processes: 8 vs 62 -- both arms would
    # have recorded effective 62).
    config = dict(PRODUCTION_CONFIG, **{"braunschweig.chainsolvers.processes": 8})
    report = resources.build_report(config, machine=SERVER, env={})
    chainsolver = next(
        r for r in report.resolutions if r.key == "braunschweig.chainsolvers.processes")
    assert chainsolver.origin == "reported"
    assert chainsolver.configured == 8
    assert chainsolver.effective == 8
    # The note must say the startup preview is a ceiling, not the final count.
    assert "LIVE" in chainsolver.note and "stage start" in chainsolver.note


def test_report_caps_a_chainsolver_pin_above_the_core_budget():
    config = dict(PRODUCTION_CONFIG, **{"braunschweig.chainsolvers.processes": 999})
    report = resources.build_report(config, machine=SERVER, env={})
    chainsolver = next(
        r for r in report.resolutions if r.key == "braunschweig.chainsolvers.processes")
    assert chainsolver.effective == SERVER.cores - 2


def test_report_falls_back_to_the_synpp_processes_key_for_the_chainsolver_preview():
    # The stage does the same: braunschweig.chainsolvers.processes unset (or null)
    # defers to the synpp-level `processes` key.
    config = {k: v for k, v in PRODUCTION_CONFIG.items()
              if k != "braunschweig.chainsolvers.processes"}
    report = resources.build_report(config, machine=SERVER, env={})
    chainsolver = next(
        r for r in report.resolutions if r.key == "braunschweig.chainsolvers.processes")
    assert chainsolver.effective == 32   # the config's `processes` pin, not 62


def test_report_rejects_an_unusable_chainsolver_pin_at_the_run_start():
    config = dict(PRODUCTION_CONFIG, **{"braunschweig.chainsolvers.processes": -4})
    with pytest.raises(ValueError, match="braunschweig.chainsolvers.processes"):
        resources.build_report(config, machine=SERVER, env={})


def test_as_dict_is_json_serialisable_for_the_run_provenance():
    import json
    report = resources.build_report(PRODUCTION_CONFIG, machine=SERVER, env={})
    payload = report.as_dict()
    assert payload["machine"]["cores"] == 64
    assert payload["machine"]["memory_source"] == "psutil"
    assert payload["resolutions"]["java_memory"]["effective"] == "86G"
    json.dumps(payload)   # must not raise
