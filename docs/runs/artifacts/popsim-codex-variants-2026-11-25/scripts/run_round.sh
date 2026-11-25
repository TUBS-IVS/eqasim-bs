#!/usr/bin/env bash
# One measurement round of the PopulationSim code variants (issue #677).
# Usage: run_round.sh <batch_src_dir> <batch_tag> <round> <arm> [<arm> ...]
# For every arm: copy the prepared batch folder to ~/i677/runs/<tag>_<arm>_r<round>/ (output/ emptied),
# start PopulationSim with PYTHONPATH=~/i677/trees/<arm> and wait for all arms of the round.
# Command and thread caps as run_arm.sh of popsim-ladder-arm3-2026-11-18, but `python -m populationsim -w`
# from the benchmark environment (the arm tree wins on PYTHONPATH). Wall time and peak RSS come from GNU time.
set -euo pipefail
SRC="$1"; TAG="$2"; RND="$3"; shift 3
ROOT="$HOME/i677"
PY="$HOME/i646/env_bs2/.venv/bin/python"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
for arm in "$@"; do
  d="$ROOT/runs/${TAG}_${arm}_r${RND}"
  [ ! -e "$d" ] || { echo "refuse: $d exists" >&2; exit 1; }
  mkdir -p "$d"
  cp -r "$SRC/configs" "$SRC/data" "$d/"
  mkdir "$d/output"
done
for arm in "$@"; do
  d="$ROOT/runs/${TAG}_${arm}_r${RND}"
  ( cd "$d" && PYTHONPATH="$ROOT/trees/$arm" /usr/bin/time -v -o "$d.time" "$PY" -m populationsim -w "$d" \
      > "$d.stdout" 2>&1; echo "exit $?" > "$d.exit" ) &
done
wait
for arm in "$@"; do
  d="$ROOT/runs/${TAG}_${arm}_r${RND}"
  sha256sum "$d/output/final_expanded_household_ids.csv" "$d/output/synthetic_households.csv" \
    "$d/output/synthetic_persons.csv" > "$d.sha256" 2>&1 || true
done
echo done > "$ROOT/runs/${TAG}_r${RND}.DONE"
