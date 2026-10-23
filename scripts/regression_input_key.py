"""Print the regression input key of a commit: a hash of every tracked file a regression test can read.

    python scripts/regression_input_key.py [--repo-root <dir>] [--commit HEAD] [--verbose]

The CI workflow ``tests.yml`` stores a marker in the GitHub Actions cache under this key after a
green regression run and skips the run for a later commit with the same key (ADR-0165). The key is
the SHA-256 of ``git ls-tree -r <commit>`` without the paths under ``REGRESSION_EXCLUDED_PREFIXES``,
so a commit that changes only generated documentation views or ADR texts reuses the green result,
while any change to code, configs, tests, data tables, registries or run manifests runs the suite.

The exclusion is valid only while no regression-only test reads those paths; the guards in
``tests/test_regression_input_key.py`` enforce that the tests which do read them also run in the
metadata workflow ``docs.yml`` on every pull request. The key is computed from the commit, never
from the working tree, and blob ids do not depend on line-ending conversion, so Linux and Windows
compute the same key for the same commit.
"""
from __future__ import annotations

import argparse
import hashlib
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

#: Documentation-only paths that no regression-only test reads (ADR-0165): the generated views,
#: which documentation build writes from the registries, and the ADR texts. Run manifests and
#: registries are NOT excluded: regression tests read both.
REGRESSION_EXCLUDED_PREFIXES = ("docs/generated/", "docs/decisions/")


def _tree_entries(repo_root: Path, commit: str) -> list:
    output = subprocess.run(["git", "ls-tree", "-r", "--full-tree", commit], cwd=repo_root,
                            check=True, capture_output=True).stdout
    return [line for line in output.decode("utf-8", errors="surrogateescape").split("\n") if line]


def split_entries(entries: list) -> tuple:
    """Split ``git ls-tree`` lines into (included, excluded) by the path after the tab."""
    included, excluded = [], []
    for entry in entries:
        path = entry.split("\t", 1)[1]
        (excluded if path.startswith(REGRESSION_EXCLUDED_PREFIXES) else included).append(entry)
    return included, excluded


def regression_input_key(repo_root: Path = REPO, commit: str = "HEAD") -> str:
    included, _ = split_entries(_tree_entries(repo_root, commit))
    return hashlib.sha256("\n".join(included).encode("utf-8", errors="surrogateescape")).hexdigest()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--repo-root", default=str(REPO))
    parser.add_argument("--commit", default="HEAD")
    parser.add_argument("--verbose", action="store_true", help="report the included/excluded path counts on stderr")
    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root)
    if args.verbose:
        included, excluded = split_entries(_tree_entries(repo_root, args.commit))
        print(f"[regression_input_key] included {len(included)} path(s), excluded {len(excluded)} "
              f"documentation-only path(s) under {', '.join(REGRESSION_EXCLUDED_PREFIXES)}", file=sys.stderr)
    print(regression_input_key(repo_root, args.commit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
