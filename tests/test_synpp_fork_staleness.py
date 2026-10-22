# tests/test_synpp_fork_staleness.py
"""Own-reason and parent-output staleness, checked against real synpp runs.

Two kinds of run appear here and must not be mixed up. ``_pristine_run`` is synpp 1.6.2's own
``run()`` (legacy records, every descendant of a stale stage devalidated): the upstream-differential
tests compare ``assess`` with what that function devalidates. ``_fork_run`` is the fork's
``run()`` (output hashes, early cutoff): the fork tests compare ``assess`` with what it executes.
"""
import importlib
import os
import shutil
import sys
import textwrap

import pytest
import synpp
import synpp.pipeline  # process_stages is looked up at call time: braunschweig.synpp_fork.install() may replace it

from tests.synpp_pristine import upstream_run


def _pristine_run(*args, **kwargs):
    return upstream_run()(*args, **kwargs)


def _fork_run(*args, **kwargs):
    from braunschweig import synpp_fork
    from braunschweig.synpp_fork import pipeline as fork
    synpp_fork.install()
    return fork.run(*args, **kwargs)

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
        return _pristine_run([{"descriptor": "st_child"}], config, working_directory=str(working),
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
    lines = [line for line in report.splitlines() if line.startswith(("HIT", "STALE", "RECOMPUTE"))]
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
    assert ("RECOMPUTE st_child  [ancestor st_parent is stale; no recorded output hash cuts the cascade off]"
            in report)
    assert "STALE-IF" not in report
    assert "1 will recompute" in report.splitlines()[-1]


def test_stale_ancestor_whose_digest_the_child_recorded_stays_stale_if(project):
    """The child's own record decides (ruling R16-2): it holds the digest, so early cutoff can spare it."""
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, _, run, registry = project
    run()
    meta = metadata.read_meta(str(working))
    parent_hash, child_hash = _parent_and_child_hashes(meta)
    meta[child_hash]["dependency_output_hashes"] = {parent_hash: "abc"}
    _remove_pickle(working, "st_parent")
    assessments = staleness.assess(registry(), str(working), meta, rerun_required=False)
    child = _by_name(assessments)["st_child"]
    assert child.stale_if == ["st_parent"] and not child.certain_recompute
    assert child.unhashed_ancestors == []
    report = staleness.format_report(assessments)
    assert "STALE-IF st_child  [if output of st_parent changes]" in report
    assert "RECOMPUTE" not in report and "synpp 1.6.2" not in report
    assert "1 may recompute (stale ancestor, early cutoff decides)" in report.splitlines()[-1]


def test_a_parent_output_hash_does_not_help_a_child_that_recorded_none(project):
    """The parent carries a digest, the child's record has none for it: nothing to compare, certain recompute."""
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, _, run, registry = project
    run()
    meta = metadata.read_meta(str(working))
    parent_hash, child_hash = _parent_and_child_hashes(meta)
    meta[parent_hash]["output_hash"] = {"format": 1, "digest": "abc"}
    assert "dependency_output_hashes" not in meta[child_hash]
    _remove_pickle(working, "st_parent")
    child = _by_name(staleness.assess(registry(), str(working), meta, rerun_required=False))["st_child"]
    assert child.certain_recompute and child.unhashed_ancestors == ["st_parent"]


def test_stale_if_label_names_synpp_1_6_2_while_the_early_cutoff_flag_is_off(project, monkeypatch):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, _, run, registry = project
    run()
    meta = metadata.read_meta(str(working))
    parent_hash, child_hash = _parent_and_child_hashes(meta)
    meta[child_hash]["dependency_output_hashes"] = {parent_hash: "abc"}
    _remove_pickle(working, "st_parent")
    assessments = staleness.assess(registry(), str(working), meta, rerun_required=False)
    assert staleness.EARLY_CUTOFF_ACTIVE is True         # the fork's run() decides children by output hash
    monkeypatch.setattr(staleness, "EARLY_CUTOFF_ACTIVE", False)
    report = staleness.format_report(assessments)
    assert ("STALE-IF st_child  [recomputes under synpp 1.6.2; the fork would reuse it if "
            "st_parent output is unchanged]") in report
    last = report.splitlines()[-1]
    assert "1 may recompute" in last and "synpp 1.6.2 recomputes" in last


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


def test_token_difference_names_module_and_non_module_keys():
    """A composite token (modules plus ``files``) must say WHAT differs, not collapse to ``format``."""
    from braunschweig.synpp_fork import staleness
    base = {"format": 2, "python": "3.10", "modules": {"a": "1", "b": "2"}, "files": {"t.csv": "x"}}
    assert staleness.token_difference({**base, "files": {"t.csv": "y"}}, base) == ["files"]
    assert staleness.token_difference({**base, "python": "3.11"}, base) == ["python"]
    assert staleness.token_difference({**base, "format": 3}, base) == ["format"]
    changed = {**base, "modules": {"a": "9", "b": "2"}, "files": {"t.csv": "y"}}
    assert staleness.token_difference(changed, base) == ["a", "files"]
    only_current = {k: v for k, v in base.items() if k != "files"}
    assert staleness.token_difference(only_current, base) == ["files"]


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
    # The token covers the token file AND the payload file; the output is the payload: a token
    # edit re-executes dm_a with an identical output, a payload edit with a changed one.
    "dm_a": """
from pathlib import Path
def configure(context):
    context.config("token_file")
    context.config("payload_file")
def validate(context):
    text = Path(context.config("token_file")).read_text() + Path(context.config("payload_file")).read_text()
    return {"format": 2, "python": "3.10", "modules": {"helper": text}}
def execute(context):
    return int(Path(context.config("payload_file")).read_text())
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


def _build_diamond(tmp_path, monkeypatch, run_function):
    """dm_a -> (dm_b, dm_c) -> dm_d, populated by ``run_function`` (pristine or fork run)."""
    for name, source in DIAMOND.items():
        (tmp_path / (name + ".py")).write_text(textwrap.dedent(source), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    token = tmp_path / "token.txt"
    token.write_text("v1", encoding="utf-8")
    payload = tmp_path / "payload.txt"
    payload.write_text("1", encoding="utf-8")
    config = {"token_file": str(token), "payload_file": str(payload)}
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
        return run_function([{"descriptor": "dm_d"}], config, working_directory=str(working),
                            rerun_required=False, verbose=True)

    run()
    return tmp_path, working, token, registry, run


@pytest.fixture
def diamond(tmp_path, monkeypatch):
    """Legacy records written by synpp 1.6.2's own run(); ``run`` is that run too."""
    return _build_diamond(tmp_path, monkeypatch, _pristine_run)


@pytest.fixture
def fork_diamond(tmp_path, monkeypatch):
    """Records written by the fork's run() (output hashes everywhere); ``run`` is the fork's run."""
    return _build_diamond(tmp_path, monkeypatch, _fork_run)


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
    assert {h for h, a in assessments.items() if a.stale or a.stale_if} == set(actual)
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


CHAIN = {
    "ch_g": "def execute(context):\n    return 1\n",
    "ch_h": "def execute(context):\n    return 2\n",
    "ch_x": ('def configure(context):\n    context.stage("ch_g")\n    context.stage("ch_h")\n'
             'def execute(context):\n    return context.stage("ch_g") + context.stage("ch_h")\n'),
    "ch_c": ('def configure(context):\n    context.stage("ch_x")\n'
             'def execute(context):\n    return context.stage("ch_x") + 1\n'),
}


def test_child_inherits_only_from_non_stale_dependencies(tmp_path, monkeypatch):
    """G stale and unhashed, X stale for its OWN reason, child C that recorded X's digest.

    C is STALE-IF on X; the unhashed grandparent G must not turn C into a certain RECOMPUTE,
    because C's recorded digest of X can still stop the propagation (early cutoff).
    """
    from braunschweig.synpp_fork import metadata, staleness
    for name, source in CHAIN.items():
        (tmp_path / (name + ".py")).write_text(source, encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))
    working = tmp_path / "cache"
    working.mkdir()

    def build():
        for name in CHAIN:
            monkeypatch.delitem(sys.modules, name, raising=False)
        importlib.invalidate_caches()
        return synpp.pipeline.process_stages([{"descriptor": "ch_c"}], {})

    for name in CHAIN:
        monkeypatch.delitem(sys.modules, name, raising=False)
    importlib.invalidate_caches()
    _pristine_run([{"descriptor": "ch_c"}], {}, working_directory=str(working), rerun_required=False)
    meta = metadata.read_meta(str(working))

    def hash_of(name):
        return next(h for h in meta if h.split("__")[0] == name)

    _remove_pickle(working, "ch_g")                       # G: stale (cache_missing), no output hash
    meta[hash_of("ch_h")]["updated"] += 10.0              # X: own reason via the legacy rule on H
    meta[hash_of("ch_c")]["dependency_output_hashes"] = {hash_of("ch_x"): "abc"}
    result = _by_name(staleness.assess(build(), str(working), meta, rerun_required=False))
    assert result["ch_g"].reasons == ["cache_missing"]
    assert result["ch_x"].reasons == ["parent_updated:ch_h"]
    assert result["ch_c"].stale_if == ["ch_x"]
    assert result["ch_c"].unhashed_ancestors == []
    assert not result["ch_c"].certain_recompute


def _write_modules(tmp_path, monkeypatch, sources):
    for name, source in sources.items():
        (tmp_path / (name + ".py")).write_text(textwrap.dedent(source), encoding="utf-8")
    monkeypatch.syspath_prepend(str(tmp_path))

    def reload():
        for name in sources:
            monkeypatch.delitem(sys.modules, name, raising=False)
        importlib.invalidate_caches()
    return reload


def _stage(parent=None, token=False, ephemeral=False, extra_parent=None):
    """Source of a tiny stage: optional parent (optionally ephemeral), a second plain parent and a token module."""
    lines = []
    if token:
        lines.append("from pathlib import Path")
    lines.append("def configure(context):")
    lines.append("    pass")
    if token:
        lines.append("    context.config('token_file')")
    if parent:
        lines.append(f"    context.stage('{parent}', ephemeral={ephemeral})")
    if extra_parent:
        lines.append(f"    context.stage('{extra_parent}')")
    if token:
        lines.append("def validate(context):")
        lines.append("    return {'format': 2, 'python': '3.10', "
                     "'modules': {'helper': Path(context.config('token_file')).read_text()}}")
    lines.append("def execute(context):")
    total = " + ".join(f"context.stage('{name}')" for name in (parent, extra_parent) if name) or "1"
    lines.append(f"    return {total}")
    return "\n".join(lines) + "\n"


LINEAR = {"ln_a": _stage(token=True), "ln_b": _stage("ln_a"), "ln_c": _stage("ln_b"), "ln_d": _stage("ln_c")}


def test_assess_agrees_with_synpp_over_a_three_level_chain(tmp_path, monkeypatch):
    """a -> b -> c -> d, token change on a: the transitive path equals synpp's devalidated set."""
    from braunschweig.synpp_fork import metadata, staleness
    reload = _write_modules(tmp_path, monkeypatch, LINEAR)
    token = tmp_path / "token.txt"
    token.write_text("v1", encoding="utf-8")
    config = {"token_file": str(token)}
    working = tmp_path / "cache"
    working.mkdir()
    reload()
    _pristine_run([{"descriptor": "ln_d"}], config, working_directory=str(working), rerun_required=False)
    token.write_text("v2", encoding="utf-8")
    reload()
    assessments = staleness.assess(synpp.pipeline.process_stages([{"descriptor": "ln_d"}], config),
                                   str(working), metadata.read_meta(str(working)), rerun_required=False)
    reload()
    actual = _pristine_run([{"descriptor": "ln_d"}], config, working_directory=str(working),
                       rerun_required=False, verbose=True)["stale"]
    by_name = _by_name(assessments)
    assert by_name["ln_a"].reasons == ["token_changed:helper"]
    assert by_name["ln_d"].stale_if == ["ln_a"]           # inherited over two non-stale levels
    assert {h for h, a in assessments.items() if a.stale or a.stale_if} == set(actual)
    assert len(actual) == 4


def test_stale_if_and_unhashed_ancestor_hashes_run_parallel_to_the_names(project):
    from braunschweig.synpp_fork import metadata, staleness
    _, working, _, _, run, registry = project
    run()
    meta = metadata.read_meta(str(working))
    parent_hash, _ = _parent_and_child_hashes(meta)
    _remove_pickle(working, "st_parent")
    child = _by_name(staleness.assess(registry(), str(working), meta, rerun_required=False))["st_child"]
    assert child.stale_if == ["st_parent"] and child.stale_if_hashes == [parent_hash]
    assert child.unhashed_ancestors == ["st_parent"] and child.unhashed_ancestor_hashes == [parent_hash]


CUTOFF = {"co_g": _stage(), "co_b": _stage("co_g"), "co_c": _stage("co_b")}


def test_hashed_intermediate_is_the_cutoff_point_for_the_unhashed_flag(tmp_path, monkeypatch):
    """G stale and unhashed -> B valid -> C that recorded B's digest.

    B recomputes for certain (its record holds no digest of G), but C's recorded digest of B can
    still spare C once the fork compares it with B's new output, so C is STALE-IF, not a
    certain RECOMPUTE.
    """
    from braunschweig.synpp_fork import metadata, staleness
    reload = _write_modules(tmp_path, monkeypatch, CUTOFF)
    working = tmp_path / "cache"
    working.mkdir()
    reload()
    _pristine_run([{"descriptor": "co_c"}], {}, working_directory=str(working), rerun_required=False)
    meta = metadata.read_meta(str(working))
    b_hash = next(h for h in meta if h.split("__")[0] == "co_b")
    c_hash = next(h for h in meta if h.split("__")[0] == "co_c")
    meta[c_hash]["dependency_output_hashes"] = {b_hash: "abc"}
    _remove_pickle(working, "co_g")
    reload()
    result = _by_name(staleness.assess(synpp.pipeline.process_stages([{"descriptor": "co_c"}], {}),
                                       str(working), meta, rerun_required=False))
    assert result["co_b"].certain_recompute and result["co_b"].unhashed_ancestors == ["co_g"]
    assert result["co_c"].stale_if == ["co_g"]
    assert result["co_c"].unhashed_ancestors == [] and not result["co_c"].certain_recompute


# ep_c declares its parent ep_e ephemeral; synpp makes every ancestor of an ephemeral stage ephemeral
# too, unless another path reaches it non-ephemerally (ep_x reaches ep_g directly).
EPHEMERAL = {"ep_g": _stage(token=True), "ep_e": _stage("ep_g"), "ep_c": _stage("ep_e", ephemeral=True),
             "ep_x": _stage("ep_c", extra_parent="ep_g")}


def _ephemeral_project(tmp_path, monkeypatch, target):
    reload = _write_modules(tmp_path, monkeypatch, EPHEMERAL)
    token = tmp_path / "token.txt"
    token.write_text("v1", encoding="utf-8")
    config = {"token_file": str(token)}
    working = tmp_path / "cache"
    working.mkdir()

    def registry():
        reload()
        return synpp.pipeline.process_stages([{"descriptor": target}], config)

    def run():
        reload()
        return _pristine_run([{"descriptor": target}], config, working_directory=str(working),
                         rerun_required=False, verbose=True)
    run()
    return working, token, registry, run


def test_uncached_ephemeral_stage_is_neither_stale_nor_unexpected(tmp_path, monkeypatch):
    """synpp excludes an uncached ephemeral stage from its own checks, so its child is a HIT."""
    from braunschweig.synpp_fork import metadata, staleness
    working, _, registry, run = _ephemeral_project(tmp_path, monkeypatch, "ep_x")
    assessments = staleness.assess(registry(), str(working), metadata.read_meta(str(working)),
                                   rerun_required=False)
    result = _by_name(assessments)
    assert result["ep_e"].ephemeral_uncached and result["ep_e"].reasons == []
    assert not result["ep_g"].ephemeral_uncached          # reached non-ephemerally through ep_x
    for name in ("ep_g", "ep_c", "ep_x"):
        assert not result[name].stale and not result[name].stale_if and not result[name].certain_recompute
    assert staleness.unexpected(assessments, set()) == []
    assert {h for h, a in assessments.items() if a.stale or a.stale_if} == set(run()["stale"]) == set()
    report = staleness.format_report(assessments)
    assert "EPHEMERAL ep_e  [not cached; synpp recomputes it only if a descendant runs]" in report
    assert ("# WARNING: 1 ephemeral stage(s) in the registry; the fork disables early cutoff "
            "for such runs (spec 4.4)") in report
    assert "HIT      ep_c" in report


def test_stale_ancestor_above_an_uncached_ephemeral_stage_reaches_its_descendants(tmp_path, monkeypatch):
    from braunschweig.synpp_fork import metadata, staleness
    working, token, registry, run = _ephemeral_project(tmp_path, monkeypatch, "ep_x")
    token.write_text("v2", encoding="utf-8")
    assessments = staleness.assess(registry(), str(working), metadata.read_meta(str(working)),
                                   rerun_required=False)
    result = _by_name(assessments)
    assert result["ep_g"].reasons == ["token_changed:helper"]
    assert result["ep_e"].reasons == [] and result["ep_e"].stale_if == ["ep_g"]
    assert result["ep_c"].stale_if == ["ep_g"] and result["ep_c"].certain_recompute
    assert staleness.unexpected(assessments, set()) == ["ep_g"]
    assert {h for h, a in assessments.items() if a.stale or a.stale_if} == set(run()["stale"])


def test_all_ephemeral_ancestry_is_ignored_by_synpp_and_by_assess(tmp_path, monkeypatch):
    """With ep_c as target ep_g and ep_e are both ephemeral: a token change on ep_g stales nothing."""
    from braunschweig.synpp_fork import metadata, staleness
    working, token, registry, run = _ephemeral_project(tmp_path, monkeypatch, "ep_c")
    token.write_text("v2", encoding="utf-8")
    assessments = staleness.assess(registry(), str(working), metadata.read_meta(str(working)),
                                   rerun_required=False)
    assert all(not a.stale and not a.stale_if for a in assessments.values())
    assert sum(a.ephemeral_uncached for a in assessments.values()) == 2
    assert set(run()["stale"]) == set()
    assert "# WARNING: 2 ephemeral stage(s)" in staleness.format_report(assessments)


# --- the fork's run(): early cutoff, mirrored exactly by assess (rulings R16-2 and R16-4) -------------

def _scenario_payload_changed(working, token, meta):
    (token.parent / "payload.txt").write_text("3", encoding="utf-8")
    return meta


def _names(hashes):
    return {h.split("__")[0] for h in hashes}


@pytest.mark.parametrize("scenario, expected", [
    (_scenario_token_changed, {"dm_a"}),                                    # dm_a's output is unchanged
    (_scenario_payload_changed, {"dm_a", "dm_b", "dm_c", "dm_d"}),          # dm_a's output changed
    (_scenario_pickle_removed, {"dm_b"}),                                   # dm_b rebuilds identically
])
def test_the_fork_executes_own_stale_stages_plus_stale_if_stages_whose_ancestor_output_changed(
        fork_diamond, scenario, expected):
    """Pristine synpp would execute every descendant; the fork executes only what really changed."""
    from braunschweig.synpp_fork import metadata, staleness
    _, working, token, registry, run = fork_diamond
    metadata.write_meta(scenario(working, token, metadata.read_meta(str(working))), str(working))
    assessments = staleness.assess(registry(), str(working), metadata.read_meta(str(working)),
                                   rerun_required=False)
    own = {a.stage for a in assessments.values() if a.stale}
    maybe = {a.stage for a in assessments.values() if not a.stale and a.stale_if}
    assert not any(a.certain_recompute for a in assessments.values())       # every record has digests
    executed = _names(run()["stale"])
    assert executed == expected
    assert own <= executed <= own | maybe


@pytest.mark.parametrize("unrecorded, expected", [
    ((), {"ln_a"}),
    (("ln_c", "ln_d"), {"ln_a"}),                      # unrecorded edges behind a stage that can still be spared
    (("ln_b",), {"ln_a", "ln_b"}),
    (("ln_b", "ln_c"), {"ln_a", "ln_b", "ln_c"}),
    (("ln_b", "ln_c", "ln_d"), {"ln_a", "ln_b", "ln_c", "ln_d"}),
])
def test_assess_names_exactly_the_stages_the_fork_cannot_spare(tmp_path, monkeypatch, unrecorded, expected):
    """a -> b -> c -> d, a re-executes with an identical output; a child that recorded no digest of its
    parent recomputes, and the chain breaks exactly where such a record is missing (ruling R16-2)."""
    from braunschweig.synpp_fork import metadata, staleness
    reload = _write_modules(tmp_path, monkeypatch, LINEAR)
    token = tmp_path / "token.txt"
    token.write_text("v1", encoding="utf-8")
    config = {"token_file": str(token)}
    working = tmp_path / "cache"
    working.mkdir()

    def run():
        reload()
        return _fork_run([{"descriptor": "ln_d"}], config, working_directory=str(working),
                         rerun_required=False, verbose=True)

    run()
    meta = metadata.read_meta(str(working))
    parent_of = {"ln_b": "ln_a", "ln_c": "ln_b", "ln_d": "ln_c"}

    def hash_of(name):
        return next(h for h in meta if h.split("__")[0] == name)

    for child in unrecorded:
        del meta[hash_of(child)]["dependency_output_hashes"][hash_of(parent_of[child])]
    metadata.write_meta(meta, str(working))
    token.write_text("v2", encoding="utf-8")
    reload()
    assessments = staleness.assess(synpp.pipeline.process_stages([{"descriptor": "ln_d"}], config),
                                   str(working), metadata.read_meta(str(working)), rerun_required=False)
    assert {a.stage for a in assessments.values() if a.stale or a.certain_recompute} == expected
    assert _names(run()["stale"]) == expected
