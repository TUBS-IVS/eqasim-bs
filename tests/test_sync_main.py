"""Tests for scripts/sync_main.py: merging origin/main and keeping the generated views at main's state.

Each test builds a throw-away origin + clone. The fake "documentation build" renders
docs/generated/STATUS.md from the record files under docs/registry/, exactly the shape that made
the real views conflict: both branches add a record, so both change the counter line. Since
ADR-0168 only main commits a rebuilt view (the generated-views workflow); the tests mimic that with
``_rebuild_on_main``. A branch that still commits its own view (from before ADR-0168) is built with
``_commit_record(..., with_view=True)``.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from scripts import check_generated_views_untouched as guard
from scripts import sync_main as tool

REPO = Path(__file__).resolve().parents[1]

FAKE_BUILD = (sys.executable, "-c",
              "from pathlib import Path; records = sorted(p.name for p in Path('docs/registry').glob('*.yml')); "
              "Path('docs/generated/STATUS.md').write_bytes(('records: %d\\n' % len(records) + "
              "''.join('- %s\\n' % r for r in records)).encode())")
PASSING_CHECK = (sys.executable, "-c", "raise SystemExit(0)")
FAILING_CHECK = (sys.executable, "-c", "raise SystemExit(1)")
VIEW = "docs/generated/STATUS.md"


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def _configure(clone: Path) -> None:
    _git(clone, "config", "user.email", "test@example.org")
    _git(clone, "config", "user.name", "Test")
    _git(clone, "config", "core.autocrlf", "false")


def _commit_record(clone: Path, name: str, message: str, with_view: bool = False) -> None:
    (clone / "docs" / "registry" / name).write_text(f"id: {name}\n", encoding="utf-8")
    paths = [f"docs/registry/{name}"]
    if with_view:
        subprocess.run(list(FAKE_BUILD), cwd=clone, check=True)
        paths.append(VIEW)
    _git(clone, "add", "--", *paths)
    _git(clone, "commit", "-q", "-m", message)


def _rebuild_on_main(seed: Path) -> None:
    """What the generated-views workflow does after a merge to main: rebuild, commit, push."""
    _git(seed, "pull", "-q", "--no-rebase", "origin", "main")
    subprocess.run(list(FAKE_BUILD), cwd=seed, check=True)
    _git(seed, "add", "--", VIEW)
    _git(seed, "commit", "-q", "-m", "rebuild the generated views")
    _git(seed, "push", "-q", "origin", "main")


def _view(clone: Path) -> str:
    return (clone / VIEW).read_text(encoding="utf-8")


@pytest.fixture
def clones(tmp_path):
    """(origin-side clone that pushes main, feature clone) sharing a bare origin."""
    origin = tmp_path / "origin.git"
    _git(tmp_path, "init", "-q", "--bare", "-b", "main", str(origin))
    seed = tmp_path / "seed"
    _git(tmp_path, "clone", "-q", str(origin), str(seed))
    _configure(seed)
    (seed / ".gitattributes").write_text((REPO / ".gitattributes").read_text(encoding="utf-8"), encoding="utf-8")
    (seed / "docs" / "registry").mkdir(parents=True)
    (seed / "docs" / "generated").mkdir(parents=True)
    (seed / "code.py").write_text("VALUE = 1\n", encoding="utf-8")
    _git(seed, "add", "--", ".gitattributes", "code.py")
    _git(seed, "commit", "-q", "-m", "seed")
    _commit_record(seed, "a.yml", "record a", with_view=True)
    _git(seed, "push", "-q", "origin", "main")
    feature = tmp_path / "feature"
    _git(tmp_path, "clone", "-q", str(origin), str(feature))
    _configure(feature)
    _git(feature, "switch", "-q", "-c", "feature")
    return seed, feature


def _diverge(seed: Path, feature: Path, feature_view: bool = False) -> None:
    _commit_record(seed, "b_main.yml", "main adds a record")
    _git(seed, "push", "-q", "origin", "main")
    _rebuild_on_main(seed)
    _commit_record(feature, "c_feature.yml", "feature adds a record", with_view=feature_view)


def test_without_the_driver_branches_that_commit_views_conflict(clones):
    # Baseline that motivates ADR-0165/0168: GitHub has no merge driver, and two sides that both
    # change the view stop on the counter line.
    seed, feature = clones
    _diverge(seed, feature, feature_view=True)
    _git(feature, "fetch", "-q", "origin", "main")
    result = subprocess.run(["git", "merge", "--no-edit", "origin/main"], cwd=feature, capture_output=True, text=True)
    assert result.returncode != 0
    assert VIEW in _git(feature, "diff", "--name-only", "--diff-filter=U")


def test_sync_merges_and_the_view_is_mains(clones):
    seed, feature = clones
    _diverge(seed, feature)
    assert tool.sync(feature, check_command=PASSING_CHECK) == 0
    assert _view(feature) == "records: 2\n- a.yml\n- b_main.yml\n"
    assert _git(feature, "status", "--porcelain") == ""
    parents = _git(feature, "rev-list", "--parents", "-n", "1", "HEAD").split()
    assert len(parents) == 3, "the merge lands as one merge commit"
    assert guard.changed_views(feature, "origin/main") == []


def test_sync_resets_a_view_the_branch_committed_before_adr_0168(clones):
    seed, feature = clones
    _diverge(seed, feature, feature_view=True)
    assert tool.sync(feature, check_command=PASSING_CHECK) == 0
    assert _view(feature) == "records: 2\n- a.yml\n- b_main.yml\n"
    assert "<<<<<<<" not in _view(feature)
    assert guard.changed_views(feature, "origin/main") == []


def test_sync_resets_an_own_view_also_when_up_to_date(clones):
    _, feature = clones
    _commit_record(feature, "c_feature.yml", "feature adds a record", with_view=True)
    assert guard.changed_views(feature, "origin/main") == [VIEW]
    assert tool.sync(feature, check_command=PASSING_CHECK) == 0
    assert guard.changed_views(feature, "origin/main") == []
    assert _git(feature, "log", "-1", "--format=%s").startswith("Reset the generated documentation views")


def test_sync_registers_the_merge_driver(clones):
    _, feature = clones
    tool.ensure_merge_driver(feature)
    assert _git(feature, "config", "--get", "merge.generated-view.driver") == "true"


def test_sync_is_a_no_op_when_up_to_date(clones):
    _, feature = clones
    head = _git(feature, "rev-parse", "HEAD")
    assert tool.sync(feature, check_command=FAILING_CHECK) == 0
    assert _git(feature, "rev-parse", "HEAD") == head


def test_a_failing_check_fails_the_sync(clones):
    seed, feature = clones
    _diverge(seed, feature)
    assert tool.sync(feature, check_command=FAILING_CHECK) == 1


def test_other_conflicts_stop_and_finish_completes(clones):
    seed, feature = clones
    (seed / "code.py").write_text("VALUE = 2\n", encoding="utf-8")
    _git(seed, "commit", "-q", "-am", "main changes code")
    _diverge(seed, feature, feature_view=True)
    (feature / "code.py").write_text("VALUE = 3\n", encoding="utf-8")
    _git(feature, "commit", "-q", "-am", "feature changes code")

    assert tool.sync(feature, check_command=PASSING_CHECK) == 2
    assert _git(feature, "diff", "--name-only", "--diff-filter=U") == "code.py"

    (feature / "code.py").write_text("VALUE = 3\n", encoding="utf-8")
    _git(feature, "add", "--", "code.py")
    assert tool.finish(feature, check_command=PASSING_CHECK) == 0
    assert _view(feature) == "records: 2\n- a.yml\n- b_main.yml\n"
    assert _git(feature, "status", "--porcelain") == ""
    assert guard.changed_views(feature, "origin/main") == []


def test_sync_refuses_a_dirty_tree(clones):
    _, feature = clones
    (feature / "code.py").write_text("VALUE = 9\n", encoding="utf-8")
    with pytest.raises(tool.SyncError, match="uncommitted changes"):
        tool.sync(feature, check_command=PASSING_CHECK)


def test_gitattributes_assigns_the_driver_and_lf_to_the_generated_views():
    attributes = _git(REPO, "check-attr", "merge", "eol", "--", VIEW)
    assert "merge: generated-view" in attributes
    assert "eol: lf" in attributes
