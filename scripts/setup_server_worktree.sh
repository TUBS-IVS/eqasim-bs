#!/usr/bin/env bash
#
# setup_server_worktree.sh - create (or repair) a git worktree on the Linux run
# server that safely shares the large, gitignored eqasim-data/ input tree via a
# symlink, without git ever destroying that symlink.
#
# WHY THIS EXISTS (structural trap, not a one-off):
#   The repo git-TRACKS ~91 small files under eqasim-data/ (DOWNLOAD_CHECKLISTs,
#   calibration/detour outputs, kba/derived, the target2026_* CSVs, bosserhof,
#   ...), while the bulk raw inputs (MiD raw, cells, regiostar, network, GTFS,
#   ~13 GB) are gitignored and only live in the shared real dir. To avoid copying
#   13 GB per worktree we replace eqasim-data/ with a symlink to the shared dir.
#   These two facts are INCOMPATIBLE: any working-tree-writing git op (checkout,
#   reset --hard, pull, merge) in such a worktree deletes the symlink and
#   recreates a real eqasim-data/ containing ONLY the 91 tracked files -- silently
#   severing every gitignored large input, so the next run crashes on the first
#   missing file (e.g. regiostar_referenzdatei.xlsx, MiD2023_Haushalte.csv).
#
# THE FIX (applied here): sparse-checkout with --no-cone, excluding /eqasim-data/,
#   so git NEVER materialises anything under that path. The symlink then survives
#   all future checkout/reset/pull/merge. The run reads BOTH the committed files
#   and the gitignored inputs through the symlink into the shared real dir.
#
# Keep the shared real dir current on main (git pull there) so its 91 committed
# files are fresh, and sync any freshly-committed targets into it.
#
# HARD RULE (this script cannot enforce it for you): NEVER run a git command in a
#   worktree that has a LIVE run -- a checkout/reset mid-run would sever the
#   symlink and crash the pipeline, which reads inputs on demand throughout both
#   popsim and matsim.
#
# Usage (on the server):
#   bash ~/eqasim-bs/scripts/setup_server_worktree.sh <worktree-path> [git-ref]
#
# Examples:
#   bash ~/eqasim-bs/scripts/setup_server_worktree.sh ~/wt/feature-x origin/feature-x
#   bash ~/eqasim-bs/scripts/setup_server_worktree.sh ~/wt/main-rerun main
#
# Arguments:
#   worktree-path  where to create the worktree (required)
#   git-ref        branch/tag/commit to check out (default: origin/main)
#
# Environment overrides:
#   EQASIM_REPO_DIR       main checkout to add the worktree from (default ~/eqasim-bs)
#   EQASIM_SHARED_DATA    shared real eqasim-data dir the symlink points at
#                         (default $EQASIM_REPO_DIR/eqasim-data)

set -euo pipefail

REPO_DIR="${EQASIM_REPO_DIR:-$HOME/eqasim-bs}"
SHARED_DATA="${EQASIM_SHARED_DATA:-$REPO_DIR/eqasim-data}"

WORKTREE_PATH="${1:-}"
GIT_REF="${2:-origin/main}"

if [[ -z "$WORKTREE_PATH" ]]; then
    echo "ERROR: worktree path is required." >&2
    echo "Usage: bash $0 <worktree-path> [git-ref]" >&2
    exit 1
fi

if [[ ! -d "$REPO_DIR/.git" ]]; then
    echo "ERROR: '$REPO_DIR' is not a git repository (set EQASIM_REPO_DIR)." >&2
    exit 1
fi

# The symlink target must be a real directory that actually holds the inputs, and
# it must NOT be inside the worktree itself (that would be self-referential once
# git removes it). Fail early with a clear message rather than producing a broken
# worktree that crashes mid-run.
if [[ ! -d "$SHARED_DATA" ]]; then
    echo "ERROR: shared data dir '$SHARED_DATA' does not exist (set EQASIM_SHARED_DATA)." >&2
    exit 1
fi
SHARED_DATA="$(cd "$SHARED_DATA" && pwd -P)"

echo "==> Repo:        $REPO_DIR"
echo "==> Worktree:    $WORKTREE_PATH"
echo "==> Ref:         $GIT_REF"
echo "==> Shared data: $SHARED_DATA (symlink target)"

# --- 1. Create the worktree if it does not exist yet -----------------------
# git worktree add fails if the path already exists, so treat an existing
# worktree as "repair" (re-apply sparse-checkout + symlink) rather than an error.
if [[ -d "$WORKTREE_PATH" ]]; then
    echo "==> Worktree path already exists - repairing sparse-checkout + symlink only."
    if [[ ! -e "$WORKTREE_PATH/.git" ]]; then
        echo "ERROR: '$WORKTREE_PATH' exists but is not a git worktree." >&2
        exit 1
    fi
else
    echo "==> Creating worktree ..."
    git -C "$REPO_DIR" worktree add --detach "$WORKTREE_PATH" "$GIT_REF"
fi

# --- 2. Exclude eqasim-data/ from the working tree via sparse-checkout ------
# --no-cone lets us use gitignore-style negative patterns. '/*' includes every
# top-level entry; '!/eqasim-data/*' then excludes everything under eqasim-data/
# (the negation must target the directory *contents* with the trailing '/*';
# '!/eqasim-data/' alone does NOT exclude anything). With no included path under
# it, git leaves eqasim-data/ absent, so git never writes a tracked file there
# and the symlink we create below survives all future checkout/reset/pull/merge.
echo "==> Configuring sparse-checkout to exclude eqasim-data/ ..."
git -C "$WORKTREE_PATH" sparse-checkout init --no-cone
git -C "$WORKTREE_PATH" sparse-checkout set '/*' '!/eqasim-data/*'

# --- 3. Replace eqasim-data/ with the symlink to the shared real dir --------
# After sparse-checkout, eqasim-data/ should be absent; remove any residue
# (a stale real dir or an old/broken symlink) before creating the fresh symlink.
LINK="$WORKTREE_PATH/eqasim-data"
if [[ -L "$LINK" ]]; then
    rm -f "$LINK"
elif [[ -d "$LINK" ]]; then
    # A real directory here means git materialised the tracked files despite
    # sparse-checkout (older git, or a pre-existing worktree). It only ever holds
    # committed files (the gitignored inputs live in the shared dir), so removing
    # it loses nothing that is not also in the shared dir.
    echo "==> Removing residual real eqasim-data/ directory before symlinking ..."
    rm -rf "$LINK"
fi
ln -s "$SHARED_DATA" "$LINK"

# --- 4. Verify the symlink resolves and a known input is reachable ----------
if [[ ! -d "$LINK/" ]]; then
    echo "ERROR: symlink '$LINK' does not resolve to a directory." >&2
    exit 1
fi
echo "==> Symlink OK: eqasim-data -> $(readlink "$LINK")"

echo "==> Done. Worktree is ready:"
echo "      cd '$WORKTREE_PATH'"
echo ""
echo "    REMINDER: never run a git command in this worktree while a run is live."
