#!/usr/bin/env bash
#
# run_pipeline.sh - activate the conda environment and run the synpp pipeline
# for a given config on the Linux run server.
#
# This is the server-side runner. Isolated runs at a given commit are started by
# scripts/isolated_run.py; it can also be called directly:
#
#   bash ~/eqasim-bs/scripts/run_pipeline.sh configs/fixtures/config_local_braunschweig_25pct.yml
#
# It writes a timestamped log next to the repo so a run can be followed with
#   tail -f ~/eqasim-bs/logs/run_*.log
#
# Assumptions:
#   - conda is installed at $CONDA_ROOT (default ~/miniforge3)
#   - the conda environment is named "eqasim"
#   - the working directory is the repository root
#
# All config arguments are forwarded to `python scripts/run_synpp.py`, which takes
# either one complete config or the composed form <base> <overlay> (config
# composition, #230; see configs/base_bs.yml):
#   bash scripts/run_pipeline.sh configs/base_bs.yml configs/overlays/test_25pct.yml
# The JDK override check below inspects every given file; the working and output
# directories are created by run_synpp.py (ensure_run_directories) from the resolved config.

set -euo pipefail

REPO_DIR="${EQASIM_REPO_DIR:-$HOME/eqasim-bs}"
CONDA_ENV="${EQASIM_CONDA_ENV:-eqasim}"
CONDA_ROOT="${CONDA_ROOT:-$HOME/miniforge3}"
REQUESTED_JAVA_HOME="${JAVA_HOME:-}"

# Default is a syntax placeholder only (its former Linux server counterpart,
# config_server_braunschweig_25pct.yml, was removed as superseded ballast, #230);
# ALWAYS pass an explicit config for a real run.
if [[ $# -gt 2 ]]; then
    echo "ERROR: at most two config paths are supported (<config> or <base> <overlay>)." >&2
    exit 1
fi
if [[ $# -eq 0 ]]; then
    set -- configs/fixtures/config_local_braunschweig_25pct.yml
fi
CONFIGS=("$@")

cd "$REPO_DIR"

for config_file in "${CONFIGS[@]}"; do
    if [[ ! -f "$config_file" ]]; then
        echo "ERROR: config '$config_file' not found in $REPO_DIR" >&2
        exit 1
    fi
done

# Activate conda in this non-interactive shell (conda activate is a shell
# function that only exists after sourcing conda.sh).
if [[ ! -f "$CONDA_ROOT/etc/profile.d/conda.sh" ]]; then
    echo "ERROR: conda not found at $CONDA_ROOT. Set CONDA_ROOT to your install." >&2
    exit 1
fi
# shellcheck disable=SC1091
source "$CONDA_ROOT/etc/profile.d/conda.sh"
conda activate "$CONDA_ENV"

# Preflight: the MATSim build uses the trusted server JDK 25. An explicitly
# configured JAVA_HOME takes precedence; otherwise use the server installation.
JAVA_HOME="${REQUESTED_JAVA_HOME:-$HOME/tools/jdk-25.0.3+9}"
java_binary="$JAVA_HOME/bin/java"
if [[ ! -x "$java_binary" ]]; then
    echo "ERROR: JDK 25 executable not found at '$java_binary'." >&2
    echo "       Set JAVA_HOME to an installed JDK 25, or install the trusted" >&2
    echo "       server JDK at '$HOME/tools/jdk-25.0.3+9'." >&2
    exit 1
fi

if ! java_version_output="$("$java_binary" -version 2>&1)"; then
    echo "ERROR: could not execute Java at '$java_binary'." >&2
    echo "       Set JAVA_HOME to a working JDK 25 installation." >&2
    exit 1
fi
java_version="${java_version_output%%$'\n'*}"
if [[ "$java_version" =~ \"([0-9]+)([.\"]|$) ]]; then
    java_major="${BASH_REMATCH[1]}"
else
    echo "ERROR: could not determine the Java version from: $java_version" >&2
    exit 1
fi
if [[ "$java_major" != "25" ]]; then
    echo "ERROR: run_pipeline.sh requires JDK 25; found major version $java_major" >&2
    echo "       at '$JAVA_HOME' ($java_version). Set JAVA_HOME to a JDK 25." >&2
    exit 1
fi

export JAVA_HOME
export PATH="$JAVA_HOME/bin:$PATH"
echo "==> Using JDK 25 at $JAVA_HOME"

# The Python runtime stages may override the shell selection through their
# config. Reject a conflicting override here, before Maven or MATSim starts.
if ! python - "$JAVA_HOME" "$java_binary" "${CONFIGS[@]}" <<'PY'
import os
import shutil
import sys

import yaml


selected_home, selected_binary, *config_paths = sys.argv[1:]


def real_path(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))


def check_config(config_path):
    with open(config_path, encoding="utf-8") as config_file:
        config = yaml.safe_load(config_file) or {}

    if not isinstance(config, dict):
        print(f"ERROR: config '{config_path}' must contain a mapping.", file=sys.stderr)
        raise SystemExit(1)

    runtime_config = config.get("config", {})
    if not isinstance(runtime_config, dict):
        print(f"ERROR: config '{config_path}' field 'config' must contain a mapping.",
              file=sys.stderr)
        raise SystemExit(1)

    configured_home = runtime_config.get("java_home")
    if configured_home and real_path(str(configured_home)) != real_path(selected_home):
        print(
            "ERROR: config java_home does not match the selected JDK 25: "
            f"{configured_home!r} != {selected_home!r}.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    configured_binary = runtime_config.get("java_binary")
    if configured_binary:
        resolved_binary = shutil.which(str(configured_binary))
        if resolved_binary is None:
            print(
                f"ERROR: config java_binary cannot be resolved: {configured_binary!r}.",
                file=sys.stderr,
            )
            raise SystemExit(1)
        if real_path(resolved_binary) != real_path(selected_binary):
            print(
                "ERROR: config java_binary does not match the selected JDK 25: "
                f"{configured_binary!r} resolves to {resolved_binary!r}, "
                f"not {selected_binary!r}.",
                file=sys.stderr,
            )
            raise SystemExit(1)


for path in config_paths:
    check_config(path)
PY
then
    exit 1
fi

if ! command -v mvn >/dev/null 2>&1; then
    echo "ERROR: Maven (mvn) not found on PATH. The eqasim MATSim jar is built with" >&2
    echo "       Maven. Install it with:" >&2
    echo "         sudo apt-get install -y maven" >&2
    exit 1
fi

mkdir -p logs

# Preflight: verify ALL required pipeline input data up front (issue #135).
# Data completeness was previously only discovered as a stage crash deep in
# the DAG, hours into a 64-core run. verify_braunschweig_inputs.py prints a
# checklist with download sources for anything missing and exits non-zero.
# Escape hatch: EQASIM_SKIP_VERIFY=1 skips the gate (e.g. for a deliberately
# partial data tree feeding only cached stages).
if [[ "${EQASIM_SKIP_VERIFY:-0}" != "1" ]]; then
    echo "==> Preflight: verifying pipeline input data (EQASIM_SKIP_VERIFY=1 to skip)"
    if ! PYTHONUTF8=1 python scripts/verify_braunschweig_inputs.py --matsim; then
        echo "ERROR: input verification failed. Fix the missing inputs above" >&2
        echo "       (download sources are listed per dataset), or re-run with" >&2
        echo "       EQASIM_SKIP_VERIFY=1 if the missing inputs are known to be" >&2
        echo "       served from cached stages." >&2
        exit 1
    fi
else
    echo "==> Preflight SKIPPED (EQASIM_SKIP_VERIFY=1)"
fi

# Timestamp is taken from the server clock at launch time for traceability.
log_file="logs/run_$(date +%Y%m%d_%H%M%S).log"

# PYTHONUTF8=1 avoids UnicodeEncodeError when stages print non-ASCII diagnostics
# (e.g. the IPF "max |delta| per margin" line) into a redirected/teed stream.
# run_synpp.py is a thin wrapper around `python -m synpp` that timestamps the log
# lines so per-stage runtimes can be extracted afterwards, and installs the
# deterministic stage-hash patch (ADR-0105) before synpp builds the stage graph.
# Sample machine CPU/RAM utilization while the pipeline runs (background). The
# runtime analysis below joins these samples to each stage to expose single-core
# bottlenecks (cores_busy ~ 1). Best-effort; never blocks the run.
samples_csv="${log_file%.log}_samples.csv"
bash scripts/sample_load.sh "$samples_csv" 15 &
SAMPLER_PID=$!
# `set -e` + pipefail exits on a failing run before the explicit kill below; the
# EXIT trap guarantees the sampler never outlives the script. A plain trap command
# does not change the exit status, so the run's own status is preserved.
trap 'kill "$SAMPLER_PID" 2>/dev/null || true' EXIT

echo "==> Running synpp on ${CONFIGS[*]} (env: $CONDA_ENV), logging to $log_file"
PYTHONUTF8=1 python scripts/run_synpp.py "${CONFIGS[@]}" 2>&1 | tee "$log_file"

# Stop the load sampler.
kill "$SAMPLER_PID" 2>/dev/null || true
trap - EXIT   # already stopped; do not signal the PID a second time

echo "==> Pipeline finished. Log: $REPO_DIR/$log_file"

# Per-stage runtime + utilization CSV (which stages dominated, and whether they
# ran on one core -> tune settings). Auto-detects <log>_samples.csv. Best-effort.
runtime_csv="${log_file%.log}_stage_runtime.csv"
PYTHONUTF8=1 python -m braunschweig.analysis.runtime --log "$log_file" \
    --output "$runtime_csv" || echo "WARNING: runtime analysis failed (non-fatal)"
