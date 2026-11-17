#!/usr/bin/env bash
# Run one ladder arm of #138 (WP1) on two production batches of prod100_20261031.
# Usage: run_arm.sh <arm_dir> <timeout_key> <project_dir_with_populationsim_env>
# Copies configs/ and data/ of each batch (never output/), appends the solver time limit,
# runs PopulationSim with the stage's command and thread caps, two batches in parallel,
# and records wall time and peak RSS per batch with /usr/bin/time -v.
set -euo pipefail
ARM_DIR="$1"; TIMEOUT_KEY="$2"; PROJECT="$3"
SRC="$HOME/prod100_20261031/popsim_work"
UV="${UV:-$HOME/.local/bin/uv}"   # arm 0b needs a uv >= 0.13.4 (upstream required-version)
mkdir -p "$ARM_DIR"
for b in batch_000 batch_032; do
  if [ -e "$ARM_DIR/$b" ]; then echo "refuse: $ARM_DIR/$b exists" >&2; exit 1; fi
  mkdir -p "$ARM_DIR/$b"
  cp -a "$SRC/$b/configs" "$SRC/$b/data" "$ARM_DIR/$b/"
  printf '\n%s: 7200\n' "$TIMEOUT_KEY" >> "$ARM_DIR/$b/configs/settings.yaml"
  mkdir -p "$ARM_DIR/$b/output"
done
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
cd "$PROJECT"
for b in batch_000 batch_032; do
  ( /usr/bin/time -v -o "$ARM_DIR/$b.time" "$UV" run --no-sync populationsim -w "$ARM_DIR/$b" \
      > "$ARM_DIR/$b.stdout" 2>&1; echo "exit $?" > "$ARM_DIR/$b.exit" ) &
done
wait
echo done > "$ARM_DIR/ARM_DONE"
