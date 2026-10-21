# tests/test_synpp_fork_staleness.py
"""Own-reason and parent-output staleness, checked against real synpp runs."""
import importlib
import os
import shutil
import sys
import textwrap

import pytest
import synpp
import synpp.pipeline  # process_stages is looked up at call time: braunschweig.synpp_fork.install() may replace it

PARENT = """
from pathlib import Path
def configure(context):
    context.config("value")
    context.config("token_file")
def validate(context):
    return {"format": 2, "python": "3.10", "modules": {"helper": Path(context.config("token_file")).read_text()}}
def execute(context):
    return context.config("value")
"""
CHILD = """
def configure(context):
    context.stage("st_parent")
def execute(context):
    return context.stage("st_parent") * 2
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "st_parent.py").write_text(textwrap.dedent(PARENT), encoding="utf-8")
    (tmp_path / "st_child.py").write_text(textwrap.dedent(CHILD), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    token = tmp_path / "token.txt"
    token.write_text("v1", encoding="utf-8")
    config = {"value": 3, "token_file": str(token)}
    working = tmp_path / "cache"
    working.mkdir()

    def reload():
        for name in ("st_parent", "st_child"):
            monkeypatch.delitem(sys.modules, name, raising=False)
        importlib.invalidate_caches()

    def run():
        reload()
        return synpp.run([{"descriptor": "st_child"}], config, working_directory=str(working),
                         rerun_required=False)

    def registry():
        reload()
        return synpp.pipeline.process_stages([{"descriptor": "st_child"}], config)

    return tmp_path, working, token, config, run, registry


def _by_name(assessments):
    return {a.stage: a for a in assessments.values()}


def test_everything_valid_after_a_run(project):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, _, run, registry = project
    run()
    result = _by_name(staleness.assess(registry(), str(working), metadata.read_meta(str(working)),
                                       rerun_required=False))
    assert not result["st_parent"].stale and not result["st_child"].stale
    assert result["st_child"].stale_if == []


def test_required_targets_are_reported_but_not_unexpected(project):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, _, run, registry = project
    run()
    assessments = staleness.assess(registry(), str(working), metadata.read_meta(str(working)))
    assert _by_name(assessments)["st_child"].reasons == ["required"]
    assert staleness.unexpected(assessments, set()) == []


def test_missing_cache_file_and_transitive_prediction(project):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, _, run, registry = project
    run()
    parent_entry = next(n for n in os.listdir(working) if n.startswith("st_parent__") and n.endswith(".p"))
    os.remove(working / parent_entry)
    result = _by_name(staleness.assess(registry(), str(working), metadata.read_meta(str(working)),
                                       rerun_required=False))
    assert result["st_parent"].reasons == ["cache_missing"]
    assert not result["st_child"].stale and result["st_child"].stale_if == ["st_parent"]
    assert result["st_child"].certain_recompute          # legacy parent: no recorded output hash


def test_token_change_names_the_module(project):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, token, _, run, registry = project
    run()
    token.write_text("v2", encoding="utf-8")
    result = _by_name(staleness.assess(registry(), str(working), metadata.read_meta(str(working)),
                                       rerun_required=False))
    assert result["st_parent"].reasons == ["token_changed:helper"]


def test_module_hash_change_is_detected(project):
    from braunschweig.synpp_fork import metadata, staleness
    tmp_path, working, _, _, run, registry = project
    run()
    (tmp_path / "st_child.py").write_text(textwrap.dedent(CHILD).replace("* 2", "* 3"), encoding="utf-8")
    result = _by_name(staleness.assess(registry(), str(working), metadata.read_meta(str(working)),
                                       rerun_required=False))
    assert result["st_child"].reasons == ["module_hash_changed"]


def test_assess_applies_the_legacy_timestamp_rule_and_reports_it(project):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, _, run, registry = project
    run()
    meta = metadata.read_meta(str(working))
    parent_hash = next(h for h in meta if h.startswith("st_parent__"))
    meta[parent_hash]["updated"] += 10.0          # parent "newer" than the child's record
    result = _by_name(staleness.assess(registry(), str(working), meta, rerun_required=False))
    assert result["st_child"].reasons == ["parent_updated:st_parent"]
    assert result["st_child"].legacy_rules == ["st_parent"]


def test_output_hash_rule_wins_over_timestamps_when_both_sides_carry_hashes(project):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, _, run, registry = project
    run()
    meta = metadata.read_meta(str(working))
    parent_hash = next(h for h in meta if h.startswith("st_parent__"))
    child_hash = next(h for h in meta if h.startswith("st_child__"))
    meta[parent_hash]["updated"] += 10.0
    meta[parent_hash]["output_hash"] = {"format": 1, "digest": "abc"}
    meta[child_hash]["dependency_output_hashes"] = {parent_hash: "abc"}
    result = _by_name(staleness.assess(registry(), str(working), meta, rerun_required=False))
    assert not result["st_child"].stale and result["st_child"].legacy_rules == []
    meta[child_hash]["dependency_output_hashes"] = {parent_hash: "different"}
    result = _by_name(staleness.assess(registry(), str(working), meta, rerun_required=False))
    assert result["st_child"].reasons == ["parent_output_changed:st_parent"]


def test_config_diff_against_the_nearest_cached_variant(project):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, config, run, registry = project
    run()
    config["value"] = 4
    result = _by_name(staleness.assess(registry(), str(working), metadata.read_meta(str(working)),
                                       rerun_required=False))
    assert result["st_parent"].config_diff == {"value": (3, 4)}
    assert "cache_missing" in result["st_parent"].reasons


def test_format_report_has_one_line_per_stage(project):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, _, run, registry = project
    run()
    report = staleness.format_report(staleness.assess(registry(), str(working),
                                                      metadata.read_meta(str(working)), rerun_required=False))
    lines = [line for line in report.splitlines() if line.startswith(("HIT", "STALE"))]
    assert len(lines) == 2


def _parent_and_child_hashes(meta):
    return (next(h for h in meta if h.startswith("st_parent__")),
            next(h for h in meta if h.startswith("st_child__")))


def _remove_pickle(working, prefix):
    entry = next(n for n in os.listdir(working) if n.startswith(prefix) and n.endswith(".p"))
    os.remove(working / entry)


def test_stale_ancestor_without_output_hash_means_certain_recompute(project):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, _, run, registry = project
    run()
    _remove_pickle(working, "st_parent")
    assessments = staleness.assess(registry(), str(working), metadata.read_meta(str(working)),
                                   rerun_required=False)
    child = _by_name(assessments)["st_child"]
    assert child.stale_if == ["st_parent"] and child.certain_recompute
    assert child.unhashed_ancestors == ["st_parent"]
    report = staleness.format_report(assessments)
    assert ("RECOMPUTE st_child  [ancestor st_parent is stale and has no recorded output hash]"
            in report)
    assert "STALE-IF" not in report
    assert "1 will recompute" in report.splitlines()[-1]


def test_stale_ancestor_with_output_hash_stays_stale_if(project):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, _, run, registry = project
    run()
    meta = metadata.read_meta(str(working))
    parent_hash, _ = _parent_and_child_hashes(meta)
    meta[parent_hash]["output_hash"] = {"format": 1, "digest": "abc"}
    _remove_pickle(working, "st_parent")
    assessments = staleness.assess(registry(), str(working), meta, rerun_required=False)
    child = _by_name(assessments)["st_child"]
    assert child.stale_if == ["st_parent"] and not child.certain_recompute
    report = staleness.format_report(assessments)
    assert "STALE-IF st_child  [if output of st_parent changes]" in report
    assert "RECOMPUTE" not in report
    assert "1 may recompute" in report.splitlines()[-1]


def test_missing_parent_timestamp_is_reported_as_malformed_meta(project):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, _, run, registry = project
    run()
    meta = metadata.read_meta(str(working))
    parent_hash, child_hash = _parent_and_child_hashes(meta)
    del meta[parent_hash]["updated"]
    result = _by_name(staleness.assess(registry(), str(working), meta, rerun_required=False))
    assert result["st_child"].reasons == ["meta_malformed:updated:st_parent"]
    meta[parent_hash]["updated"] = 0.0
    meta[child_hash]["dependencies"][parent_hash] = None
    result = _by_name(staleness.assess(registry(), str(working), meta, rerun_required=False))
    assert result["st_child"].reasons == ["meta_malformed:dependency_timestamp:st_parent"]


def test_token_difference_is_opaque_when_modules_is_not_a_mapping():
    from braunschweig.synpp_fork import staleness
    structured = {"modules": {"helper": "v1"}}
    assert staleness.token_difference({"modules": "x"}, structured) == ["opaque"]
    assert staleness.token_difference(structured, {"modules": ["helper"]}) == ["opaque"]
    assert staleness.token_difference(structured, {"modules": {"helper": "v2"}}) == ["helper"]


def test_stored_string_token_against_a_structured_current_token_is_opaque(project):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, _, run, registry = project
    run()
    meta = metadata.read_meta(str(working))
    parent_hash, _ = _parent_and_child_hashes(meta)
    meta[parent_hash]["validation_token"] = "legacy-string-token"
    result = _by_name(staleness.assess(registry(), str(working), meta, rerun_required=False))
    assert result["st_parent"].reasons == ["token_changed:opaque"]


def test_changed_parent_set_is_reported(project):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, _, run, registry = project
    run()
    meta = metadata.read_meta(str(working))
    _, child_hash = _parent_and_child_hashes(meta)
    meta[child_hash]["dependencies"] = {}
    result = _by_name(staleness.assess(registry(), str(working), meta, rerun_required=False))
    assert result["st_child"].reasons == ["parents_changed"]


def test_missing_cache_directory_alone_makes_the_stage_stale(project):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, _, run, registry = project
    run()
    directory = next(n for n in os.listdir(working) if n.startswith("st_parent__") and n.endswith(".cache"))
    shutil.rmtree(working / directory)
    result = _by_name(staleness.assess(registry(), str(working), metadata.read_meta(str(working)),
                                       rerun_required=False))
    assert result["st_parent"].reasons == ["cache_missing"]


DIAMOND = {
    "dm_a": """
from pathlib import Path
def configure(context):
    context.config("token_file")
def validate(context):
    return {"format": 2, "python": "3.10", "modules": {"helper": Path(context.config("token_file")).read_text()}}
def execute(context):
    return 1
""",
    "dm_b": """
def configure(context):
    context.stage("dm_a")
def execute(context):
    return context.stage("dm_a") + 1
""",
    "dm_c": """
def configure(context):
    context.stage("dm_a")
def execute(context):
    return context.stage("dm_a") + 2
""",
    "dm_d": """
def configure(context):
    context.stage("dm_b")
    context.stage("dm_c")
def execute(context):
    return context.stage("dm_b") + context.stage("dm_c")
""",
}


@pytest.fixture
def diamond(tmp_path, monkeypatch):
    for name, source in DIAMOND.items():
        (tmp_path / (name + ".py")).write_text(textwrap.dedent(source), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    token = tmp_path / "token.txt"
    token.write_text("v1", encoding="utf-8")
    config = {"token_file": str(token)}
    working = tmp_path / "cache"
    working.mkdir()

    def reload():
        for name in DIAMOND:
            monkeypatch.delitem(sys.modules, name, raising=False)
        importlib.invalidate_caches()

    def registry():
        reload()
        return synpp.pipeline.process_stages([{"descriptor": "dm_d"}], config)

    def run():
        reload()
        return synpp.run([{"descriptor": "dm_d"}], config, working_directory=str(working),
                         rerun_required=False, verbose=True)

    run()
    return tmp_path, working, token, registry, run


def _own_stale_plus_descendants(registry_dict, assessments):
    import networkx as nx
    graph = nx.DiGraph()
    for stage_hash, stage in registry_dict.items():
        graph.add_node(stage_hash)
        for dependency in stage.get("dependencies", []):
            graph.add_edge(dependency, stage_hash)
    stale = {h for h, a in assessments.items() if a.stale}
    for stage_hash in set(stale):
        stale |= nx.descendants(graph, stage_hash)
    return stale


def _scenario_none(working, token, meta):
    return meta


def _scenario_parent_timestamp(working, token, meta):
    parent = next(h for h in meta if h.startswith("dm_a__"))
    meta[parent]["updated"] += 10.0
    return meta


def _scenario_pickle_removed(working, token, meta):
    _remove_pickle(working, "dm_b")
    return meta


def _scenario_token_changed(working, token, meta):
    token.write_text("v2", encoding="utf-8")
    return meta


@pytest.mark.parametrize("scenario", [_scenario_none, _scenario_parent_timestamp,
                                      _scenario_pickle_removed, _scenario_token_changed])
def test_assess_agrees_with_synpps_own_stale_set(diamond, scenario):
    """Own-reason stale stages plus their graph descendants equal synpp 1.6.2's devalidated set."""
    from braunschweig.synpp_fork import metadata, staleness
    _, working, token, registry, run = diamond
    meta = scenario(working, token, metadata.read_meta(str(working)))
    metadata.write_meta(meta, str(working))
    reg = registry()
    assessments = staleness.assess(reg, str(working), metadata.read_meta(str(working)),
                                   rerun_required=False)
    predicted = _own_stale_plus_descendants(reg, assessments)
    actual = run()["stale"]
    assert predicted == set(actual)
    if scenario is _scenario_none:
        assert predicted == set()
    else:
        assert predicted                                  # the scenario really devalidates something


def test_unexpected_honours_a_non_trivial_expected_set(diamond):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, registry, _ = diamond
    _remove_pickle(working, "dm_b")
    _remove_pickle(working, "dm_c")
    assessments = staleness.assess(registry(), str(working), metadata.read_meta(str(working)))
    names = {a.stage for a in assessments.values() if a.stale}
    assert names == {"dm_b", "dm_c", "dm_d"}              # dm_d is a run target ("required")
    assert staleness.unexpected(assessments, {"dm_b"}) == ["dm_c"]
    assert staleness.unexpected(assessments, {"dm_b", "dm_c"}) == []
    assert staleness.unexpected(assessments, set()) == ["dm_b", "dm_c"]
