#!/usr/bin/env bash
#
# run_pipeline.sh - activate the conda environment and run the synpp pipeline
# for a given config on the Linux run server.
#
# This is the server-side runner invoked (inside a tmux session) by the Windows
# orchestrator run_pipeline_on_server.ps1. It can also be called directly:
#
#   bash ~/eqasim-bs/scripts/run_pipeline.sh config_server_braunschweig_25pct.yml
#
# It writes a timestamped log next to the repo so a run can be followed with
#   tail -f ~/eqasim-bs/logs/run_*.log
#
# Assumptions:
#   - conda is installed at $CONDA_ROOT (default ~/miniforge3)
#   - the conda environment is named "eqasim"
#   - the working directory is the repository root

set -euo pipefail

REPO_DIR="${EQASIM_REPO_DIR:-$HOME/eqasim-bs}"
CONDA_ENV="${EQASIM_CONDA_ENV:-eqasim}"
CONDA_ROOT="${CONDA_ROOT:-$HOME/miniforge3}"

CONFIG="${1:-config_server_braunschweig_25pct.yml}"

cd "$REPO_DIR"

if [[ ! -f "$CONFIG" ]]; then
    echo "ERROR: config '$CONFIG' not found in $REPO_DIR" >&2
    exit 1
fi

# Activate conda in this non-interactive shell (conda activate is a shell
# function that only exists after sourcing conda.sh).
if [[ ! -f "$CONDA_ROOT/etc/profile.d/conda.sh" ]]; then
    echo "ERROR: conda not found at $CONDA_ROOT. Set CONDA_ROOT to your install." >&2
    exit 1
fi
# shellcheck disable=SC1091
source "$CONDA_ROOT/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV"

mkdir -p logs

# synpp's output stage (synthesis/output.py validate()) requires the configured
# output directory to already exist and aborts the whole run otherwise. Extract
# output_path from the YAML config and create it up front so a fresh server
# checkout does not crash on a missing directory.
output_path=$(grep -E '^[[:space:]]*output_path:' "$CONFIG" \
    | head -1 \
    | sed -E 's/^[[:space:]]*output_path:[[:space:]]*//; s/[[:space:]]*$//' \
    | tr -d '"'"'"'')
if [[ -n "$output_path" ]]; then
    mkdir -p "$output_path"
    echo "==> Ensured output directory exists: $output_path"
fi

# Timestamp is taken from the server clock at launch time for traceability.
log_file="logs/run_$(date +%Y%m%d_%H%M%S).log"

# PYTHONUTF8=1 avoids UnicodeEncodeError when stages print non-ASCII diagnostics
# (e.g. the IPF "max |delta| per margin" line) into a redirected/teed stream.
echo "==> Running synpp on $CONFIG (env: $CONDA_ENV), logging to $log_file"
PYTHONUTF8=1 python -m synpp "$CONFIG" 2>&1 | tee "$log_file"

echo "==> Pipeline finished. Log: $REPO_DIR/$log_file"
