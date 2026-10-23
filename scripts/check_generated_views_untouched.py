"""Fail a pull request that changes the generated documentation views (ADR-0168).

    python scripts/check_generated_views_untouched.py --base <base commit or ref> [--head HEAD]

The views ``docs/generated/*.md`` are aggregations of the registries; two branches that each add
a record change the same lines (STATUS.md's counter line, the last row of DECISIONS.md). GitHub's
mergeability check and merge button ignore the local merge driver of ADR-0165, so such branches
showed as CONFLICTING on GitHub after every sibling merge. Since ADR-0168 only main commits the
views: the generated-views workflow rebuilds them after each merge. A branch leaves them exactly at
main's state, so it has no diff there and cannot conflict there.

This guard compares ``docs/generated/`` between the merge base of ``--base`` and ``--head`` and
``--head`` itself, i.e. the change the pull request would bring into main. It lists every changed
view and exits 1; ``git sync-main`` (``scripts/sync_main.py``) resets the views to main's state.

Exit codes: 0 no generated view changed; 1 at least one changed (or a git error).
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

GENERATED_DIRECTORY = "docs/generated"
FIX_HINT = ("bring main in with `git sync-main` (it resets docs/generated/ to main's state), or run "
            "`git checkout <merge base> -- docs/generated` and commit; never commit a rebuilt view (ADR-0168)")


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed ({result.returncode}): {result.stderr.strip()}")
    return result.stdout


def changed_views(repo: Path, base: str, head: str = "HEAD") -> list:
    """Paths under docs/generated/ that ``head`` changes relative to its merge base with ``base``."""
    merge_base = _git(repo, "merge-base", base, head).strip()
    output = _git(repo, "diff", "--name-only", "--no-renames", merge_base, head, "--", GENERATED_DIRECTORY)
    return [path for path in output.splitlines() if path]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", required=True, help="the pull request's base (commit or ref, e.g. origin/main)")
    parser.add_argument("--head", default="HEAD", help="the commit to check (default: HEAD)")
    parser.add_argument("--repo-root", default=None, help="repository to operate on (default: current directory)")
    args = parser.parse_args(argv)
    repo = Path(args.repo_root) if args.repo_root else Path.cwd()
    try:
        changed = changed_views(repo, args.base, args.head)
    except RuntimeError as error:
        print(f"[generated-views] error: {error}", file=sys.stderr)
        return 1
    if not changed:
        print(f"[generated-views] OK: {args.head} leaves {GENERATED_DIRECTORY}/ at the state of {args.base}")
        return 0
    print(f"[generated-views] FAIL: the change touches {len(changed)} generated view(s); only main's "
          f"generated-views workflow commits them:")
    for path in changed:
        print(f"  {path}")
    print(f"[generated-views] fix: {FIX_HINT}")
    return 1


if __name__ == "__main__":
    sys.exit(main())
