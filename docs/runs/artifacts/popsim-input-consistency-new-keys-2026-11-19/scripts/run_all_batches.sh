#!/usr/bin/env bash
# Input consistency of all 60 production batches with the production keys of issue #658 (ADR-0203).
#
# For every batch folder of the production work dir, prepare_input_arm.py (the ladder driver of
# popsim-ladder-inputs-a-2026-11-18, same library functions as the stage) rewrites the KREIS table with all
# three steps (kreis_universe, kreis_follow_grids, employment_level) into a copy; then
# run_input_consistency.py measures the copies with the diagnostics' reader. Table work only, no
# PopulationSim. It answers whether the stage guard (0.05 for the age pairs in parts with >= 1,000
# persons, 1e-6 for the grid pairs) passes on every batch, and gives the residual of the new split.
#
# Usage: run_all_batches.sh <root> <code_dir> <commit> <prod_work_dir> <cells_parquet>
set -uo pipefail
ROOT="$1"; CODE="$2"; COMMIT="$3"; PROD="$4"; CELLS="$5"
ART="$CODE/docs/runs/artifacts"
PYTHON="${PYTHON:-$HOME/miniforge3/envs/eqasim/bin/python}"
[ ! -e "$ROOT" ] || { echo "refuse: $ROOT exists" >&2; exit 1; }
mkdir -p "$ROOT/folders" "$ROOT/logs"
export PYTHONPATH="$CODE" EQASIM_BS_COMMIT="$COMMIT" OPENBLAS_NUM_THREADS=2
cd "$HOME"
failed=0
for base in "$PROD"/batch_*; do
  b="$(basename "$base")"
  "$PYTHON" "$ART/popsim-ladder-inputs-a-2026-11-18/scripts/prepare_input_arm.py" --base "$base" \
    --out "$ROOT/folders/$b" --steps kreis_universe,kreis_follow_grids,employment_level \
    --prod-work-dir "$PROD" --cells-100m "$CELLS" --commit "$COMMIT" > "$ROOT/logs/$b.log" 2>&1 \
    || { echo "FAILED $b" >> "$ROOT/failed.txt"; failed=$((failed + 1)); }
done
echo "prepare failures: $failed" > "$ROOT/prepare_summary.txt"
"$PYTHON" "$ART/popsim-input-consistency-2026-11-18/scripts/run_input_consistency.py" --set new_keys \
  --work-dir "$ROOT/folders" --out "$ROOT/input_consistency_new_keys.csv" --commit "$COMMIT" \
  > "$ROOT/logs/measure.log" 2>&1
echo "measure rc=$?" >> "$ROOT/prepare_summary.txt"
