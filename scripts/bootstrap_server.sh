#!/usr/bin/env bash
#
# bootstrap_server.sh - one-time first-run setup on the Linux run server:
# clone the repository and create the conda environment. Idempotent (safe to
# re-run: pulls if already cloned and preserves an existing server environment).
#
# It deliberately does NOT:
#   - install system packages: git, osmosis, osmconvert and the MATSim Java
#     toolchain need sudo. Run first:
#       sudo apt-get install -y git osmosis osmctools maven
#     (osmctools provides /usr/bin/osmconvert; maven builds the eqasim MATSim
#      jar and needs a full JDK with javac. Install JDK 25 separately using the
#      server's approved distribution; do not assume an openjdk-25 package is
#      available from the operating system repository. A plain JRE is not
#      sufficient for the Maven build stage.)
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
REPAIR_PIP=0

case "$#" in
    0)
        ;;
    1)
        if [[ "$1" == "--repair-pip" ]]; then
            REPAIR_PIP=1
        else
            echo "ERROR: unknown option '$1'. Use --repair-pip or no option." >&2
            exit 2
        fi
        ;;
    *)
        echo "ERROR: expected no option or --repair-pip." >&2
        exit 2
        ;;
esac

if ! command -v git >/dev/null 2>&1; then
    echo "ERROR: git is not installed. Run this first (needs sudo):" >&2
    echo "  sudo apt-get install -y git osmosis osmctools maven" >&2
    echo "Also install JDK 25 separately using the server's approved distribution." >&2
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

# --- Conda environment: create the captured production snapshot ------------
if [[ ! -f "$CONDA_ROOT/etc/profile.d/conda.sh" ]]; then
    echo "ERROR: conda not found at $CONDA_ROOT. Set CONDA_ROOT to your install." >&2
    exit 1
fi
# shellcheck disable=SC1091
source "$CONDA_ROOT/etc/profile.d/conda.sh"

SERVER_CONDA_LOCK="$REPO_DIR/environments/server-linux-64.lock"
SERVER_PIP_REQUIREMENTS="$REPO_DIR/environments/requirements-server-linux-64.txt"
if [[ ! -f "$SERVER_CONDA_LOCK" || ! -f "$SERVER_PIP_REQUIREMENTS" ]]; then
    echo "ERROR: production Linux environment snapshot is incomplete after checkout:" >&2
    echo "  expected $SERVER_CONDA_LOCK and $SERVER_PIP_REQUIREMENTS" >&2
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
    echo "==> conda env '$CONDA_ENV' already exists - preserving it."
    echo "    It is not updated from environment.yml; that file is the Windows source lock."
    if [[ "$REPAIR_PIP" -eq 1 ]]; then
        echo "==> Replaying the captured pip layer by explicit request ..."
        if ! conda run -n "$CONDA_ENV" python -m pip install --no-deps -r "$SERVER_PIP_REQUIREMENTS"; then
            echo "ERROR: pip repair failed; the existing environment was not deleted." >&2
            exit 1
        fi
    fi
else
    echo "==> Creating conda env '$CONDA_ENV' from the production Linux snapshot ..."
    "$CREATE" create -y -n "$CONDA_ENV" --file "$SERVER_CONDA_LOCK"
    echo "==> Installing the captured pip layer without dependency resolution ..."
    if ! conda run -n "$CONDA_ENV" python -m pip install --no-deps -r "$SERVER_PIP_REQUIREMENTS"; then
        echo "ERROR: initial pip installation failed; the newly created environment was preserved." >&2
        echo "       After fixing access to the staged artifacts, run:" >&2
        echo "       bash $0 --repair-pip" >&2
        exit 1
    fi
fi

echo "==> Verifying effective dependency consistency ..."
conda run -n "$CONDA_ENV" python -m pip check

echo ""
echo "==> Bootstrap complete."
echo "    Repo : $REPO_DIR ($(git -C "$REPO_DIR" rev-parse --short HEAD))"
echo "    Env  : $CONDA_ENV"
echo "    Next : sync data (scripts/sync_data_to_server.ps1), then start the run."
