#!/usr/bin/env bash
# Run the recorded WP1 batch diagnostics (braunschweig.analysis.popsim_batch_diagnostics) with the
# misfit split on one arm of issue #645, then delete that arm's PopulationSim checkpoint stores.
# Usage: run_diag.sh <arm_dir> <out_dir> <code_dir> <commit>
set -euo pipefail
ARM_DIR="$1"; OUT="$2"; CODE="$3"; COMMIT="$4"
MID=~/eqasim-bs/eqasim-data/data/braunschweig/popsim/mid2023_raw/MiD2023_Haushalte.csv
[ ! -e "$OUT" ] || { echo "refuse: $OUT exists" >&2; exit 1; }
export PYTHONPATH="$CODE" EQASIM_BS_COMMIT="$COMMIT" OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4
cd "$CODE"
/usr/bin/time -v -o "$OUT.time" ~/miniforge3/envs/eqasim/bin/python -m braunschweig.analysis.popsim_batch_diagnostics.run \
  --work-dir "$ARM_DIR" --out-dir "$OUT" --batches batch_000 batch_032 --mid-households "$MID" --misfit \
  > "$OUT.log" 2>&1
echo "exit $?" >> "$OUT.log"
sha256sum "$ARM_DIR"/batch_*/output/final_expanded_household_ids.csv > "$OUT.expanded_sha256"
rm -f "$ARM_DIR"/batch_*/output/pipeline.h5
echo done > "$OUT.DONE"
