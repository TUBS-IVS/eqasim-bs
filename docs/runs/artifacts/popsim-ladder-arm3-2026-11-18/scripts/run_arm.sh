#!/usr/bin/env bash
# Run PopulationSim on the prepared batch folders of one arm (issues #646 and #656).
# Usage: run_arm.sh <arm_dir> <populationsim_project_dir> <batch> [<batch> ...]
# As run_arm.sh of popsim-ladder-arm1-2026-11-17: the batches run in parallel with the stage's command
# and thread caps (`uv run --no-sync populationsim -w <folder>`, BLAS/OpenMP threads pinned to 1), wall
# time and peak RSS per batch come from GNU time, and tool.json records the installed package (version and
# the commit it was installed from). Added here: the sha256 of the three output files of every batch
# (outputs_sha256.txt), the size of its checkpoint store in bytes (<batch>.store_bytes) and, with
# DELETE_STORE=1, the deletion of that store after the run (for arms that get no diagnostics). Folders
# must come from prepare_variant.py (output/ empty).
set -euo pipefail
ARM_DIR="$1"; PROJECT="$2"; shift 2
UV="$HOME/.local/bin/uv"
for b in "$@"; do
  [ -f "$ARM_DIR/$b/configs/settings.yaml" ] || { echo "missing $ARM_DIR/$b/configs/settings.yaml" >&2; exit 1; }
  [ -z "$(ls -A "$ARM_DIR/$b/output")" ] || { echo "refuse: $ARM_DIR/$b/output not empty" >&2; exit 1; }
done
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
cd "$PROJECT"
"$UV" run --no-sync python -c "import importlib.metadata as m, json; d=m.distribution('populationsim'); print(json.dumps({'populationsim': d.version, 'direct_url': d.read_text('direct_url.json')}))" > "$ARM_DIR/tool.json"
for b in "$@"; do
  ( /usr/bin/time -v -o "$ARM_DIR/$b.time" "$UV" run --no-sync populationsim -w "$ARM_DIR/$b" \
      > "$ARM_DIR/$b.stdout" 2>&1; echo "exit $?" > "$ARM_DIR/$b.exit" ) &
done
wait
for b in "$@"; do
  sha256sum "$ARM_DIR/$b/output/final_expanded_household_ids.csv" "$ARM_DIR/$b/output/synthetic_households.csv" \
    "$ARM_DIR/$b/output/synthetic_persons.csv" >> "$ARM_DIR/outputs_sha256.txt" 2>&1 || true
  stat -c %s "$ARM_DIR/$b/output/pipeline.h5" > "$ARM_DIR/$b.store_bytes" 2>/dev/null || true
  if [ "${DELETE_STORE:-0}" = "1" ]; then rm -f "$ARM_DIR/$b/output/pipeline.h5"; fi
done
echo done > "$ARM_DIR/ARM_DONE"
