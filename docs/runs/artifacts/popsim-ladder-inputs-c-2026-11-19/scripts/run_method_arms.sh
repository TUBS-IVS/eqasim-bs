#!/usr/bin/env bash
# Control-level and rounding measurements of issue #660 (Part C): arms 9 and 10, each prepared from the
# arm-8 folders of popsim-ladder-inputs-b-2026-11-19 and run on the same four batches. Adapted from
# run_household_arms.sh of that record.
#
#   prepare  copies <base_arm_dir>/<batch> into <root>/<arm>/<batch> with prepare_method_arm.py and ONE
#            step (age_sex_at_1km or float_census_controls). Deterministic, no PopulationSim. The census
#            inputs of float_census_controls come from the environment (CELLS_100M, KREISE).
#   popsim   PopulationSim on the named arm and batches, all at once (run_arm.sh of the arm-3 record).
#            The run-time options of #656 are on in the arm-8 settings.yaml the folders are copied from
#            (2.7 GB peak per batch in arm 8), so four processes fit the server's memory.
#   diag     the batch diagnostics of the arm as run, with --misfit and --cells-1km, into
#            <root>/diag_<arm>; deletes the batches' stores after a successful run.
#   view     the diagnostics of the output of <arm> judged against the TARGETS of another folder set:
#            <root>/views/<arm>@<view>/<batch>/ holds symlinks configs -> <configs_arm>/<batch>/configs,
#            data -> <data_arm>/<batch>/data, output -> <root>/<arm>/<batch>/output; the diagnostics
#            (no --misfit: the stores are gone; household composition and solver paths come from the
#            output and are the same as in diag_<arm>) go to <root>/diag_views/<arm>@<view>.
#            Views of the record: int100 = arm_8 configs + arm_8 data (age x sex at 100 m, integer
#            targets); int1km = arm_9 configs + arm_8 data (age x sex at 1 km, integer targets);
#            flt100 = arm_10 configs + arm_10 data (age x sex at 100 m, unrounded targets); flt1km =
#            arm_9 configs + arm_10 data (age x sex at 1 km, unrounded targets).
#
# Usage: run_method_arms.sh prepare <root> <code_dir> <commit> <arm> <base_arm_dir> <step> <batch>...
#          env (float_census_controls): CELLS_100M, KREISE (comma list)
#        run_method_arms.sh popsim <root> <code_dir> <commit> <arm> <populationsim_project_dir> <batch>...
#        run_method_arms.sh diag <root> <code_dir> <commit> <arm> <mid_households_csv> <cells_1km> <batch>...
#        run_method_arms.sh view <root> <code_dir> <commit> <arm> <view> <configs_arm> <data_arm> <batch>...
# Every step appends one line to <root>/progress.log; every run leaves <arm>/<batch>.exit or <out>.DONE,
# which is what a chained step waits on (never a line of progress.log).
set -uo pipefail
PHASE="$1"; ROOT="$2"; CODE="$3"; COMMIT="$4"; ARM="$5"; shift 5
ART="$CODE/docs/runs/artifacts"
P="$ART/popsim-ladder-inputs-c-2026-11-19/scripts/prepare_method_arm.py"
PYTHON="${PYTHON:-$HOME/miniforge3/envs/eqasim/bin/python}"
mkdir -p "$ROOT/logs"
say() { echo "$(date +%F_%T) $*" >> "$ROOT/progress.log"; }
case "$ROOT" in /*) ;; *) echo "root must be an absolute path: $ROOT" >&2; exit 2;; esac
export PYTHONPATH="$CODE" EQASIM_BS_COMMIT="$COMMIT" OPENBLAS_NUM_THREADS=2
cd "$HOME"

run_diagnostics() {  # <work_dir> <out_dir> <batches...>; extra flags in DIAG_FLAGS
  local work="$1" out="$2"; shift 2
  [ ! -e "$out" ] || { say "refuse: $out exists"; echo "done rc=1 (refused: $out exists)" > "$out.DONE"; return 1; }
  (
    export OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4
    cd "$CODE" || exit 1
    /usr/bin/time -v -o "$out.time" "$PYTHON" -m braunschweig.analysis.popsim_batch_diagnostics.run \
      --work-dir "$work" --out-dir "$out" --batches "$@" ${DIAG_FLAGS[@]+"${DIAG_FLAGS[@]}"} > "$out.log" 2>&1
  )
  local rc=$?
  echo "exit $rc" >> "$out.log"
  echo "done rc=$rc" > "$out.DONE"
  return $rc
}

if [ "$PHASE" = "prepare" ]; then
  BASE_ARM="$1"; STEP="$2"; shift 2
  EXTRA=()
  [ -n "${CELLS_100M:-}" ] && EXTRA+=(--cells-100m "$CELLS_100M")
  [ -n "${KREISE:-}" ] && EXTRA+=(--kreise "$KREISE")
  mkdir -p "$ROOT/$ARM"
  for b in "$@"; do
    [ ! -e "$ROOT/$ARM/$b" ] || { say "refuse: $ROOT/$ARM/$b exists"; exit 1; }
    "$PYTHON" "$P" --base "$BASE_ARM/$b" --out "$ROOT/$ARM/$b" --step "$STEP" --commit "$COMMIT" \
      ${EXTRA[@]+"${EXTRA[@]}"} > "$ROOT/logs/$ARM.$b.prepare.log" 2>&1 \
      || { say "PREPARE FAILED $ARM $b (step $STEP)"; exit 1; }
    say "prepared $ARM $b from $BASE_ARM/$b (step $STEP)"
  done
  say "prepare $ARM done"
elif [ "$PHASE" = "popsim" ]; then
  PROJECT="$1"; shift
  RUN="$ART/popsim-ladder-arm3-2026-11-18/scripts/run_arm.sh"
  say "start PopulationSim $ARM: $*"
  bash "$RUN" "$ROOT/$ARM" "$PROJECT" "$@" > "$ROOT/logs/$ARM.run.log" 2>&1
  FAILED=0
  for b in "$@"; do
    EXIT_LINE="$(cat "$ROOT/$ARM/$b.exit" 2>/dev/null || echo "no exit file")"
    say "end PopulationSim $ARM $b: $EXIT_LINE"
    [ "$EXIT_LINE" = "exit 0" ] || FAILED=1
  done
  exit "$FAILED"
elif [ "$PHASE" = "diag" ]; then
  MID="$1"; CELLS_1KM_DIAG="$2"; shift 2
  OUT="$ROOT/diag_$ARM"
  DIAG_FLAGS=(--mid-households "$MID" --misfit --cells-1km "$CELLS_1KM_DIAG")
  say "start diagnostics $ARM: $*"
  run_diagnostics "$ROOT/$ARM" "$OUT" "$@"
  RC=$?
  if [ "$RC" -eq 0 ]; then
    for b in "$@"; do rm -f "$ROOT/$ARM/$b/output/pipeline.h5"; done
  fi
  say "end diagnostics $ARM: rc=$RC"
elif [ "$PHASE" = "view" ]; then
  VIEW="$1"; CONFIGS_ARM="$2"; DATA_ARM="$3"; shift 3
  VDIR="$ROOT/views/$ARM@$VIEW"
  [ ! -e "$VDIR" ] || { say "refuse: $VDIR exists"; exit 1; }
  for b in "$@"; do
    for src in "$ROOT/$CONFIGS_ARM/$b/configs" "$ROOT/$DATA_ARM/$b/data" "$ROOT/$ARM/$b/output"; do
      [ -d "$src" ] || { say "refuse view $ARM@$VIEW: $src is not a directory"; exit 1; }
    done
  done
  for b in "$@"; do
    mkdir -p "$VDIR/$b"
    ln -s "$ROOT/$CONFIGS_ARM/$b/configs" "$VDIR/$b/configs"
    ln -s "$ROOT/$DATA_ARM/$b/data" "$VDIR/$b/data"
    ln -s "$ROOT/$ARM/$b/output" "$VDIR/$b/output"
  done
  mkdir -p "$ROOT/diag_views"
  DIAG_FLAGS=()
  say "start view diagnostics $ARM@$VIEW (configs $CONFIGS_ARM, data $DATA_ARM): $*"
  run_diagnostics "$VDIR" "$ROOT/diag_views/$ARM@$VIEW" "$@"
  say "end view diagnostics $ARM@$VIEW: rc=$?"
else
  echo "unknown phase $PHASE" >&2; exit 2
fi
