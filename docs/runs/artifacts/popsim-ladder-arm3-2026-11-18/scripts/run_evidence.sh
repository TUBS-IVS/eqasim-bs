#!/usr/bin/env bash
# Prepare and run every arm of the two run manifests of issues #646 (controlled rounding) and #656
# (run-time and memory options) with one installed PopulationSim build, in an order that keeps the
# memory of the run server in bounds (a run without the memory option needs about 30 GB).
#
# Usage: run_evidence.sh <root> <populationsim_project_dir> <code_dir> <commit> <version> \
#            <ladder_src> <largest_src> <mid_households_csv>
#   root         new directory for all arm folders, logs and diagnostics
#   code_dir     eqasim-bs code (git archive) at <commit>: prepare_variant.py and the diagnostics import it
#   version      populationsim_version the arms are prepared for (the installed build must report it)
#   ladder_src   the arm-2 folders with batch_000 and batch_032
#   largest_src  a folder with batch_028 prepared like arm 2
#
# Arms (settings variant x batches); "off"/"on" = both run-time options off / on:
#   round_off, round_on, controlled_off, controlled_on           batch_000 batch_032
#   controlled_off_repeat                                        batch_000
#   round_off_b028, round_on_b028, controlled_off_b028, controlled_on_b028   batch_028
# Diagnostics with --misfit: round_off, controlled_off and controlled_on (batch_000, batch_032).
# Every step appends one line to <root>/progress.log; <root>/EVIDENCE_DONE marks the end.
set -uo pipefail
ROOT="$1"; PROJECT="$2"; CODE="$3"; COMMIT="$4"; VERSION="$5"; LADDER_SRC="$6"; LARGEST_SRC="$7"; MID="$8"
HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHON=~/miniforge3/envs/eqasim/bin/python
RANDOM_SEED=1234
[ ! -e "$ROOT" ] || { echo "refuse: $ROOT exists" >&2; exit 1; }
mkdir -p "$ROOT"
say() { echo "$(date +%F_%T) $*" >> "$ROOT/progress.log"; }

prepare() {  # prepare <arm> <src> <rounding> <options: off|on> <batch...>
  local arm="$1" src="$2" rounding="$3" options="$4"; shift 4
  local flags=()
  if [ "$options" = "on" ]; then flags=(--store-positive-rows --fast-paths); fi
  PYTHONPATH="$CODE" "$PYTHON" "$HERE/prepare_variant.py" --src "$src" --arm-dir "$ROOT/$arm" \
    --batches "$@" --random-seed "$RANDOM_SEED" --rounding "$rounding" \
    --populationsim-version "$VERSION" "${flags[@]}" > "$ROOT/$arm.prepare.log" 2>&1 \
    || { say "PREPARE FAILED $arm"; exit 1; }
}
run() {  # run <arm> <delete store: 0|1> <batch...>
  local arm="$1" delete="$2"; shift 2
  say "start $arm $*"
  DELETE_STORE="$delete" "$HERE/run_arm.sh" "$ROOT/$arm" "$PROJECT" "$@" > "$ROOT/$arm.run.log" 2>&1
  say "end $arm rc=$? $(cat "$ROOT/$arm"/*.exit 2>/dev/null | tr '\n' ' ')"
}
diag() {  # diag <arm> <batch...>
  local arm="$1"; shift
  say "start diagnostics $arm"
  "$HERE/run_diag.sh" "$ROOT/$arm" "$ROOT/diag_$arm" "$CODE" "$COMMIT" "$MID" "$@" > "$ROOT/diag_$arm.launch.log" 2>&1
  say "end diagnostics $arm: $(cat "$ROOT/diag_$arm.DONE" 2>/dev/null)"
}

say "prepare (code $COMMIT, version $VERSION)"
prepare round_off "$LADDER_SRC" round off batch_000 batch_032
prepare round_on "$LADDER_SRC" round on batch_000 batch_032
prepare controlled_off "$LADDER_SRC" controlled off batch_000 batch_032
prepare controlled_on "$LADDER_SRC" controlled on batch_000 batch_032
prepare controlled_off_repeat "$LADDER_SRC" controlled off batch_000
prepare round_off_b028 "$LARGEST_SRC" round off batch_028
prepare round_on_b028 "$LARGEST_SRC" round on batch_028
prepare controlled_off_b028 "$LARGEST_SRC" controlled off batch_028
prepare controlled_on_b028 "$LARGEST_SRC" controlled on batch_028

# Wave 1: rounding "round", options off and on at the same time (like-for-like timing).
run round_off 0 batch_000 batch_032 &
run round_on 1 batch_000 batch_032 &
wait
# Wave 2: rounding "controlled", options off and on at the same time (both stores are kept for the
# diagnostics: the one with the options on shows that the diagnostics read a positive-row store alike).
run controlled_off 0 batch_000 batch_032 &
run controlled_on 0 batch_000 batch_032 &
wait
# Wave 3: the largest batch. Its run with "round" and the options off starts at once, next to the two
# runs with the options on and the three diagnostics; the controlled run without the options follows
# when the diagnostics have freed their memory.
run round_off_b028 1 batch_028 &
run round_on_b028 1 batch_028 &
run controlled_on_b028 1 batch_028 &
( diag round_off batch_000 batch_032 &
  diag controlled_off batch_000 batch_032 &
  diag controlled_on batch_000 batch_032 &
  wait
  run controlled_off_b028 1 batch_028 ) &
wait
# Last: the repeat of batch_000 (controlled, options off).
run controlled_off_repeat 1 batch_000
say "all runs ended"
echo done > "$ROOT/EVIDENCE_DONE"
