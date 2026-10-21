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


def test_dryrun_config_skips_the_assessment_even_when_it_would_abort(project, caplog):
    write, working = project
    config_path = write(["something_else"])
    os.remove(working / next(n for n in os.listdir(working) if n.endswith(".p")))
    doc = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    doc["dryrun"] = True
    config_path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    with caplog.at_level("INFO"):
        assert _load().explain_before_run(str(config_path)) == 0
    assert "dryrun: pre-run assessment skipped" in caplog.text


def test_gate_notes_how_many_further_stages_recompute_without_aborting(tmp_path, monkeypatch, caplog):
    """a (stale, expected) -> b -> c (run target): b recomputes for certain and is only noted."""
    for name, parent in (("gt_a", None), ("gt_b", "gt_a"), ("gt_c", "gt_b")):
        body = f"    context.stage('{parent}')\n" if parent else "    pass\n"
        value = f"context.stage('{parent}') + 1" if parent else "1"
        (tmp_path / (name + ".py")).write_text(
            f"def configure(context):\n{body}def execute(context):\n    return {value}\n", encoding="utf-8")
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    working = tmp_path / "cache"
    working.mkdir()
    synpp.run([{"descriptor": "gt_c"}], {}, working_directory=str(working), rerun_required=False)
    os.remove(working / next(n for n in os.listdir(working) if n.startswith("gt_a") and n.endswith(".p")))
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump({
        "working_directory": str(working), "run": ["gt_c"],
        "config": {"synpp_explain_before_run": True, "synpp_expected_stale_stages": ["gt_a"]}}),
        encoding="utf-8")
    with caplog.at_level("INFO"):
        assert _load().explain_before_run(str(config_path)) == 0
    assert "note: 1 further stage(s) will recompute because an ancestor is stale" in caplog.text
