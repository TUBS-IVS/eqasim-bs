"""Tests for scripts/check_generated_views_untouched.py and the ADR-0168 merge contract.

Defects caught: two sibling pull requests that each add a record conflicting in
docs/generated/ under a plain git merge (GitHub runs no custom merge driver), and a pull
request that commits a rebuilt view slipping past the CI guard.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import check_generated_views_untouched as guard

VIEW = "docs/generated/STATUS.md"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _write_view(repo: Path) -> None:
    records = sorted(p.name for p in (repo / "docs" / "registry").glob("*.yml"))
    (repo / VIEW).write_bytes((f"records: {len(records)}\n" + "".join(f"- {r}\n" for r in records)).encode())


def _add_record(repo: Path, name: str, with_view: bool) -> None:
    (repo / "docs" / "registry" / name).write_text(f"id: {name}\n", encoding="utf-8")
    paths = [f"docs/registry/{name}"]
    if with_view:
        _write_view(repo)
        paths.append(VIEW)
    _git(repo, "add", "--", *paths)
    _git(repo, "commit", "-q", "-m", f"add {name}")


@pytest.fixture
def repo(tmp_path):
    """A repository with a rebuilt view on main and NO generated-view driver configured, as on GitHub."""
    _git(tmp_path, "init", "-q", "-b", "main")
    for key, value in (("user.email", "test@example.org"), ("user.name", "Test"), ("core.autocrlf", "false")):
        _git(tmp_path, "config", key, value)
    (tmp_path / "docs" / "registry").mkdir(parents=True)
    (tmp_path / "docs" / "generated").mkdir(parents=True)
    _add_record(tmp_path, "a.yml", with_view=True)
    return tmp_path


def _merge_into_main(repo: Path, branch: str) -> subprocess.CompletedProcess:
    _git(repo, "switch", "-q", "main")
    return subprocess.run(["git", "merge", "--no-ff", "--no-edit", branch], cwd=repo, capture_output=True, text=True)


def test_sibling_branches_that_leave_the_views_alone_merge_without_a_driver(repo):
    for branch, record in (("sibling-1", "b.yml"), ("sibling-2", "c.yml")):
        _git(repo, "switch", "-q", "-c", branch, "main")
        _add_record(repo, record, with_view=False)
        assert guard.changed_views(repo, "main") == []

    assert _merge_into_main(repo, "sibling-1").returncode == 0
    _write_view(repo)  # the generated-views workflow rebuilds main after the merge
    _git(repo, "commit", "-q", "-am", "rebuild the generated views")

    second = _merge_into_main(repo, "sibling-2")
    assert second.returncode == 0, second.stdout + second.stderr
    _write_view(repo)
    assert (repo / VIEW).read_text(encoding="utf-8") == "records: 3\n- a.yml\n- b.yml\n- c.yml\n"


def test_the_guard_fails_a_branch_that_commits_a_rebuilt_view(repo, capsys):
    _git(repo, "switch", "-q", "-c", "feature")
    _add_record(repo, "b.yml", with_view=True)
    assert guard.main(["--base", "main", "--repo-root", str(repo)]) == 1
    output = capsys.readouterr().out
    assert VIEW in output and "git sync-main" in output
    _git(repo, "checkout", "main", "--", "docs/generated")
    _git(repo, "commit", "-q", "-m", "reset the views")
    assert guard.main(["--base", "main", "--repo-root", str(repo)]) == 0
