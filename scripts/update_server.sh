#!/usr/bin/env bash
#
# update_server.sh - pull the latest eqasim-bs code on the Linux run server and
# keep the conda environment in sync.
#
# Code is distributed via git (GitHub is the single source of truth), so this
# script never copies files by hand: it fast-forwards the local checkout and,
# only if environment.yml actually changed, updates the conda environment.
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
#   - conda is initialised for the current shell

set -euo pipefail

REPO_DIR="${EQASIM_REPO_DIR:-$HOME/eqasim-bs}"
CONDA_ENV="${EQASIM_CONDA_ENV:-eqasim}"

if [[ ! -d "$REPO_DIR/.git" ]]; then
    echo "ERROR: '$REPO_DIR' is not a git repository." >&2
    echo "Clone it first:  git clone <repo-url> '$REPO_DIR'" >&2
    exit 1
fi

cd "$REPO_DIR"

# Record the environment.yml hash before pulling so we can detect dependency
# changes that require a conda env update (recreating the env on every pull
# would be slow and unnecessary).
ENV_FILE="environment.yml"
hash_before=""
if [[ -f "$ENV_FILE" ]]; then
    hash_before="$(sha1sum "$ENV_FILE" | awk '{print $1}')"
fi

echo "==> Fetching latest code on branch $(git rev-parse --abbrev-ref HEAD) ..."
git pull --ff-only

hash_after=""
if [[ -f "$ENV_FILE" ]]; then
    hash_after="$(sha1sum "$ENV_FILE" | awk '{print $1}')"
fi

if [[ "$hash_before" != "$hash_after" ]]; then
    echo "==> $ENV_FILE changed - updating conda environment '$CONDA_ENV' ..."
    # conda is a shell function that only exists after sourcing conda.sh; this
    # script runs in a non-interactive SSH shell where conda is not initialised,
    # so 'conda env update' would fail with 'conda: command not found'.
    CONDA_ROOT="${CONDA_ROOT:-$HOME/miniforge3}"
    if [[ ! -f "$CONDA_ROOT/etc/profile.d/conda.sh" ]]; then
        echo "ERROR: conda not found at $CONDA_ROOT. Set CONDA_ROOT to your install." >&2
        exit 1
    fi
    # shellcheck disable=SC1091
    source "$CONDA_ROOT/etc/profile.d/conda.sh"
    # 'conda env update --prune' adds new and removes dropped dependencies.
    conda env update -n "$CONDA_ENV" -f "$ENV_FILE" --prune
else
    echo "==> $ENV_FILE unchanged - conda environment '$CONDA_ENV' left as is."
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
