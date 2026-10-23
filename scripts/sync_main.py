"""Merge origin/main into the current branch and leave the documentation views at main's state.

    python scripts/sync_main.py [--remote origin] [--branch main] [--no-fetch] [--no-dag]
    python scripts/sync_main.py --finish [--no-dag]      # after resolving other conflicts by hand
    git sync-main                                        # alias, see docs/codebase/notes/merging-main.md

The generated views ``docs/generated/*.md`` are aggregations of the registries, so two branches
that each add a record change the same lines (STATUS.md's counter line, the last row of
DECISIONS.md) without any real disagreement (ADR-0165). Since ADR-0168 only main commits the
views: the generated-views workflow rebuilds them after each merge, and a branch keeps them exactly
at main's state, so neither git nor GitHub sees a conflict there. This script merges without
committing, sets every view to the merged main's version (``git restore --source=MERGE_HEAD``),
commits the merge and runs ``documentation check``. When the branch is already up to date it
still resets views that the branch itself changed (a branch from before ADR-0168).

``.gitattributes`` keeps the merge driver ``generated-view`` (keep the current side, no conflict
markers) so that a branch from before ADR-0168 merges without markers; the restore then replaces
the kept side. A merge that leaves a conflict outside the generated views stops with the list of
files (exit 2); resolve and stage them, then run ``--finish``, which restores, commits and checks
the same way.

Exit codes: 0 merged (or already up to date) and the documentation check passed; 1 error or a
failed check; 2 conflicts outside the generated views wait for a manual resolution.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from scripts.regression_input_key import regression_input_key  # noqa: E402

GENERATED_DIRECTORY = "docs/generated"
#: Name of the merge driver that .gitattributes assigns to docs/generated/*.md. "true" exits 0 and
#: leaves the current branch's file in place; the restore from main below replaces it.
MERGE_DRIVER = "generated-view"
MERGE_DRIVER_CONFIG = {
    f"merge.{MERGE_DRIVER}.name": "keep ours; scripts/sync_main.py restores docs/generated from main",
    f"merge.{MERGE_DRIVER}.driver": "true",
}
CHECK_COMMAND = (sys.executable, "-m", "braunschweig.documentation", "check", "--quiet")


class SyncError(RuntimeError):
    pass


def _git(repo: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if check and result.returncode != 0:
        raise SyncError(f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}")
    return result


def _log(message: str) -> None:
    print(f"[sync_main] {message}", flush=True)


def ensure_merge_driver(repo: Path) -> None:
    """Register the generated-view merge driver in the clone's config (shared by all worktrees)."""
    for key, value in MERGE_DRIVER_CONFIG.items():
        if _git(repo, "config", "--get", key, check=False).stdout.strip() != value:
            _git(repo, "config", key, value)
            _log(f"registered {key} = {value!r}")


def _merge_in_progress(repo: Path) -> bool:
    return _git(repo, "rev-parse", "-q", "--verify", "MERGE_HEAD", check=False).returncode == 0


def _unmerged_paths(repo: Path, *pathspec: str) -> list:
    output = _git(repo, "diff", "--name-only", "--diff-filter=U", "--", *pathspec).stdout
    return [p for p in output.splitlines() if p]


def _tracked_changes(repo: Path) -> list:
    return [p for p in _git(repo, "status", "--porcelain", "--untracked-files=no").stdout.splitlines() if p]


def _run(command, repo: Path, label: str) -> int:
    _log(f"{label}: {' '.join(command)}")
    return subprocess.run(list(command), cwd=repo).returncode


def restore_views(repo: Path, source: str) -> list:
    """Set docs/generated/ in the index and working tree to ``source``; return the paths that differ from HEAD.

    ``git restore`` also removes a view that ``source`` does not have, so the branch's views end up
    identical to main's.
    """
    _git(repo, "restore", f"--source={source}", "--staged", "--worktree", "--", GENERATED_DIRECTORY)
    return [p for p in _git(repo, "diff", "--cached", "--name-only", "HEAD", "--",
                            GENERATED_DIRECTORY).stdout.splitlines() if p]


def restore_and_commit(repo: Path, source: str) -> list:
    """Restore the views from ``source`` and commit the pending merge (or the reset alone)."""
    other = [p for p in _unmerged_paths(repo) if not p.startswith(f"{GENERATED_DIRECTORY}/")]
    if other:
        raise SyncError(f"unresolved conflicts remain: {other}")
    changed = restore_views(repo, source)
    _log(f"{len(changed)} generated view(s) set to main's state: {', '.join(changed) or '-'}")
    if _merge_in_progress(repo):
        _git(repo, "commit", "--no-edit")
        _log(f"committed the merge as {_git(repo, 'rev-parse', '--short', 'HEAD').stdout.strip()}")
    elif changed:
        _git(repo, "commit", "-m", "Reset the generated documentation views to main's state (ADR-0168)")
        _log("committed the reset of the generated views")
    return changed


def report_regression_key(repo: Path, before: str) -> None:
    """Say whether CI can reuse a green regression result of the pre-merge commit (ADR-0165)."""
    if regression_input_key(repo, before) == regression_input_key(repo, "HEAD"):
        _log("regression inputs unchanged by the merge: CI reuses a green regression result of the "
             "pre-merge commit, if it had one")
    else:
        _log("regression inputs changed: CI runs the full regression suite on both platforms")


def sync(repo: Path, remote: str = "origin", branch: str = "main", fetch: bool = True,
         check_command=CHECK_COMMAND) -> int:
    ensure_merge_driver(repo)
    if _merge_in_progress(repo):
        raise SyncError("a merge is already in progress; resolve it and run --finish")
    dirty = _tracked_changes(repo)
    if dirty:
        raise SyncError(f"the working tree has uncommitted changes; commit them first: {dirty}")
    if fetch:
        _git(repo, "fetch", remote, branch)
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    target = f"{remote}/{branch}"
    merge = _git(repo, "merge", "--no-commit", "--no-ff", target, check=False)
    if not _merge_in_progress(repo):
        if merge.returncode != 0:
            raise SyncError(f"git merge {target} failed: {merge.stderr.strip() or merge.stdout.strip()}")
        _log(f"already up to date with {target}")
        if restore_and_commit(repo, target):
            return 0 if _run(check_command, repo, "documentation check") == 0 else 1
        return 0
    conflicts = [p for p in _unmerged_paths(repo) if not p.startswith(f"{GENERATED_DIRECTORY}/")]
    if conflicts:
        _log(f"conflicts outside the generated views need a manual resolution ({len(conflicts)}):")
        for path in conflicts:
            print(f"  {path}")
        _log("resolve them, `git add` the files, then run: python scripts/sync_main.py --finish")
        return 2
    brought = [p for p in _git(repo, "diff", "--name-only", before, "MERGE_HEAD").stdout.splitlines() if p]
    _log(f"merged {target}: {len(brought)} path(s) differ from the branch")
    restore_and_commit(repo, "MERGE_HEAD")
    report_regression_key(repo, before)
    return 0 if _run(check_command, repo, "documentation check") == 0 else 1


def finish(repo: Path, check_command=CHECK_COMMAND) -> int:
    if not _merge_in_progress(repo):
        raise SyncError("no merge in progress; --finish completes a merge that sync_main stopped")
    before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    restore_and_commit(repo, "MERGE_HEAD")
    report_regression_key(repo, before)
    return 0 if _run(check_command, repo, "documentation check") == 0 else 1


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--remote", default="origin")
    parser.add_argument("--branch", default="main")
    parser.add_argument("--no-fetch", action="store_true", help="merge the remote-tracking branch as it is")
    parser.add_argument("--no-dag", action="store_true", help="pass --no-dag to documentation check")
    parser.add_argument("--finish", action="store_true", help="complete a merge stopped on other conflicts")
    parser.add_argument("--repo-root", default=None, help="repository to operate on (default: current directory)")
    args = parser.parse_args(argv)
    repo = Path(args.repo_root) if args.repo_root else Path.cwd()
    check_command = CHECK_COMMAND + (("--no-dag",) if args.no_dag else ())
    try:
        if args.finish:
            return finish(repo, check_command=check_command)
        return sync(repo, args.remote, args.branch, fetch=not args.no_fetch, check_command=check_command)
    except SyncError as error:
        print(f"[sync_main] error: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
