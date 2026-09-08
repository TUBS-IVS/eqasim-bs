"""Pin the root-scratch .gitignore rules.

Ad-hoc diagnostics, pickle probes, one-off exports and launch helpers accumulate at the
root of long-lived server checkouts. On 2026-09-08 the shared felix checkout held 17 such
files that were untracked but NOT ignored, i.e. one careless ``git add -A`` away from the
history of a citable scientific repository.

The rules are deliberately ROOT-ANCHORED, and that is the half worth pinning: the
repository legitimately tracks 31 ``scripts/extract_*.py`` and 3 ``scripts/inspect_*.py``
files, so an unanchored pattern would hide real tooling and any future sibling of it. Both
directions are asserted below, because a rule that ignores too much fails as badly as one
that ignores too little.

``git check-ignore`` is queried rather than the filesystem, so the test creates no files
and passes on a clean checkout. It is queried with ``--no-index``, which matters: without
that flag git reports every TRACKED path as not-ignored regardless of the rules, so the
visibility assertions on real committed tooling could never fail. That was measured -- with
the leading slashes stripped, the flagless form failed 3 of 9 visibility cases while the
``--no-index`` form fails all 9.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

# Dropped at the repository root, these must never be stageable.
IGNORED_AT_ROOT = (
    "scratch/whatever.py",
    "scratch/nested/deeper/probe.pkl",
    "diag_employed.py",
    "diag_seed_filter_bias.py",
    "export_full_allfeatures.py",
    "extract_emp.py",
    "inspect_popsim_pickle.py",
    "launch_popsim100.sh",
    "launch_experiment.py",
    "mkcfg_experiments.py",
)

# These must stay visible: real committed tooling under scripts/, plausible future
# siblings of it, a tracked root script, and an ordinary module.
MUST_STAY_VISIBLE = (
    "scripts/extract_bbs_share_by_age.py",
    "scripts/extract_kba_fleet.py",
    "scripts/inspect_hh_gap.py",
    "scripts/inspect_mid_p13.py",
    "scripts/export_data_source_inventory.py",
    "scripts/launch_something.sh",
    "scripts/diag_helper.py",
    "make_smoke_configs.py",
    "braunschweig/popsim/trips.py",
)

# Real committed paths among the examples above, asserted to exist so the visibility
# cases protect actual tooling rather than hypothetical files.
REAL_TRACKED_EXAMPLES = (
    "scripts/extract_bbs_share_by_age.py",
    "scripts/extract_kba_fleet.py",
    "scripts/inspect_hh_gap.py",
    "scripts/inspect_mid_p13.py",
    "make_smoke_configs.py",
)


def _is_ignored(relative_path: str) -> bool:
    """Ask git whether the ignore RULES cover that path. The path need not exist.

    ``--no-index`` evaluates the rules alone. Without it a tracked path always reports
    not-ignored, which would make every assertion about committed tooling vacuous.
    """
    result = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", relative_path],
        cwd=REPO_ROOT, capture_output=True,
    )
    if result.returncode not in (0, 1):
        raise RuntimeError(
            f"git check-ignore failed for {relative_path!r} with code "
            f"{result.returncode}: {result.stderr.decode(errors='replace')}")
    return result.returncode == 0


@pytest.mark.parametrize("relative_path", IGNORED_AT_ROOT)
def test_root_scratch_paths_are_ignored(relative_path: str) -> None:
    assert _is_ignored(relative_path), (
        f"{relative_path} is NOT ignored, so a `git add -A` at the repository root would "
        f"stage it. Add or restore the root-anchored rule in .gitignore.")


@pytest.mark.parametrize("relative_path", MUST_STAY_VISIBLE)
def test_committed_tooling_stays_visible(relative_path: str) -> None:
    assert not _is_ignored(relative_path), (
        f"{relative_path} IS ignored. The root-scratch rules must stay anchored with a "
        f"leading slash; an unanchored pattern hides real tooling under scripts/.")


@pytest.mark.parametrize("relative_path", REAL_TRACKED_EXAMPLES)
def test_the_named_examples_really_exist(relative_path: str) -> None:
    """Guard against protecting fictional files.

    If a named example is ever renamed away, the visibility assertion for it would still
    pass while proving nothing, so its presence in the index is asserted explicitly.
    """
    listed = subprocess.run(
        ["git", "ls-files", "--error-unmatch", relative_path],
        cwd=REPO_ROOT, capture_output=True, text=True,
    )
    assert listed.returncode == 0, (
        f"{relative_path} is no longer tracked, so the visibility case naming it proves "
        f"nothing. Point it at a file that exists, or drop it.")
