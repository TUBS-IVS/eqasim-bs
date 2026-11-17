"""Run-time check of the PopulationSim environment the popsim stage launches (issue #645, G7).

The stage runs PopulationSim as ``uv run --no-sync populationsim`` in the popsimprep
environment. These tests pin: the version comparison, the one subprocess that asks THAT
environment what is installed, the loud failure (with the sync hint), the batch-cache
signature and the provenance record. The meaning of the configured version is stated once,
at ``KEY_POPSIM_VERSION`` in ``braunschweig/popsim/stage/config_keys.py``.
"""
import json
import subprocess
from types import SimpleNamespace

import pandas as pd
import pytest
import yaml

from braunschweig import provenance
from braunschweig.popsim import stage, tool_version


def test_require_equal_passes():
    tool_version.require_populationsim_version(
        "0.10.0+bs.1", "0.10.0+bs.1", project_dir="/opt/popsimprep")


def test_require_mismatch_raises():
    # The upstream release the fork is based on must NOT satisfy the fork's version.
    with pytest.raises(RuntimeError) as excinfo:
        tool_version.require_populationsim_version(
            "0.10.0", "0.10.0+bs.1", project_dir="/opt/popsimprep")
    message = str(excinfo.value)
    assert "0.10.0+bs.1" in message and "0.10.0" in message
    # The hint names the directory that was actually asked, not a fixed one.
    assert "uv sync --frozen" in message and "/opt/popsimprep" in message


_REPORT = {
    "populationsim": "0.10.0+bs.1",
    "commit": "abc123",
    "ortools": "9.9.3963",
    "numpy": "1.26.4",
    "pandas": "2.2.2",
    "python": "3.11.9",
}


def test_report_parses_stdout():
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout=json.dumps(_REPORT) + "\n", stderr="")

    report = tool_version.installed_tool_report(
        "/opt/uv", "/opt/popsimprep", subprocess_run=fake_run)

    assert report == _REPORT
    command, kwargs = calls[0]
    # Same environment as the stage's batch runner: same uv, --no-sync, cwd = popsimprep dir.
    assert command[:5] == ["/opt/uv", "run", "--no-sync", "python", "-c"]
    assert kwargs["cwd"] == "/opt/popsimprep"
    assert kwargs["timeout"] == 120


@pytest.mark.parametrize("outcome", ["exit", "timeout", "garbage"])
def test_report_failure_raises_with_sync_hint(outcome):
    def fake_run(command, **kwargs):
        if outcome == "timeout":
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])
        if outcome == "exit":
            return subprocess.CompletedProcess(
                command, 1, stdout="", stderr="ModuleNotFoundError: populationsim")
        return subprocess.CompletedProcess(command, 0, stdout="not json", stderr="")

    with pytest.raises(RuntimeError) as excinfo:
        tool_version.installed_tool_report("/opt/uv", "/opt/popsimprep", subprocess_run=fake_run)
    assert "uv sync --frozen" in str(excinfo.value)
    assert "/opt/popsimprep" in str(excinfo.value)
    if outcome == "exit":
        assert "ModuleNotFoundError" in str(excinfo.value)


def test_check_refuses_an_environment_without_the_fork():
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(dict(_REPORT, populationsim="0.10.0")), stderr="")

    context = SimpleNamespace(set_info=lambda key, value: None)
    with pytest.raises(RuntimeError, match="0.10.0"):
        tool_version.check_populationsim_environment(
            context, "/opt/uv", "/opt/popsimprep", "0.10.0+bs.1", subprocess_run=fake_run)


def test_execute_checks_the_environment_before_anything_else(monkeypatch):
    # A wrong environment must stop the run before any further config is read or seed built.
    class _Stop(Exception):
        pass

    def refuse(context, uv_path, project_dir, expected):
        assert (uv_path, project_dir, expected) == ("uv", "popsimprep", "0.10.0+bs.1")
        raise _Stop

    def must_not_run(context):
        raise AssertionError("execute() read more config before the environment check")

    monkeypatch.setattr(
        stage, "_read_stage_paths",
        lambda ctx: ("cells", "mid", "controls", "settings", "logging", "popsimprep", "uv"))
    monkeypatch.setattr(stage.tool_version, "check_populationsim_environment", refuse)
    monkeypatch.setattr(stage, "_read_batching_and_scope_config", must_not_run)
    context = SimpleNamespace(config=lambda key: {stage.KEY_POPSIM_VERSION: "0.10.0+bs.1"}[key])
    with pytest.raises(_Stop):
        stage.execute(context)


def _signature(populationsim_version, commit="c0ffee", lock_hash="lockhash"):
    return stage.compute_batch_config_signature(
        controls_df=pd.DataFrame({"target": ["total_households"], "importance": [1000]}),
        settings_text="geographies: [ZENSUS100m]\n",
        max_cells=1500,
        stratify_regiostar=False,
        source_name="mid",
        employment_grid_on=False,
        kreis_controls_map=None,
        seed_day_filter=(1, 2, 3),
        seed_households=pd.DataFrame({"H_ID": [1], "H_GEW": [1.0]}),
        seed_persons=pd.DataFrame({"H_ID": [1], "P_ID": [1]}),
        kreis_table=pd.DataFrame({"ARS_kreis": ["03101"]}),
        populationsim_version=populationsim_version,
        populationsim_commit=commit,
        populationsim_lock_hash=lock_hash,
        settings_overrides={},
        integerizer_seed_base=None,
    )


def test_signature_changes_with_version():
    # A batch finished by another tool version must be purged, never reused.
    assert _signature("0.10.0+bs.1") != _signature("0.10.0+bs.2")
    assert _signature("0.10.0+bs.1") == _signature("0.10.0+bs.1")


def test_provenance_records_version(tmp_path):
    key = stage.KEY_POPSIM_VERSION
    config = tmp_path / "config_test.yml"
    config.write_text(yaml.safe_dump({"config": {key: "0.10.0+bs.1"}}), encoding="utf-8")
    record = provenance.collect_run_provenance(str(config))
    assert record[key] == "0.10.0+bs.1"


# --- expected commit from the tracked lock file (review finding 1) ----------------------

_SHA = "3d70f73c00fb47696b56552540c2d858d93ed356"
_OTHER_SHA = "0123456789abcdef0123456789abcdef01234567"

_LOCK_EXCERPT = f'''\
[[package]]
name = "idna"
version = "3.11"
source = {{ registry = "https://pypi.org/simple" }}

[[package]]
name = "populationsim"
version = "0.10.0+bs.1"
source = {{ git = "https://github.com/TUBS-IVS/populationsim?rev={_SHA}#{_SHA}" }}
dependencies = [
    {{ name = "numba" }},
]

[[package]]
name = "ortools"
version = "9.9.3963"
source = {{ registry = "https://pypi.org/simple" }}

[[package]]
name = "numpy"
version = "1.26.4"
source = {{ registry = "https://pypi.org/simple" }}

[[package]]
name = "pandas"
version = "2.2.2"
source = {{ registry = "https://pypi.org/simple" }}

[[package]]
name = "pytz"
version = "2025.2"
source = {{ registry = "https://pypi.org/simple" }}
'''


def _project(tmp_path, lock_text=_LOCK_EXCERPT):
    if lock_text is not None:
        (tmp_path / "uv.lock").write_text(lock_text, encoding="utf-8")
    return tmp_path


def test_expected_commit_is_parsed_from_the_lock(tmp_path):
    assert tool_version.expected_populationsim_commit(_project(tmp_path)) == _SHA


@pytest.mark.parametrize("lock_text", [
    None,  # no uv.lock at all
    '[[package]]\nname = "idna"\nversion = "3.11"\n',  # no populationsim entry
    '[[package]]\nname = "populationsim"\nversion = "0.10.0"\n'
    'source = { registry = "https://pypi.org/simple" }\n',  # not a git source
], ids=["no-lock", "no-entry", "no-git-source"])
def test_expected_commit_unparsable_lock_raises(tmp_path, lock_text):
    with pytest.raises(RuntimeError, match="uv.lock"):
        tool_version.expected_populationsim_commit(_project(tmp_path, lock_text))


def _run_with_commit(commit):
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(dict(_REPORT, commit=commit)), stderr="")
    return fake_run


def test_check_accepts_the_locked_commit(tmp_path):
    context = SimpleNamespace(info={})
    context.set_info = context.info.__setitem__
    report = tool_version.check_populationsim_environment(
        context, "/opt/uv", str(_project(tmp_path)), "0.10.0+bs.1",
        subprocess_run=_run_with_commit(_SHA))
    assert report["commit"] == _SHA


@pytest.mark.parametrize("installed", [_OTHER_SHA, "unknown"])
def test_check_refuses_a_commit_other_than_the_lock(tmp_path, installed):
    # Same version string, other commit: a moved branch or a local build must not pass.
    context = SimpleNamespace(set_info=lambda key, value: None)
    with pytest.raises(RuntimeError) as excinfo:
        tool_version.check_populationsim_environment(
            context, "/opt/uv", str(_project(tmp_path)), "0.10.0+bs.1",
            subprocess_run=_run_with_commit(installed))
    message = str(excinfo.value)
    assert _SHA in message and installed in message
    assert "uv sync --frozen" in message and repr(str(tmp_path)) in message


def test_check_refuses_a_missing_lock(tmp_path):
    context = SimpleNamespace(set_info=lambda key, value: None)
    with pytest.raises(RuntimeError, match="uv.lock"):
        tool_version.check_populationsim_environment(
            context, "/opt/uv", str(tmp_path), "0.10.0+bs.1",
            subprocess_run=_run_with_commit(_SHA))


def test_signature_changes_with_the_commit():
    # A batch finished by another commit of the same version must be purged, never reused.
    assert _signature("0.10.0+bs.1", commit=_SHA) != _signature("0.10.0+bs.1", commit=_OTHER_SHA)
    assert _signature("0.10.0+bs.1", commit=_SHA) == _signature("0.10.0+bs.1", commit=_SHA)


# --- the rest of the locked solver stack (review round 3, finding 2) ---------------------

def test_check_refuses_an_ortools_version_other_than_the_lock(tmp_path):
    # Same PopulationSim commit, other solver backend: the results are not the locked ones.
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(
            command, 0, stdout=json.dumps(dict(_REPORT, commit=_SHA, ortools="9.10.4067")),
            stderr="")

    context = SimpleNamespace(set_info=lambda key, value: None)
    with pytest.raises(RuntimeError) as excinfo:
        tool_version.check_populationsim_environment(
            context, "/opt/uv", str(_project(tmp_path)), "0.10.0+bs.1", subprocess_run=fake_run)
    message = str(excinfo.value)
    assert "ortools" in message and "9.10.4067" in message and "9.9.3963" in message
    assert "uv sync --frozen" in message


def test_locked_package_versions_reads_the_lock_and_names_a_missing_package(tmp_path):
    project = _project(tmp_path)
    assert tool_version.locked_package_versions(project, ("ortools", "numpy")) == {
        "ortools": "9.9.3963", "numpy": "1.26.4"}
    with pytest.raises(RuntimeError, match="'scipy'"):
        tool_version.locked_package_versions(project, ("scipy",))


def test_signature_changes_with_the_lock_hash():
    assert _signature("0.10.0+bs.1", lock_hash="a") != _signature("0.10.0+bs.1", lock_hash="b")
    assert _signature("0.10.0+bs.1", lock_hash="a") == _signature("0.10.0+bs.1", lock_hash="a")


def test_lock_content_hash_follows_the_text_not_the_line_endings(tmp_path):
    lf, crlf, other = tmp_path / "lf", tmp_path / "crlf", tmp_path / "other"
    for directory in (lf, crlf, other):
        directory.mkdir()
    (lf / "uv.lock").write_bytes(_LOCK_EXCERPT.encode("utf-8"))
    (crlf / "uv.lock").write_bytes(_LOCK_EXCERPT.replace("\n", "\r\n").encode("utf-8"))
    (other / "uv.lock").write_bytes(
        _LOCK_EXCERPT.replace("9.9.3963", "9.10.4067").encode("utf-8"))
    assert tool_version.lock_content_hash(lf) == tool_version.lock_content_hash(crlf)
    assert tool_version.lock_content_hash(lf) != tool_version.lock_content_hash(other)
