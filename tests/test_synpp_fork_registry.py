"""The fork's registry builder composes a config and builds the stage registry read-only."""
import importlib
import sys
import textwrap

import pytest
import yaml


@pytest.fixture
def mini_project(tmp_path, monkeypatch):
    (tmp_path / "mini_parent.py").write_text(textwrap.dedent("""
        def configure(context):
            context.config("value")
        def execute(context):
            return context.config("value")
    """), encoding="utf-8")
    (tmp_path / "mini_child.py").write_text(textwrap.dedent("""
        def configure(context):
            context.stage("mini_parent")
        def execute(context):
            return context.stage("mini_parent") * 2
    """), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    for name in ("mini_parent", "mini_child"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    importlib.invalidate_caches()
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump({
        "working_directory": str(tmp_path / "cache"),
        "run": ["mini_child"],
        "config": {"value": 21},
    }), encoding="utf-8")
    return config_path


def test_load_settings_without_overlay_reads_the_yaml(mini_project):
    from braunschweig.synpp_fork import registry
    settings = registry.load_settings(str(mini_project), None)
    assert settings["run"] == ["mini_child"]
    assert settings["config"]["value"] == 21


def test_definitions_from_settings_handles_parameterised_entries():
    from braunschweig.synpp_fork import registry
    definitions = registry.definitions_from_settings({"run": ["a", {"b": {"k": 1}}]})
    assert definitions == [{"descriptor": "a", "config": {}}, {"descriptor": "b", "config": {"k": 1}}]


def test_build_registry_resolves_dependencies(mini_project):
    from braunschweig.synpp_fork import registry
    reg = registry.build_registry(str(mini_project))
    names = sorted(stage["wrapper"].name for stage in reg.values())
    assert names == ["mini_child", "mini_parent"]
    child = next(s for s in reg.values() if s["wrapper"].name == "mini_child")
    assert len(child["dependencies"]) == 1


def test_report_script_still_exposes_build_registry():
    import importlib.util, pathlib
    path = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "report_stage_hash_impact.py"
    spec = importlib.util.spec_from_file_location("report_stage_hash_impact_mod", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert callable(module.build_registry)
