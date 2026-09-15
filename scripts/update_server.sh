#!/usr/bin/env bash
#
# update_server.sh - pull the latest eqasim-bs code on the Linux run server and
# preserve the captured production conda environment.
#
# Code is distributed via git (GitHub is the single source of truth), so this
# script never copies files by hand: it fast-forwards the local checkout and,
# never rebuilds an existing production environment from environment.yml. That
# file is the Windows source lock; Linux uses the captured server snapshot.
#
# The large raw input tree (eqasim-data/, gitignored) is NOT touched here - it
# is synced separately and rarely (see sync_data_to_server.ps1 on the Windows
# side), because it is static input data, not code.
#
# Usage (on the server, from anywhere):
#   bash ~/eqasim-bs/scripts/update_server.sh
#
# Assumptions:
#   - the repository lives at $REPO_DIR (default ~/eqasim-bs)
#   - the conda environment is named "eqasim"
#   - the environment was installed from environments/server-linux-64.lock

set -euo pipefail

REPO_DIR="${EQASIM_REPO_DIR:-$HOME/eqasim-bs}"
if [[ ! -d "$REPO_DIR/.git" ]]; then
    echo "ERROR: '$REPO_DIR' is not a git repository." >&2
    echo "Clone it first:  git clone <repo-url> '$REPO_DIR'" >&2
    exit 1
fi

cd "$REPO_DIR"

# Record the two canonical Linux snapshot artifacts before pulling. A changed
# snapshot requires an explicit fresh-environment installation; do not mutate a
# trusted production environment in place.
SERVER_CONDA_LOCK="environments/server-linux-64.lock"
SERVER_PIP_REQUIREMENTS="environments/requirements-server-linux-64.txt"
snapshot_hash() {
    if [[ -f "$1" ]]; then
        sha256sum "$1" | awk '{print $1}'
    else
        printf 'missing\n'
    fi
}
conda_lock_before="$(snapshot_hash "$SERVER_CONDA_LOCK")"
pip_requirements_before="$(snapshot_hash "$SERVER_PIP_REQUIREMENTS")"

echo "==> Fetching latest code on branch $(git rev-parse --abbrev-ref HEAD) ..."
git pull --ff-only

conda_lock_after="$(snapshot_hash "$SERVER_CONDA_LOCK")"
pip_requirements_after="$(snapshot_hash "$SERVER_PIP_REQUIREMENTS")"
snapshot_changed=0
if [[ "$conda_lock_before" != "$conda_lock_after" || "$pip_requirements_before" != "$pip_requirements_after" ]]; then
    echo "ERROR: the production Linux environment snapshot changed." >&2
    echo "       Existing environments are preserved; install a fresh environment" >&2
    echo "       from $SERVER_CONDA_LOCK and $SERVER_PIP_REQUIREMENTS." >&2
    snapshot_changed=1
else
    echo "==> Production Linux environment snapshot unchanged - existing environment left as is."
fi

# Keep the sibling eqasim-java-bs (our own editable Java project, built via
# eqasim_source_path=../eqasim-java-bs) in sync, so Java changes pushed to that repo
# are picked up and rebuilt on the next run.
JAVA_DIR="${EQASIM_JAVA_BS_DIR:-$HOME/eqasim-java-bs}"
if [[ -d "$JAVA_DIR/.git" ]]; then
    echo "==> Fetching latest eqasim-java-bs ..."
    git -C "$JAVA_DIR" pull --ff-only || echo "WARN: eqasim-java-bs pull failed (continuing)"
    echo "    eqasim-java-bs at: $(git -C "$JAVA_DIR" rev-parse --short HEAD)"
fi

echo "==> Done. Now at commit:"
git --no-pager log -1 --oneline

if [[ "$snapshot_changed" -ne 0 ]]; then
    exit 1
fi
