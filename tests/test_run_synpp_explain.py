import importlib
import importlib.util
import os
import pathlib
import sys
import textwrap

import pytest
import synpp
import yaml

_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "run_synpp.py"


def _load():
    spec = importlib.util.spec_from_file_location("run_synpp_mod", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "ls_stage.py").write_text(textwrap.dedent("""
        def configure(context):
            context.config("value")
        def execute(context):
            return context.config("value")
    """), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "ls_stage", raising=False)
    importlib.invalidate_caches()
    working = tmp_path / "cache"
    working.mkdir()
    synpp.run([{"descriptor": "ls_stage"}], {"value": 1}, working_directory=str(working), rerun_required=False)

    def write(expected):
        config_path = tmp_path / "config.yml"
        config_path.write_text(yaml.safe_dump({
            "working_directory": str(working), "run": ["ls_stage"],
            "config": {"value": 1, "synpp_explain_before_run": True,
                       "synpp_expected_stale_stages": expected}}), encoding="utf-8")
        return config_path

    return write, working


def test_explain_before_run_passes_when_only_targets_are_stale(project):
    write, _ = project
    assert _load().explain_before_run(str(write([]))) == 0


def test_explain_before_run_aborts_on_an_unexpected_stale_stage(project, tmp_path):
    write, working = project
    config_path = write(["something_else"])
    entry = next(n for n in os.listdir(working) if n.endswith(".p"))
    os.remove(working / entry)
    assert _load().explain_before_run(str(config_path)) == 2


def test_disabled_switch_is_a_logged_noop(project, caplog):
    write, _ = project
    config_path = write([])
    doc = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    doc["config"]["synpp_explain_before_run"] = False
    config_path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with caplog.at_level("INFO"):
        assert _load().explain_before_run(str(config_path)) == 0
    assert "pre-run assessment disabled" in caplog.text


def test_expected_list_containing_the_stale_stage_lets_the_launch_continue(project):
    write, working = project
    config_path = write(["ls_stage"])
    entry = next(n for n in os.listdir(working) if n.endswith(".p"))
    os.remove(working / entry)
    assert _load().explain_before_run(str(config_path)) == 0


def test_missing_working_directory_is_a_clear_error(project):
    write, _ = project
    config_path = write([])
    doc = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    del doc["working_directory"]
    config_path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with pytest.raises(RuntimeError, match="working_directory"):
        _load().explain_before_run(str(config_path))
