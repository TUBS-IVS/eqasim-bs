#!/usr/bin/env bash
#
# bootstrap_server.sh - one-time first-run setup on the Linux run server:
# clone the repository and create the conda environment. Idempotent (safe to
# re-run: pulls if already cloned, updates the env if it already exists).
#
# It deliberately does NOT:
#   - install system packages: git, osmosis and osmconvert need sudo. Run first:
#       sudo apt-get install -y git osmosis osmctools
#     (osmctools provides /usr/bin/osmconvert; osmosis needs Java, already present)
#   - start the pipeline (that is run_pipeline.sh / run_pipeline_on_server.ps1)
#
# Run it by piping this file to the server's bash over SSH (no checkout needed
# yet, since this is what does the checkout):
#   Get-Content scripts/bootstrap_server.sh -Raw | ssh felix@<host> bash -s
# or, once the repo exists, directly on the server:
#   bash ~/eqasim-bs/scripts/bootstrap_server.sh

set -euo pipefail

REPO_URL="${EQASIM_REPO_URL:-https://github.com/TUBS-IVS/eqasim-bs.git}"
REPO_DIR="${EQASIM_REPO_DIR:-$HOME/eqasim-bs}"
CONDA_ROOT="${CONDA_ROOT:-$HOME/miniforge3}"
CONDA_ENV="${EQASIM_CONDA_ENV:-eqasim}"

if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git is not installed. Run this first (needs sudo):" >&2
    echo "  sudo apt-get install -y git osmosis osmctools" >&2
    exit 1
fi

# --- Code: clone or fast-forward -------------------------------------------
if [[ -d "$REPO_DIR/.git" ]]; then
    echo "==> Repository already at $REPO_DIR - pulling latest ..."
    git -C "$REPO_DIR" pull --ff-only
else
    echo "==> Cloning $REPO_URL -> $REPO_DIR ..."
    git clone "$REPO_URL" "$REPO_DIR"
fi

# The raw input tree is gitignored and synced separately; make sure the target
# directory exists so rsync (3.1.x, no --mkpath) can write into it.
mkdir -p "$REPO_DIR/eqasim-data/data"

# --- Conda environment: create or update -----------------------------------
if [[ ! -f "$CONDA_ROOT/etc/profile.d/conda.sh" ]]; then
    echo "ERROR: conda not found at $CONDA_ROOT. Set CONDA_ROOT to your install." >&2
    exit 1
fi
# shellcheck disable=SC1091
source "$CONDA_ROOT/etc/profile.d/conda.sh"

ENV_FILE="$REPO_DIR/environment.yml"
if [[ ! -f "$ENV_FILE" ]]; then
    echo "ERROR: $ENV_FILE not found after checkout." >&2
    exit 1
fi

# Prefer mamba (much faster solver) when available; conda 25.x already uses the
# libmamba solver by default, so plain conda is acceptable too.
if command -v mamba >/dev/null 2>&1; then
    CREATE="mamba"
else
    CREATE="conda"
fi

if conda env list | awk '{print $1}' | grep -qx "$CONDA_ENV"; then
    echo "==> conda env '$CONDA_ENV' exists - updating from environment.yml ..."
    "$CREATE" env update -n "$CONDA_ENV" -f "$ENV_FILE" --prune
else
    echo "==> Creating conda env '$CONDA_ENV' from environment.yml (this takes a while) ..."
    "$CREATE" env create -n "$CONDA_ENV" -f "$ENV_FILE"
fi

echo ""
echo "==> Bootstrap complete."
echo "    Repo : $REPO_DIR ($(git -C "$REPO_DIR" rev-parse --short HEAD))"
echo "    Env  : $CONDA_ENV"
echo "    Next : sync data (scripts/sync_data_to_server.ps1), then start the run."
