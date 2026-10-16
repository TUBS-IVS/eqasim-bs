# tests/test_synpp_fork_staleness.py
"""Own-reason and parent-output staleness, checked against real synpp runs."""
import importlib
import json
import os
import sys
import textwrap

import pytest
import synpp
from synpp.pipeline import process_stages

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
        return process_stages([{"descriptor": "st_child"}], config)

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
