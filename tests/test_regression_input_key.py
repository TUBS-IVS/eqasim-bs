"""Tests for the regression input key that lets CI reuse a green regression result (ADR-0165).

The key must change for every tracked file a regression test can read and stay the same for the
documentation-only paths in ``REGRESSION_EXCLUDED_PREFIXES``. The guard tests below keep the
exclusion honest: only the documentation tests that the metadata workflow (``docs.yml``) also runs
on every pull request may read those paths.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from scripts import regression_input_key as tool

REPO = Path(__file__).resolve().parents[1]


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "test@example.org")
    _git(tmp_path, "config", "user.name", "Test")
    _git(tmp_path, "config", "core.autocrlf", "false")
    for relative, text in {
        "braunschweig/model.py": "VALUE = 1\n",
        "docs/generated/STATUS.md": "ADRs: 1\n",
        "docs/decisions/ADR-0001-a.md": "# ADR-0001\n",
        "docs/runs/run-a.yml": "run_id: run-a\n",
    }.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    _git(tmp_path, "add", "--", "braunschweig/model.py", "docs/generated/STATUS.md",
         "docs/decisions/ADR-0001-a.md", "docs/runs/run-a.yml")
    _git(tmp_path, "commit", "-q", "-m", "initial")
    return tmp_path


def _commit_change(repo: Path, relative: str, text: str) -> None:
    path = repo / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    _git(repo, "add", "--", relative)
    _git(repo, "commit", "-q", "-m", f"change {relative}")


def test_key_ignores_generated_views_and_adrs(repo):
    before = tool.regression_input_key(repo)
    _commit_change(repo, "docs/generated/STATUS.md", "ADRs: 2\n")
    _commit_change(repo, "docs/decisions/ADR-0002-b.md", "# ADR-0002\n")
    assert tool.regression_input_key(repo) == before


@pytest.mark.parametrize("relative", ["braunschweig/model.py", "docs/runs/run-a.yml", "docs/generated.md",
                                      "tests/test_new.py"])
def test_key_changes_for_every_other_tracked_path(repo, relative):
    before = tool.regression_input_key(repo)
    _commit_change(repo, relative, "changed\n")
    assert tool.regression_input_key(repo) != before


def test_key_reads_the_commit_not_the_working_tree(repo):
    before = tool.regression_input_key(repo)
    (repo / "braunschweig" / "model.py").write_text("VALUE = 2\n", encoding="utf-8")
    assert tool.regression_input_key(repo) == before


def test_key_is_a_sha256_hex_digest_and_the_cli_prints_it(repo, capsys):
    key = tool.regression_input_key(repo)
    assert re.fullmatch(r"[0-9a-f]{64}", key)
    assert tool.main(["--repo-root", str(repo)]) == 0
    assert capsys.readouterr().out.strip() == key


def test_key_counts_included_and_excluded_paths(repo, capsys):
    tool.main(["--repo-root", str(repo), "--verbose"])
    assert "included 2 path(s), excluded 2 documentation-only path(s)" in capsys.readouterr().err


# --- Guards: the exclusion is only valid while no regression-only test reads the excluded paths ---------------

#: Test files that read the excluded paths from the real repository. Every one of them must also run in the
#: metadata workflow (docs.yml) on every pull request, so a change under an excluded prefix is still tested.
DOCS_WORKFLOW = REPO / ".github" / "workflows" / "docs.yml"
#: Mentions that are not reads: test_no_german_pt_ticket_literals names docs/decisions/ only to explain why
#: it does NOT scan it (it scans docs/features/), this file names the prefixes to test the key, and
#: test_sync_main writes docs/generated/ only inside throw-away repositories and reads .gitattributes.
NOT_A_READ = {"test_no_german_pt_ticket_literals.py", "test_regression_input_key.py", "test_sync_main.py"}


def _docs_workflow_test_files() -> set:
    return set(re.findall(r"tests/(test_[A-Za-z0-9_]+\.py)", DOCS_WORKFLOW.read_text(encoding="utf-8")))


def _mentions_excluded_prefix(text: str) -> bool:
    for prefix in tool.REGRESSION_EXCLUDED_PREFIXES:
        directory = prefix.rstrip("/").split("/")[-1]
        if prefix.rstrip("/") in text or re.search(rf"[\"']docs[\"']\s*[,/]\s*[\"']{directory}[\"']", text):
            return True
    return False


def test_only_docs_workflow_tests_read_the_excluded_paths():
    readers = sorted(path.name for path in (REPO / "tests").rglob("*.py")
                     if path.name not in NOT_A_READ and _mentions_excluded_prefix(path.read_text(encoding="utf-8")))
    covered = _docs_workflow_test_files()
    assert covered, f"no test files found in {DOCS_WORKFLOW}; the guard would pass vacuously"
    assert not [name for name in readers if name not in covered], (
        f"these tests read {tool.REGRESSION_EXCLUDED_PREFIXES} but docs.yml does not run them, so CI could "
        f"reuse a green regression result without them: {[n for n in readers if n not in covered]}. "
        "Add them to docs.yml or drop the prefix from REGRESSION_EXCLUDED_PREFIXES (ADR-0165).")


#: Tests that import the documentation package but, as reviewed for ADR-0165, never read the excluded paths
#: of the real repository: the gate-evidence tests replace CheckContext and run_all_checks with fakes, the DAG
#: tests use only braunschweig.documentation.dag, and the other two name the package in prose.
IMPORTS_DOCUMENTATION_WITHOUT_READING = {
    "test_documentation_dag.py", "test_gate_evidence.py", "test_ipf_config_parity.py",
    "test_simwrapper_dashboards_default.py",
}


def test_every_importer_of_the_documentation_package_is_reviewed():
    # A test can reach the excluded paths through the documentation package without naming them; a new
    # importer must be added to docs.yml or reviewed into IMPORTS_DOCUMENTATION_WITHOUT_READING.
    importers = sorted(path.name for path in (REPO / "tests").rglob("*.py")
                       if path.name not in NOT_A_READ
                       and re.search(r"braunschweig\.documentation|braunschweig import documentation",
                                     path.read_text(encoding="utf-8")))
    unreviewed = [name for name in importers
                  if name not in _docs_workflow_test_files() | IMPORTS_DOCUMENTATION_WITHOUT_READING]
    assert not unreviewed, f"unreviewed importers of braunschweig.documentation (ADR-0165): {unreviewed}"


def test_the_documentation_readers_are_detected():
    # Tripwire against a scan that silently finds nothing: the documentation checks read both prefixes.
    text = (REPO / "braunschweig" / "documentation" / "checks.py").read_text(encoding="utf-8")
    assert _mentions_excluded_prefix(text)
    assert "test_documentation_checks.py" in _docs_workflow_test_files()


def test_the_excluded_prefixes_are_exactly_the_documented_ones():
    assert tool.REGRESSION_EXCLUDED_PREFIXES == ("docs/generated/", "docs/decisions/")
