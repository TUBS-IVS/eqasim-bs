"""Regression test for the arity contract between scripts/run_synpp.py and the
installed synpp ``run_from_yaml`` API (issue #220).

synpp 1.6.2 (pinned in environment.yml) changed ``run_from_yaml`` from a single
``(path)`` argument to four required positional arguments
``(path, working_directory, run, overrides)``. The launcher must call it with a
signature-compatible argument list, otherwise every pipeline run aborts before it
starts with ``TypeError: run_from_yaml() missing 3 required positional arguments``.

Rather than pinning the exact argument count (which would silently rot on the next
synpp API change), this test binds the arguments ``main()`` actually passes against
the *installed* ``synpp.run_from_yaml`` signature. It therefore also guards against
any future signature drift, on whichever synpp version the test environment has.

Requires synpp to be importable; skipped otherwise (the binding cannot be exercised
without the real installed signature).

This file also pins the OTHER contract ``main()`` owes its callees: the resource
gate (ADR-0126). ``main()`` must build a resource report, enforce it, and hand
THAT object to ``log_and_write_run_provenance`` -- deleting
``resource_report=resource_report`` at the call site used to leave the whole
suite green, because the stub here swallowed every keyword argument without
asserting anything about it.
"""
import importlib.util
import inspect
import os
import sys
import textwrap

import pytest

pytest.importorskip("synpp")

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "run_synpp.py")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from braunschweig import resources  # noqa: E402

#: Fixed machine for the resource gate, so these tests never read the machine they
#: run on. ``main()`` reaches the REAL detection through
#: ``build_report -> resolve_budget -> detect_*``; pinning it here is what
#: ``tests/test_monitoring_pipeline_integration.py::_pin_resource_gate`` already
#: does for the same launcher.
_FIXED_MACHINE = resources.MachineResources(
    cores=64, memory_gb=94.28, cores_source="sched_getaffinity", memory_source="psutil",
)


def _pin_resource_gate(monkeypatch):
    """Resolve the startup gate against ``_FIXED_MACHINE`` and an empty environment.

    Patches ``resources.build_report`` -- the same module object
    ``scripts/run_synpp.py`` imported as ``from braunschweig import resources`` --
    so the REAL resolution logic runs and only the machine detection is stubbed.
    Returns a dict that receives the report under ``"report"``, so a test can
    assert that the object handed on to provenance is exactly this one.
    """
    built = {}
    real_build_report = resources.build_report

    def _fixed(config, machine=None, env=None):
        report = real_build_report(config, machine=_FIXED_MACHINE, env={})
        built["report"] = report
        return report

    monkeypatch.setattr(resources, "build_report", _fixed)
    return built


def _load():
    spec = importlib.util.spec_from_file_location("run_synpp_mod", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_main_calls_run_from_yaml_with_installed_signature(tmp_path, monkeypatch):
    import synpp

    mod = _load()

    # Capture the signature of the REAL installed run_from_yaml before we replace it
    # with a recorder. The arguments main() passes must bind against this signature.
    installed_signature = inspect.signature(synpp.run_from_yaml)

    captured = {}

    def _recorder(*args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs

    # Replace the heavy side effects so main() exercises only the call contract:
    # the real pipeline never runs, provenance/logging/cache-prime are neutralised.
    monkeypatch.setattr(mod.synpp, "run_from_yaml", _recorder)
    monkeypatch.setattr(mod, "prime_from_config", lambda config_path: None)
    monkeypatch.setattr(mod, "export_to_store_from_config", lambda config_path: None)
    _pin_resource_gate(monkeypatch)
    # main() imports these lazily from braunschweig.*, so patch them at the source.
    # The provenance stub CAPTURES its keyword arguments instead of swallowing
    # them: see test_main_hands_the_resource_report_to_the_provenance_record.
    provenance_calls = []
    monkeypatch.setattr("braunschweig.logging_setup.setup_logging",
                        lambda level="INFO": str(tmp_path / "run.log"))
    monkeypatch.setattr("braunschweig.provenance.log_and_write_run_provenance",
                        lambda config_path, **kwargs: provenance_calls.append(kwargs))

    config_path = tmp_path / "config.yml"
    config_path.write_text(textwrap.dedent("""
        working_directory: {wd}
        run: []
        config: {{}}
    """).format(wd=tmp_path / "wd").strip(), encoding="utf-8")

    return_code = mod.main([str(config_path)])
    assert return_code == 0
    assert "args" in captured, "main() never called synpp.run_from_yaml"

    # The crux: the arguments main() passed must satisfy the installed signature.
    # On the buggy single-argument call against synpp 1.6.2 this raises TypeError.
    try:
        installed_signature.bind(*captured["args"], **captured["kwargs"])
    except TypeError as error:
        pytest.fail(
            "run_synpp.main() called synpp.run_from_yaml with arguments that do not "
            "match the installed signature %s: %s" % (installed_signature, error))


def test_main_hands_the_resource_report_to_the_provenance_record(tmp_path, monkeypatch):
    """ADR-0126: main() must build a resource report, enforce it, and pass THAT
    object to log_and_write_run_provenance under ``resource_report``.

    Without this test, deleting ``resource_report=resource_report`` from
    scripts/run_synpp.py left all tests green while every run silently stopped
    recording the machine it ran on.
    """
    mod = _load()

    built = _pin_resource_gate(monkeypatch)
    enforced = []
    real_enforce = resources.enforce_report
    monkeypatch.setattr(resources, "enforce_report",
                        lambda report: (enforced.append(report), real_enforce(report))[1])

    provenance_calls = []
    monkeypatch.setattr(mod.synpp, "run_from_yaml", lambda *a, **k: None)
    monkeypatch.setattr(mod, "prime_from_config", lambda config_path: None)
    monkeypatch.setattr(mod, "export_to_store_from_config", lambda config_path: None)
    monkeypatch.setattr("braunschweig.logging_setup.setup_logging",
                        lambda level="INFO": str(tmp_path / "run.log"))
    monkeypatch.setattr(
        "braunschweig.provenance.log_and_write_run_provenance",
        lambda config_path, **kwargs: provenance_calls.append((config_path, kwargs)))

    config_path = tmp_path / "config.yml"
    config_path.write_text(textwrap.dedent("""
        working_directory: {wd}
        run: []
        config:
          java_memory: 100G
          processes: 32
          braunschweig.population.method: popsim_mid
          sampling_rate: 1.0
    """).format(wd=tmp_path / "wd").strip(), encoding="utf-8")

    assert mod.main([str(config_path)]) == 0

    # 1. The gate ran at all: build_report was called, and its report enforced.
    assert "report" in built, "main() never called resources.build_report"
    assert enforced and enforced[0] is built["report"]

    # 2. The report reached the run provenance -- as a keyword argument named
    #    resource_report, carrying the very object build_report returned.
    assert len(provenance_calls) == 1
    _path, kwargs = provenance_calls[0]
    assert "resource_report" in kwargs, (
        "main() called log_and_write_run_provenance without resource_report=; the "
        "run provenance would then carry no record of the machine this run used")
    assert kwargs["resource_report"] is built["report"]

    # 3. And that object really is a report of the pinned machine, not a stand-in.
    assert kwargs["resource_report"].machine.cores == _FIXED_MACHINE.cores
    assert kwargs["resource_report"].as_dict()["resolutions"]["java_memory"][
        "effective"] == "86G"


def test_main_two_args_composes_and_runs_merged_config(tmp_path, monkeypatch):
    """Two args = base + overlay: main() must merge them, write
    <working_directory>/.merged_config.yml, and hand THAT path to provenance,
    prime, run_from_yaml, and export (all four see the identical resolved doc)."""
    from pathlib import Path

    import yaml

    mod = _load()

    workdir = tmp_path / "cache"
    base = tmp_path / "base.yml"
    base.write_text(textwrap.dedent("""
        config:
          sampling_rate: 1.0
          fixed_flag: true
    """), encoding="utf-8")
    overlay = tmp_path / "overlay.yml"
    overlay.write_text(textwrap.dedent(f"""
        working_directory: {workdir.as_posix()}
        run:
          - some.stage
        config:
          sampling_rate: 0.01
    """), encoding="utf-8")

    seen = {}
    _pin_resource_gate(monkeypatch)
    monkeypatch.setattr(mod, "prime_from_config", lambda p: seen.setdefault("prime", p))
    monkeypatch.setattr(mod, "export_to_store_from_config", lambda p: seen.setdefault("export", p))
    # main() imports these lazily from braunschweig.*, so patch them at the source
    # (mirrors test_main_calls_run_from_yaml_with_installed_signature above).
    monkeypatch.setattr("braunschweig.logging_setup.setup_logging",
                        lambda level="INFO": str(tmp_path / "run.log"))
    monkeypatch.setattr("braunschweig.provenance.log_and_write_run_provenance",
                        lambda p, **kwargs: seen.setdefault("provenance", p))
    monkeypatch.setattr(mod.synpp, "run_from_yaml",
                        lambda p, wd, run, ov: seen.setdefault("run", (p, wd, run, ov)))

    assert mod.main([str(base), str(overlay)]) == 0

    merged_path = workdir / ".merged_config.yml"
    assert merged_path.is_file()
    with open(merged_path, encoding="utf-8") as f:
        doc = yaml.safe_load(f)
    assert doc["config"]["sampling_rate"] == 0.01     # overlay wins
    assert doc["config"]["fixed_flag"] is True        # base kept
    assert doc["run"] == ["some.stage"]
    # All four consumers received the merged path, not base or overlay. Compared as
    # Path objects (not raw strings): write_merged() joins os.path.join() onto the
    # working_directory string taken verbatim from the overlay YAML, which on Windows
    # can retain the overlay's forward-slash form and legitimately differ from
    # WindowsPath.__str__()'s backslash form for the identical file location.
    assert Path(seen["run"][0]) == merged_path
    assert Path(seen["prime"]) == merged_path
    assert Path(seen["export"]) == merged_path
    assert Path(seen["provenance"]) == merged_path
