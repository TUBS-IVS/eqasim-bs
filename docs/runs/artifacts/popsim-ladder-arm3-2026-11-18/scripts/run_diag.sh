#!/usr/bin/env bash
# Run the batch diagnostics (braunschweig.analysis.popsim_batch_diagnostics) with the misfit split and the
# household conservation table on named batches of one arm, then delete those batches' PopulationSim
# checkpoint stores (several GB each).
# Usage: run_diag.sh <arm_dir> <out_dir> <code_dir> <commit> <mid_households_csv> <batch> [<batch> ...]
# As run_diag.sh of popsim-ladder-arm1-2026-11-17, with the batches and the survey file as arguments.
set -euo pipefail
ARM_DIR="$1"; OUT="$2"; CODE="$3"; COMMIT="$4"; MID="$5"; shift 5
[ ! -e "$OUT" ] || { echo "refuse: $OUT exists" >&2; exit 1; }
export PYTHONPATH="$CODE" EQASIM_BS_COMMIT="$COMMIT" OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4
cd "$CODE"
set +e
/usr/bin/time -v -o "$OUT.time" ~/miniforge3/envs/eqasim/bin/python -m braunschweig.analysis.popsim_batch_diagnostics.run \
  --work-dir "$ARM_DIR" --out-dir "$OUT" --batches "$@" --mid-households "$MID" --misfit \
  > "$OUT.log" 2>&1
RC=$?
set -e
echo "exit $RC" >> "$OUT.log"
if [ "$RC" -eq 0 ]; then
  for b in "$@"; do rm -f "$ARM_DIR/$b/output/pipeline.h5"; done
fi
echo "done rc=$RC" > "$OUT.DONE"
