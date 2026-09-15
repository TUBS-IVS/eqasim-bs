"""Exercise the public test command in a fresh process and unrelated directory."""
import os
from pathlib import Path
import subprocess
import sys


RUNNER = Path(__file__).resolve().parents[1] / "scripts" / "run_tests.py"


def _run(tmp_path, source, *options):
    test_file = tmp_path / "test_probe.py"
    test_file.write_text(source, encoding="utf-8")
    env = dict(os.environ, PYTHONUTF8="1", EQASIM_BS_RUN_PIPELINE="1")
    env.pop("PYTHONPATH", None)
    return subprocess.run(
        [sys.executable, str(RUNNER), *options, str(test_file), "-q"],
        cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8",
        timeout=60,
    )


def test_runner_uses_repository_imports_and_excludes_pipeline_by_default(tmp_path):
    result = _run(tmp_path, '''
import pathlib
import braunschweig
import pytest

def test_import_and_utf8():
    import sys
    import os
    assert sys.flags.utf8_mode == 1
    assert "EQASIM_BS_RUN_PIPELINE" not in os.environ
    assert pathlib.Path("configs/base_bs.yml").is_file()

@pytest.mark.pipeline
def test_real_pipeline_is_not_started():
    raise AssertionError("Pipeline must be selected explicitly")
''')
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed, 1 deselected" in result.stdout


def test_runner_propagates_pytest_failure(tmp_path):
    result = _run(tmp_path, "def test_failure():\n    assert False\n")
    assert result.returncode == 1, result.stdout + result.stderr
    assert "1 failed" in result.stdout


def test_pipeline_collection_selects_only_pipeline_without_requiring_data(tmp_path):
    result = _run(tmp_path, '''
import os
import pytest

@pytest.mark.pipeline
def test_pipeline_opt_in():
    assert os.environ["EQASIM_BS_RUN_PIPELINE"] == "1"

def test_unit_is_not_selected():
    raise AssertionError("Only pipeline tests should be selected")
''', "--pipeline", "--collect-only")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1/2 tests collected (1 deselected)" in result.stdout


def test_pipeline_input_preflight_failure_stops_before_pytest(tmp_path, monkeypatch, capfd):
    from scripts import run_tests

    monkeypatch.setattr(run_tests, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(run_tests, "check_environment", lambda: {
        "python": sys.executable, "python_version": "3.10", "errors": [],
    })
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "verify_braunschweig_inputs.py").write_text(
        'print("Missing required inputs")\nraise SystemExit(1)\n', encoding="utf-8",
    )
    assert run_tests.main(["--pipeline"]) == 1
    assert "Missing required inputs" in capfd.readouterr().out
