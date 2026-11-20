#!/usr/bin/env bash
# Run PopulationSim on prepared batch folders through capture_simul_instances.py (issue #646).
# Usage: run_capture.sh <arm_dir> <populationsim_project_dir> <capture_script> <batch> [<batch> ...]
# Same command, thread caps and time record as run_arm.sh of the ladder arms; the only difference is
# the wrapper, which saves the simultaneous integerizer inputs to <arm_dir>/<batch>_instances/.
set -euo pipefail
ARM_DIR="$1"; PROJECT="$2"; CAPTURE="$3"; shift 3
UV="$HOME/.local/bin/uv"
for b in "$@"; do
  [ -f "$ARM_DIR/$b/configs/settings.yaml" ] || { echo "missing $ARM_DIR/$b/configs/settings.yaml" >&2; exit 1; }
  [ -z "$(ls -A "$ARM_DIR/$b/output")" ] || { echo "refuse: $ARM_DIR/$b/output not empty" >&2; exit 1; }
  [ ! -e "$ARM_DIR/${b}_instances" ] || { echo "refuse: $ARM_DIR/${b}_instances exists" >&2; exit 1; }
done
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
cd "$PROJECT"
"$UV" run --no-sync python -c "import importlib.metadata as m, json; d=m.distribution('populationsim'); print(json.dumps({'populationsim': d.version, 'direct_url': d.read_text('direct_url.json')}))" > "$ARM_DIR/tool.json"
for b in "$@"; do
  ( /usr/bin/time -v -o "$ARM_DIR/$b.time" "$UV" run --no-sync python "$CAPTURE" "$ARM_DIR/${b}_instances" -w "$ARM_DIR/$b" \
      > "$ARM_DIR/$b.stdout" 2>&1; echo "exit $?" > "$ARM_DIR/$b.exit" ) &
done
wait
echo done > "$ARM_DIR/ARM_DONE"
