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

# Preflight: the MATSim part of the pipeline builds the eqasim jar with Maven and
# a JDK 21. Verify the toolchain up front and fail fast with actionable guidance,
# instead of crashing hours into the run (which is what happened before this
# check existed: missing Maven, then a JDK 17 that cannot target release 21).
#
# The eqasim-java sources are compiled for Java 21 (maven-compiler release 21),
# so an older JDK fails the build with "invalid target release: 21". Pin
# JAVA_HOME to a JDK 21 explicitly (first on PATH, after conda activation) so
# both the Maven build and the MATSim run use Java 21 regardless of the system
# default 'java'.
java21_home=$(ls -d /usr/lib/jvm/java-21-openjdk-* 2>/dev/null | head -1)
if [[ -z "$java21_home" ]]; then
    echo "ERROR: no JDK 21 found under /usr/lib/jvm. The eqasim-java sources target" >&2
    echo "       Java 21; an older JDK fails the Maven build with 'invalid target" >&2
    echo "       release: 21'. Install it with:" >&2
    echo "         sudo apt-get install -y openjdk-21-jdk" >&2
    exit 1
fi
export JAVA_HOME="$java21_home"
export PATH="$JAVA_HOME/bin:$PATH"
echo "==> Using JDK 21 at $JAVA_HOME"

if ! command -v mvn >/dev/null 2>&1; then
    echo "ERROR: Maven (mvn) not found on PATH. The eqasim MATSim jar is built with" >&2
    echo "       Maven. Install it with:" >&2
    echo "         sudo apt-get install -y maven" >&2
    exit 1
fi

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
# run_synpp.py is a thin wrapper around `python -m synpp` that timestamps the log
# lines so per-stage runtimes can be extracted afterwards.
echo "==> Running synpp on $CONFIG (env: $CONDA_ENV), logging to $log_file"
PYTHONUTF8=1 python scripts/run_synpp.py "$CONFIG" 2>&1 | tee "$log_file"

echo "==> Pipeline finished. Log: $REPO_DIR/$log_file"

# Per-stage runtime CSV (which stages dominated the run -> tune settings). Runs on
# the timestamped log; best-effort, never fails the run.
runtime_csv="${log_file%.log}_stage_runtime.csv"
PYTHONUTF8=1 python -m braunschweig.analysis.runtime --log "$log_file" \
    --output "$runtime_csv" || echo "WARNING: runtime analysis failed (non-fatal)"
