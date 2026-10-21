import importlib.util
import json
import os
import pathlib
import sys
import textwrap

import pytest
import synpp
import yaml

_PATH = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "explain_stale_stages.py"


def _load():
    spec = importlib.util.spec_from_file_location("explain_stale_stages_mod", _PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "ex_parent.py").write_text(textwrap.dedent("""
        def configure(context):
            context.config("value")
        def execute(context):
            return context.config("value")
    """), encoding="utf-8")
    (tmp_path / "ex_child.py").write_text(textwrap.dedent("""
        def configure(context):
            context.stage("ex_parent")
        def execute(context):
            return context.stage("ex_parent") + 1
    """), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in ("ex_parent", "ex_child"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    importlib.invalidate_caches()
    working = tmp_path / "cache"
    working.mkdir()
    config = {"value": 1}
    synpp.run([{"descriptor": "ex_child"}], config, working_directory=str(working), rerun_required=False)
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump({"working_directory": str(working), "run": ["ex_child"],
                                           "config": config}), encoding="utf-8")
    return config_path, working


def test_all_hits_exit_zero_and_json(project, tmp_path, capsys):
    config_path, _ = project
    out = tmp_path / "report.json"
    assert _load().main([str(config_path), "--json", str(out)]) == 0
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["ex_parent"]["reasons"] == []
    assert report["ex_child"]["reasons"] == ["required"]
    assert "HIT      ex_parent" in capsys.readouterr().out


def test_unexpected_stale_stage_exits_two(project):
    config_path, working = project
    parent = next(n for n in os.listdir(working) if n.startswith("ex_parent__") and n.endswith(".p"))
    os.remove(working / parent)
    module = _load()
    assert module.main([str(config_path), "--expect", "ex_child"]) == 2
    assert module.main([str(config_path), "--expect", "ex_parent,ex_child"]) == 0


def test_expect_file_syntax(project, tmp_path):
    config_path, working = project
    parent = next(n for n in os.listdir(working) if n.startswith("ex_parent__") and n.endswith(".p"))
    os.remove(working / parent)
    expect = tmp_path / "expect.txt"
    expect.write_text("ex_parent\n", encoding="utf-8")
    assert _load().main([str(config_path), "--expect", "@" + str(expect)]) == 0


def test_empty_expect_means_nothing_may_be_stale_while_no_expect_is_no_gate(project):
    config_path, working = project
    parent = next(n for n in os.listdir(working) if n.startswith("ex_parent__") and n.endswith(".p"))
    os.remove(working / parent)
    module = _load()
    assert module.main([str(config_path)]) == 0
    assert module.main([str(config_path), "--expect", ""]) == 2


def test_json_report_carries_the_recompute_fields(project, tmp_path):
    config_path, working = project
    parent = next(n for n in os.listdir(working) if n.startswith("ex_parent__") and n.endswith(".p"))
    os.remove(working / parent)
    out = tmp_path / "report.json"
    _load().main([str(config_path), "--json", str(out)])
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report["ex_parent"]["certain_recompute"] is False
    assert report["ex_parent"]["unhashed_ancestors"] == [] and "certain_recompute" in report["ex_child"]
