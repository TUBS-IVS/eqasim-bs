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
"""
import importlib.util
import inspect
import os
import textwrap

import pytest

pytest.importorskip("synpp")

_PATH = os.path.join(os.path.dirname(__file__), "..", "scripts", "run_synpp.py")


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
    # main() imports these lazily from braunschweig.*, so patch them at the source.
    monkeypatch.setattr("braunschweig.logging_setup.setup_logging",
                        lambda level="INFO": str(tmp_path / "run.log"))
    monkeypatch.setattr("braunschweig.provenance.log_and_write_run_provenance",
                        lambda config_path: None)

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
