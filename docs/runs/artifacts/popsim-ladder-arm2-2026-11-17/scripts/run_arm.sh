#!/usr/bin/env bash
# Run PopulationSim on the prepared batch folders of one ladder arm of issue #645 (WP2).
# Usage: run_arm.sh <arm_dir> <populationsim_project_dir> <batch> [<batch> ...]
# Runs the batches in parallel with the stage's command and thread caps
# (`uv run --no-sync populationsim -w <folder>`, BLAS/OpenMP threads pinned to 1) and records wall time
# and peak RSS per batch with GNU time. Folders must come from prepare_arm.py (output/ empty).
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
echo done > "$ARM_DIR/ARM_DONE"
