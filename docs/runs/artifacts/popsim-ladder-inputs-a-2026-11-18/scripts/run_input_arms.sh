#!/usr/bin/env bash
# Ladder arms 5 and 6 of issue #658 (Part A, Tasks A9 and A10), in two phases.
#
#   prepare  copies the arm-4 folder of every batch and rewrites only control_totals_KREIS.csv with
#            prepare_input_arm.py: arm 5 = steps kreis_universe,kreis_follow_grids; arm 6 = arm 5 steps
#            plus employment_level (both from the arm-4 folder, so arm 6 differs from arm 5 by the
#            employment step only). Deterministic, no PopulationSim; may run before the pre-registration.
#   run      PopulationSim on both arms and all batches at once (8 processes; run_arm.sh of the arm-3
#            record, stores kept), then the diagnostics with --misfit per arm (run_diag.sh of the arm-3
#            record, which deletes the stores after a successful run). Only after the pre-registration
#            is committed.
#
# Usage: run_input_arms.sh prepare <root> <code_dir> <commit> <prod_work_dir> <cells_parquet> <arm4 batch dir>...
#        run_input_arms.sh run <root> <code_dir> <commit> <populationsim_project_dir> <mid_households_csv> <batch>...
# Every step appends one line to <root>/progress.log.
set -uo pipefail
PHASE="$1"; ROOT="$2"; CODE="$3"; COMMIT="$4"; shift 4
ART="$CODE/docs/runs/artifacts"
P="$ART/popsim-ladder-inputs-a-2026-11-18/scripts/prepare_input_arm.py"
PYTHON="${PYTHON:-$HOME/miniforge3/envs/eqasim/bin/python}"
mkdir -p "$ROOT/logs"
say() { echo "$(date +%F_%T) $*" >> "$ROOT/progress.log"; }
export PYTHONPATH="$CODE" EQASIM_BS_COMMIT="$COMMIT" OPENBLAS_NUM_THREADS=2
cd "$HOME"

if [ "$PHASE" = "prepare" ]; then
  PROD="$1"; CELLS="$2"; shift 2
  for base in "$@"; do
    b="$(basename "$base")"
    for arm in 5 6; do
      steps="kreis_universe,kreis_follow_grids"
      [ "$arm" = "6" ] && steps="$steps,employment_level"
      [ ! -e "$ROOT/arm_$arm/$b" ] || { say "refuse: $ROOT/arm_$arm/$b exists"; exit 1; }
      mkdir -p "$ROOT/arm_$arm"
      "$PYTHON" "$P" --base "$base" --out "$ROOT/arm_$arm/$b" --steps "$steps" --prod-work-dir "$PROD" \
        --cells-100m "$CELLS" --commit "$COMMIT" > "$ROOT/logs/arm_$arm.$b.prepare.log" 2>&1 \
        || { say "PREPARE FAILED arm $arm $b"; exit 1; }
      say "prepared arm $arm $b from $base"
    done
  done
  say "prepare done"
elif [ "$PHASE" = "run" ]; then
  PROJECT="$1"; MID="$2"; shift 2
  RUN="$ART/popsim-ladder-arm3-2026-11-18/scripts/run_arm.sh"
  DIAG="$ART/popsim-ladder-arm3-2026-11-18/scripts/run_diag.sh"
  # Two PopulationSim processes at a time (arm 5 and arm 6 of one batch): with the run-time options of
  # #656 off, as in arm 3, one batch peaks at 26-32 GiB (popsim-run-performance-2026-11-18), so more
  # would not fit the server's memory. run_arm.sh appends to tool.json / outputs_sha256.txt per call.
  for b in "$@"; do
    say "start PopulationSim arms 5 and 6: $b"
    bash "$RUN" "$ROOT/arm_5" "$PROJECT" "$b" > "$ROOT/logs/arm_5.$b.run.log" 2>&1 &
    bash "$RUN" "$ROOT/arm_6" "$PROJECT" "$b" > "$ROOT/logs/arm_6.$b.run.log" 2>&1 &
    wait
  done
  say "end PopulationSim: arm 5 $(cat "$ROOT"/arm_5/*.exit 2>/dev/null | tr '\n' ' '); arm 6 $(cat "$ROOT"/arm_6/*.exit 2>/dev/null | tr '\n' ' ')"
  for arm in 5 6; do
    bash "$DIAG" "$ROOT/arm_$arm" "$ROOT/diag_arm_$arm" "$CODE" "$COMMIT" "$MID" "$@" \
      > "$ROOT/logs/diag_arm_$arm.launch.log" 2>&1 &
  done
  wait
  say "end diagnostics: arm 5 $(cat "$ROOT/diag_arm_5.DONE" 2>/dev/null); arm 6 $(cat "$ROOT/diag_arm_6.DONE" 2>/dev/null)"
  echo done > "$ROOT/RUN_DONE"
else
  echo "unknown phase $PHASE" >&2; exit 2
fi
