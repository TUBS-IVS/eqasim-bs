"""Guards over the push-gate evidence writers (braunschweig.gate_evidence).

The local push gate (docs/codebase/notes/agent-process-automation.md) trusts these
files instead of re-running the suite, so a wrong ``head``, a CRLF-blind ``dirty`` flag
or a focused run labelled ``full`` would let an unverified commit through.
"""
from __future__ import annotations

import json
import subprocess

import pytest

from braunschweig.gate_evidence import (git_state, junit_counts, platform_tag, run_scope,
                                        write_evidence)


def _git(repo, *args):
    return subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True,
                          check=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A git repository with one committed LF file ``a.txt``."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.org")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / "a.txt").write_bytes(b"one\ntwo\n")
    _git(tmp_path, "add", "a.txt")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_git_state_reports_head_and_crlf_insensitive_dirty(repo):
    sha = _git(repo, "rev-parse", "HEAD")
    # A CRLF-only rewrite is what a Windows checkout looks like from WSL: not a change.
    (repo / "a.txt").write_bytes(b"one\r\ntwo\r\n")
    assert git_state(repo) == {"head": sha, "dirty": False, "warning": None}
    (repo / "a.txt").write_bytes(b"one\r\ntwo\r\nthree\r\n")
    assert git_state(repo)["dirty"] is True


def test_git_state_counts_staged_changes_as_dirty(repo):
    (repo / "a.txt").write_bytes(b"changed\n")
    _git(repo, "add", "a.txt")
    assert git_state(repo)["dirty"] is True


def test_git_state_outside_a_repo_is_unknown_with_warning(tmp_path):
    state = git_state(tmp_path / "missing")
    assert state["head"] is None and state["dirty"] is None
    assert state["warning"]


@pytest.mark.parametrize("args, scope", [
    (["-q", "--durations=30", "--junitxml=test-results.xml"], "full"),
    (["-q", "-n", "4"], "full"),
    (["tests/test_x.py"], "focused"),
    (["tests/test_x.py::test_y", "-q"], "focused"),
    (["-k", "foo"], "focused"),
    (["-kfoo"], "focused"),
    (["-x"], "focused"),
    (["--lf"], "focused"),
    (["--deselect", "tests/test_x.py::test_y"], "focused"),
    (["--collect-only"], "focused"),
])
def test_test_scope_is_full_only_without_selection(args, scope):
    assert run_scope(args) == scope


@pytest.mark.parametrize("pipeline, marker, scope", [
    (True, None, "focused"), (False, "slow", "focused"), (False, None, "full"),
])
def test_test_scope_counts_pipeline_and_markers_as_selection(pipeline, marker, scope):
    assert run_scope(["-q"], pipeline=pipeline, marker=marker) == scope


def test_junit_counts_from_a_minimal_report(tmp_path):
    report = tmp_path / "junit.xml"
    report.write_text('<testsuites><testsuite name="pytest" tests="5" failures="1" '
                      'errors="0" skipped="1"/></testsuites>', encoding="utf-8")
    assert junit_counts(report) == {"passed": 3, "failed": 1, "errors": 0, "skipped": 1}
    assert junit_counts(tmp_path / "absent.xml") == {
        "passed": None, "failed": None, "errors": None, "skipped": None}


def test_write_evidence_is_versioned_json_under_the_gate_dir(tmp_path):
    path = write_evidence(tmp_path, "tests-linux.json", {"exit": 0})
    assert path == tmp_path / ".claude" / "gate" / "tests-linux.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {"schema": 1, "exit": 0}
    assert platform_tag() in ("linux", "windows", "darwin")


def test_documentation_check_writes_docs_evidence(repo, monkeypatch):
    from types import SimpleNamespace

    from braunschweig.documentation import __main__ as cli
    from braunschweig.documentation import checks

    class FakeContext:
        features = stages = datasets = adrs = manifests = ()

        def __init__(self, repo_root, use_dag_extraction):
            pass

    findings = [SimpleNamespace(severity=checks.WARN), SimpleNamespace(severity=checks.OK)]
    monkeypatch.setattr(checks, "CheckContext", FakeContext)
    monkeypatch.setattr(checks, "run_all_checks", lambda context: findings)
    assert cli.main(["check", "--no-dag", "--quiet", "--repo-root", str(repo)]) == 0
    evidence = json.loads((repo / ".claude" / "gate" / "docs-check.json").read_text(encoding="utf-8"))
    assert evidence["head"] == _git(repo, "rev-parse", "HEAD") and evidence["dirty"] is False
    assert (evidence["fail_count"], evidence["warn_count"], evidence["no_dag"]) == (0, 1, True)

    findings.append(SimpleNamespace(severity=checks.FAIL))
    assert cli.main(["check", "--no-dag", "--quiet", "--repo-root", str(repo)]) == 1
    evidence = json.loads((repo / ".claude" / "gate" / "docs-check.json").read_text(encoding="utf-8"))
    assert evidence["fail_count"] == 1
