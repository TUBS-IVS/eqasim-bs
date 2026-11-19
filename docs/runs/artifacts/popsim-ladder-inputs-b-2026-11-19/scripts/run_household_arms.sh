#!/usr/bin/env bash
# Household-universe ladder arms of issue #659 (Part B, Task B6), in three phases. Adapted from
# run_input_arms.sh of popsim-ladder-inputs-a-2026-11-18.
#
#   prepare  copies the base arm's folder of every batch into <root>/<arm>/<batch> with
#            prepare_household_arm.py and the given steps (comma list, applied in order). Deterministic,
#            no PopulationSim. The census inputs come from the environment (below).
#            With SELF_CHECK=1 the move_persons rebuild runs without the move into <root>/<arm>/<batch>
#            and must reproduce the base folder exactly (run it first, on a scratch arm name).
#   popsim   PopulationSim on the named arm and batches, two processes at a time (run_arm.sh of the
#            arm-3 record): one batch peaks near 30 GB (popsim-run-performance-2026-11-18), so more would
#            not fit the server's memory.
#   diag     the batch diagnostics with --misfit and --cells-1km (household_composition.csv needs the
#            1 km family table) into <root>/diag_<arm><suffix>; deletes the batches' stores after a
#            successful run, as run_diag.sh of the arm-3 record.
#
# Usage: run_household_arms.sh prepare <root> <code_dir> <commit> <arm> <base_arm_dir> <steps> <batch>...
#          env: CELLS_100M, KREISE (comma list); CELLS_1KM (family_minor_child);
#               KREIS_CONTROLS_DIR, DATA_PATH, EMP_SHAPE_SOURCE (move_persons with the employed grid)
#        run_household_arms.sh popsim <root> <code_dir> <commit> <arm> <populationsim_project_dir> <batch>...
#        run_household_arms.sh diag <root> <code_dir> <commit> <arm> <suffix> <mid_households_csv> <cells_1km> <batch>...
# Every step appends one line to <root>/progress.log.
set -uo pipefail
PHASE="$1"; ROOT="$2"; CODE="$3"; COMMIT="$4"; ARM="$5"; shift 5
ART="$CODE/docs/runs/artifacts"
P="$ART/popsim-ladder-inputs-b-2026-11-19/scripts/prepare_household_arm.py"
PYTHON="${PYTHON:-$HOME/miniforge3/envs/eqasim/bin/python}"
mkdir -p "$ROOT/logs"
say() { echo "$(date +%F_%T) $*" >> "$ROOT/progress.log"; }
export PYTHONPATH="$CODE" EQASIM_BS_COMMIT="$COMMIT" OPENBLAS_NUM_THREADS=2
cd "$HOME"

if [ "$PHASE" = "prepare" ]; then
  BASE_ARM="$1"; STEPS="$2"; shift 2
  EXTRA=()
  [ -n "${CELLS_100M:-}" ] && EXTRA+=(--cells-100m "$CELLS_100M")
  [ -n "${KREISE:-}" ] && EXTRA+=(--kreise "$KREISE")
  [ -n "${CELLS_1KM:-}" ] && EXTRA+=(--cells-1km "$CELLS_1KM")
  [ -n "${KREIS_CONTROLS_DIR:-}" ] && EXTRA+=(--kreis-controls-dir "$KREIS_CONTROLS_DIR")
  [ -n "${DATA_PATH:-}" ] && EXTRA+=(--data-path "$DATA_PATH")
  [ -n "${EMP_SHAPE_SOURCE:-}" ] && EXTRA+=(--employment-age-shape-source "$EMP_SHAPE_SOURCE")
  [ "${SELF_CHECK:-0}" = "1" ] && EXTRA+=(--self-check)
  mkdir -p "$ROOT/$ARM"
  for b in "$@"; do
    [ ! -e "$ROOT/$ARM/$b" ] || { say "refuse: $ROOT/$ARM/$b exists"; exit 1; }
    "$PYTHON" "$P" --base "$BASE_ARM/$b" --out "$ROOT/$ARM/$b" --steps "$STEPS" --commit "$COMMIT" \
      ${EXTRA[@]+"${EXTRA[@]}"} > "$ROOT/logs/$ARM.$b.prepare.log" 2>&1 \
      || { say "PREPARE FAILED $ARM $b (steps $STEPS)"; exit 1; }
    say "prepared $ARM $b from $BASE_ARM/$b (steps $STEPS)"
  done
  say "prepare $ARM done"
elif [ "$PHASE" = "popsim" ]; then
  PROJECT="$1"; shift
  RUN="$ART/popsim-ladder-arm3-2026-11-18/scripts/run_arm.sh"
  # Two PopulationSim processes at a time: one run_arm.sh call per pair (it starts its batches in
  # parallel and waits for them), then the next pair. One call per pair, because run_arm.sh rewrites
  # <arm>/tool.json on every call.
  while [ "$#" -gt 0 ]; do
    pair=("$1"); shift
    if [ "$#" -gt 0 ]; then pair+=("$1"); shift; fi
    say "start PopulationSim $ARM: ${pair[*]}"
    bash "$RUN" "$ROOT/$ARM" "$PROJECT" "${pair[@]}" > "$ROOT/logs/$ARM.$(IFS=_; echo "${pair[*]}").run.log" 2>&1
    for b in "${pair[@]}"; do say "end PopulationSim $ARM $b: $(cat "$ROOT/$ARM/$b.exit" 2>/dev/null)"; done
  done
elif [ "$PHASE" = "diag" ]; then
  SUFFIX="$1"; MID="$2"; CELLS_1KM_DIAG="$3"; shift 3
  OUT="$ROOT/diag_$ARM$SUFFIX"
  [ ! -e "$OUT" ] || { say "refuse: $OUT exists"; exit 1; }
  say "start diagnostics $ARM$SUFFIX: $*"
  (
    export OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4
    cd "$CODE" || exit 1
    /usr/bin/time -v -o "$OUT.time" "$PYTHON" -m braunschweig.analysis.popsim_batch_diagnostics.run \
      --work-dir "$ROOT/$ARM" --out-dir "$OUT" --batches "$@" --mid-households "$MID" --misfit \
      --cells-1km "$CELLS_1KM_DIAG" > "$OUT.log" 2>&1
  )
  RC=$?
  echo "exit $RC" >> "$OUT.log"
  if [ "$RC" -eq 0 ]; then
    for b in "$@"; do rm -f "$ROOT/$ARM/$b/output/pipeline.h5"; done
  fi
  echo "done rc=$RC" > "$OUT.DONE"
  say "end diagnostics $ARM$SUFFIX: rc=$RC"
else
  echo "unknown phase $PHASE" >&2; exit 2
fi
