"""Tests for scripts/sync_main.py: merging origin/main with the generated-view merge driver (ADR-0165).

Each test builds a throw-away origin + clone. The fake "documentation build" renders
docs/generated/STATUS.md from the record files under docs/registry/, exactly the shape that makes
the real views conflict: both branches add a record, so both change the counter line.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import sync_main as tool

REPO = Path(__file__).resolve().parents[1]

FAKE_BUILD = (sys.executable, "-c",
              "from pathlib import Path; records = sorted(p.name for p in Path('docs/registry').glob('*.yml')); "
              "Path('docs/generated/STATUS.md').write_bytes(('records: %d\\n' % len(records) + "
              "''.join('- %s\\n' % r for r in records)).encode())")
PASSING_CHECK = (sys.executable, "-c", "raise SystemExit(0)")
FAILING_CHECK = (sys.executable, "-c", "raise SystemExit(1)")


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _commit_record(clone: Path, name: str, message: str) -> None:
    (clone / "docs" / "registry" / name).write_text(f"id: {name}\n", encoding="utf-8")
    subprocess.run(list(FAKE_BUILD), cwd=clone, check=True)
    _git(clone, "add", "--", f"docs/registry/{name}", "docs/generated/STATUS.md")
    _git(clone, "commit", "-q", "-m", message)


@pytest.fixture
def clones(tmp_path):
    """(origin-side clone that pushes main, feature clone) sharing a bare origin."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(origin), str(seed))
    for clone in (seed,):
        _git(clone, "config", "user.email", "test@example.org")
        _git(clone, "config", "user.name", "Test")
        _git(clone, "config", "core.autocrlf", "false")
    (seed / ".gitattributes").write_text((REPO / ".gitattributes").read_text(encoding="utf-8"), encoding="utf-8")
    (seed / "docs" / "registry").mkdir(parents=True)
    (seed / "docs" / "generated").mkdir(parents=True)
    (seed / "code.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(seed, "add", "--", ".gitattributes", "code.py")
    _git(seed, "commit", "-q", "-m", "seed")
    _commit_record(seed, "a.yml", "record a")
    _git(seed, "push", "-q", "origin", "main")
    feature = tmp_path / "feature"
    _git(tmp_path, "clone", "-q", str(origin), str(feature))
    _git(feature, "config", "user.email", "test@example.org")
    _git(feature, "config", "user.name", "Test")
    _git(feature, "config", "core.autocrlf", "false")
    _git(feature, "switch", "-q", "-c", "feature")
    return seed, feature


def _diverge(seed: Path, feature: Path) -> None:
    _commit_record(seed, "b_main.yml", "main adds a record")
    _git(seed, "push", "-q", "origin", "main")
    _commit_record(feature, "c_feature.yml", "feature adds a record")


def test_without_the_driver_the_views_conflict(clones):
    # Baseline that motivates ADR-0165: a plain git merge stops on the counter line.
    seed, feature = clones
    _diverge(seed, feature)
    _git(feature, "fetch", "-q", "origin", "main")
    result = subprocess.run(["git", "merge", "--no-edit", "origin/main"], cwd=feature, capture_output=True, text=True)
    assert result.returncode != 0
    assert "docs/generated/STATUS.md" in _git(feature, "diff", "--name-only", "--diff-filter=U")


def test_sync_merges_and_the_rebuilt_view_is_the_union(clones):
    seed, feature = clones
    _diverge(seed, feature)
    assert tool.sync(feature, build_command=FAKE_BUILD, check_command=PASSING_CHECK) == 0
    status = (feature / "docs" / "generated" / "STATUS.md").read_text(encoding="utf-8")
    assert status == "records: 3\n- a.yml\n- b_main.yml\n- c_feature.yml\n"
    assert "<<<<<<<" not in status
    assert _git(feature, "status", "--porcelain") == ""
    parents = _git(feature, "rev-list", "--parents", "-n", "1", "HEAD").split()
    assert len(parents) == 3, "the merge and the rebuilt view land in one merge commit"
    assert _git(feature, "show", "HEAD:docs/generated/STATUS.md") + "\n" == status


def test_sync_registers_the_merge_driver(clones):
    _, feature = clones
    tool.ensure_merge_driver(feature)
    assert _git(feature, "config", "--get", "merge.generated-view.driver") == "true"


def test_sync_is_a_no_op_when_up_to_date(clones):
    _, feature = clones
    head = _git(feature, "rev-parse", "HEAD")
    assert tool.sync(feature, build_command=FAKE_BUILD, check_command=FAILING_CHECK) == 0
    assert _git(feature, "rev-parse", "HEAD") == head


def test_a_failing_check_fails_the_sync(clones):
    seed, feature = clones
    _diverge(seed, feature)
    assert tool.sync(feature, build_command=FAKE_BUILD, check_command=FAILING_CHECK) == 1


def test_other_conflicts_stop_and_finish_completes(clones):
    seed, feature = clones
    (seed / "code.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(seed, "commit", "-q", "-am", "main changes code")
    _diverge(seed, feature)
    (feature / "code.py").write_text("VALUE = 3\n", encoding="utf-8")
    _git(feature, "commit", "-q", "-am", "feature changes code")

    assert tool.sync(feature, build_command=FAKE_BUILD, check_command=PASSING_CHECK) == 2
    assert _git(feature, "diff", "--name-only", "--diff-filter=U") == "code.py"

    (feature / "code.py").write_text("VALUE = 3\n", encoding="utf-8")
    _git(feature, "add", "--", "code.py")
    assert tool.finish(feature, build_command=FAKE_BUILD, check_command=PASSING_CHECK) == 0
    assert (feature / "docs" / "generated" / "STATUS.md").read_text(encoding="utf-8").startswith("records: 3\n")
    assert _git(feature, "status", "--porcelain") == ""


def test_sync_refuses_a_dirty_tree(clones):
    _, feature = clones
    (feature / "code.py").write_text("VALUE = 9\n", encoding="utf-8")
    with pytest.raises(tool.SyncError, match="uncommitted changes"):
        tool.sync(feature, build_command=FAKE_BUILD, check_command=PASSING_CHECK)


def test_gitattributes_assigns_the_driver_and_lf_to_the_generated_views():
    attributes = _git(REPO, "check-attr", "merge", "eol", "--", "docs/generated/STATUS.md")
    assert "merge: generated-view" in attributes
    assert "eol: lf" in attributes
