#!/usr/bin/env bash
# Run PopulationSim on three prepared batches with the environments of both arms (popsim-env-security-update-2026-11-27).
# Usage: run_ab.sh <root> <min_free_gb> <ev_dir>
# <ev_dir> holds the batch folders of popsim-bs3-release-2026-11-25 (read only). For every batch and arm: copy
# configs/ and data/ to <root>/runs/<batch>_<arm>/ with an empty output/, then start PopulationSim as the popsim stage
# does (`uv run --no-sync populationsim -w <run folder>`, cwd = the arm's uv project) under GNU time, BLAS and OpenMP
# threads pinned to 1. All six runs start together. Before the start: wait until MemAvailable >= <min_free_gb> and record
# uptime, foreign java/populationsim processes and available memory in <root>/runs.load.
# Writes per run <run>.exit, <run>.time, <run>.stdout, <run>.sha256 and at the end <root>/ALL_DONE.
set -euo pipefail
ROOT="$1"; MINGB="$2"; EV="$3"
UV="$HOME/.local/bin/uv"
unset PYTHONPATH
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
declare -A SRC=([b000]="$EV/controlled_on/batch_000" [b032]="$EV/controlled_on/batch_032"
                [b028]="$EV/controlled_on_b028/batch_028")
BATCHES=(b000 b032 b028)
ARMS=(old new)
waited=0
while :; do
  avail_gb=$(awk '/MemAvailable/ {printf "%d", $2/1048576}' /proc/meminfo)
  [ "$avail_gb" -ge "$MINGB" ] && break
  echo "waiting for memory: ${avail_gb} GB available, need ${MINGB}" >&2
  sleep 60; waited=$((waited + 60))
  [ "$waited" -le 7200 ] || { echo "refuse: memory never free" >&2; exit 1; }
done
mkdir -p "$ROOT/runs"
{
  echo "start=$(date -Is)"
  echo "uptime=$(uptime)"
  echo "foreign_procs=$(ps -eo args | grep -E 'java|populationsim' | grep -v grep | grep -vc 'seclock-2026-11-27' || true)"
  echo "mem_available_gb=${avail_gb}"
  echo "waited_s=${waited}"
} > "$ROOT/runs.load"
for b in "${BATCHES[@]}"; do
  [ -f "${SRC[$b]}/configs/settings.yaml" ] || { echo "missing ${SRC[$b]}/configs/settings.yaml" >&2; exit 1; }
  for arm in "${ARMS[@]}"; do
    d="$ROOT/runs/${b}_${arm}"
    [ ! -e "$d" ] || { echo "refuse: $d exists" >&2; exit 1; }
    mkdir -p "$d"
    cp -r "${SRC[$b]}/configs" "${SRC[$b]}/data" "$d/"
    mkdir "$d/output"
  done
done
for b in "${BATCHES[@]}"; do
  for arm in "${ARMS[@]}"; do
    d="$ROOT/runs/${b}_${arm}"
    ( cd "$ROOT/$arm"
      rc=0
      /usr/bin/time -v -o "$d.time" "$UV" run --no-sync populationsim -w "$d" > "$d.stdout" 2>&1 || rc=$?
      echo "exit $rc" > "$d.exit" ) &
  done
done
wait
for b in "${BATCHES[@]}"; do
  for arm in "${ARMS[@]}"; do
    d="$ROOT/runs/${b}_${arm}"
    sha256sum "$d/output/final_expanded_household_ids.csv" "$d/output/synthetic_households.csv" \
      "$d/output/synthetic_persons.csv" > "$d.sha256" 2>&1 || true
  done
done
echo done > "$ROOT/ALL_DONE"
