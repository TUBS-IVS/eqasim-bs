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


CHAIN = {"gt_a": (), "gt_b": ("gt_a",), "gt_c": ("gt_b",)}
# gt_c declares its parent gt_e ephemeral, so gt_e is uncached after a run; gt_g stays cached
# because gt_x reaches it directly.
EPHEMERAL = {"gt_g": (), "gt_e": ("gt_g",), "gt_c": ("gt_e",), "gt_x": ("gt_c", "gt_g")}


def _populate(tmp_path, monkeypatch, graph, run_function, ephemeral=()):
    """Write the stages of ``graph`` (name -> parents), run the last one with ``run_function``."""
    for name, parents in graph.items():
        body = "".join("    context.stage('%s'%s)\n" % (parent, ", ephemeral=True" if (name, parent) in ephemeral else "")
                       for parent in parents) or "    pass\n"
        value = " + ".join("context.stage('%s')" % parent for parent in parents) or "1"
        (tmp_path / (name + ".py")).write_text(
            "def configure(context):\n%sdef execute(context):\n    return %s\n" % (body, value), encoding="utf-8")
        monkeypatch.delitem(sys.modules, name, raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    importlib.invalidate_caches()
    working = tmp_path / "cache"
    working.mkdir()
    run_function([{"descriptor": list(graph)[-1]}], {}, working_directory=str(working), rerun_required=False)
    return working


@pytest.mark.parametrize("graph, ephemeral, stale, writer, cutoff_active, further", [
    (CHAIN, (), "gt_a", "pristine", True, 1),             # legacy records: nothing cuts gt_b off
    (CHAIN, (), "gt_a", "fork", True, 0),                 # recorded digests: early cutoff may spare gt_b
    (CHAIN, (), "gt_a", "fork", False, 1),                # plain synpp 1.6.2 would recompute gt_b
    (EPHEMERAL, {("gt_c", "gt_e")}, "gt_g", "fork", True, 1),   # eager rule: gt_c recomputes for certain
])
def test_gate_notes_how_many_further_stages_recompute_without_aborting(
        tmp_path, monkeypatch, caplog, graph, ephemeral, stale, writer, cutoff_active, further):
    """The stale stage is expected; the note counts only the stages that recompute for certain."""
    from braunschweig import synpp_fork
    from braunschweig.synpp_fork import pipeline as fork, staleness
    from tests.synpp_pristine import upstream_run
    synpp_fork.install()
    run_function = fork.run if writer == "fork" else upstream_run()
    working = _populate(tmp_path, monkeypatch, graph, run_function, ephemeral)
    os.remove(working / (stale + ".p"))                  # a config-less stage's cache entry is named by its stage
    monkeypatch.setattr(staleness, "EARLY_CUTOFF_ACTIVE", cutoff_active)
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump({
        "working_directory": str(working), "run": [list(graph)[-1]],
        "config": {"synpp_explain_before_run": True, "synpp_expected_stale_stages": [stale]}}),
        encoding="utf-8")
    for name in graph:
        monkeypatch.delitem(sys.modules, name, raising=False)
    with caplog.at_level("INFO"):
        assert _load().explain_before_run(str(config_path)) == 0
    note = "note: %d further stage(s) will recompute because an ancestor is stale" % further
    assert (note in caplog.text) if further else ("further stage(s)" not in caplog.text)


def test_a_validate_that_raises_for_a_new_stage_stops_the_launch_like_synpp_does(tmp_path, monkeypatch):
    """synpp validates every cached stage before executing anything, a stage with no record included;
    the pre-run assessment must surface the same exception instead of reporting the stage as stale."""
    (tmp_path / "vr_stage.py").write_text(
        "def validate(context):\n    raise RuntimeError('missing input of vr_stage')\n"
        "def execute(context):\n    return 1\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.delitem(sys.modules, "vr_stage", raising=False)
    importlib.invalidate_caches()
    working = tmp_path / "cache"
    working.mkdir()
    config_path = tmp_path / "config.yml"
    config_path.write_text(yaml.safe_dump({"working_directory": str(working), "run": ["vr_stage"],
                                           "config": {"synpp_explain_before_run": True}}), encoding="utf-8")
    with pytest.raises(RuntimeError, match="missing input of vr_stage"):
        _load().explain_before_run(str(config_path))
