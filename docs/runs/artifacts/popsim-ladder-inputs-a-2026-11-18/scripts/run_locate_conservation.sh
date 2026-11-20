#!/usr/bin/env bash
# Post hoc (issue #658): run one batch of several ladder arms again with the checkpoint store kept, check
# that each rerun's outputs are byte-identical to the evidence run of that arm, and split the household
# conservation by 1 km parent (locate_conservation.py). The evidence runs kept their outputs but deleted
# their stores, and the per-parent split can only be read from a store.
#
# Usage: run_locate_conservation.sh <root> <populationsim_project_dir> <code_dir> <commit> <batch> \
#            <label>=<prepared folder> [<label>=<prepared folder> ...]
#   root                 new directory for the batch folders, logs and tables
#   code_dir             eqasim-bs code (git archive) at <commit>
#   <label>=<folder>     one arm: its label and its prepared batch folder (configs/, data/ and the
#                        evidence run's output/); the first label is the baseline of the comparison
#
# configs/ and data/ of each prepared folder are copied to <root>/<label>/<batch> with an empty output/;
# all arms run in parallel with run_arm.sh of popsim-ladder-arm3-2026-11-18 (stores kept). The three output
# files final_expanded_household_ids.csv, synthetic_households.csv and synthetic_persons.csv must be
# byte-identical (sha256) to those of the prepared folder's output/, else the script stops. The stores are
# deleted at the end.
set -euo pipefail
ROOT="$1"; PROJECT="$2"; CODE="$3"; COMMIT="$4"; BATCH="$5"; shift 5
[ "$#" -ge 1 ] || { echo "usage: $0 <root> <project> <code_dir> <commit> <batch> <label>=<folder> ..." >&2; exit 1; }
HERE="$(cd "$(dirname "$0")" && pwd)"
RUN_ARM="$HERE/../../popsim-ladder-arm3-2026-11-18/scripts/run_arm.sh"
PYTHON=~/miniforge3/envs/eqasim/bin/python
OUTPUTS="final_expanded_household_ids.csv synthetic_households.csv synthetic_persons.csv"
[ ! -e "$ROOT" ] || { echo "refuse: $ROOT exists" >&2; exit 1; }
[ -f "$RUN_ARM" ] || { echo "missing $RUN_ARM" >&2; exit 1; }
LABELS=(); FOLDERS=()
for pair in "$@"; do
  label="${pair%%=*}"; folder="${pair#*=}"
  [ "$label" != "$pair" ] && [ -n "$label" ] || { echo "bad argument (want label=folder): $pair" >&2; exit 1; }
  [ -f "$folder/configs/settings.yaml" ] && [ -d "$folder/data" ] || { echo "not a prepared folder: $folder" >&2; exit 1; }
  for name in $OUTPUTS; do
    [ -f "$folder/output/$name" ] || { echo "no evidence output $folder/output/$name" >&2; exit 1; }
  done
  LABELS+=("$label"); FOLDERS+=("$folder")
done
mkdir -p "$ROOT"
say() { echo "$(date +%F_%T) $*" >> "$ROOT/progress.log"; }

say "copy configs and data (code $COMMIT, batch $BATCH, arms ${LABELS[*]})"
for i in "${!LABELS[@]}"; do
  target="$ROOT/${LABELS[$i]}/$BATCH"
  mkdir -p "$target/output"
  cp -a "${FOLDERS[$i]}/configs" "$target/configs"
  cp -a "${FOLDERS[$i]}/data" "$target/data"
done

say "start runs in parallel"
for label in "${LABELS[@]}"; do
  ( bash "$RUN_ARM" "$ROOT/$label" "$PROJECT" "$BATCH" > "$ROOT/$label.run.log" 2>&1 ) &
done
wait
failed=0
for label in "${LABELS[@]}"; do
  state="$(cat "$ROOT/$label/$BATCH.exit" 2>/dev/null || echo 'no exit file')"
  say "end run $label: $state"
  [ "$state" = "exit 0" ] || failed=1
done
[ "$failed" = 0 ] || { say "RUN FAILED"; exit 1; }

: > "$ROOT/identity_check.txt"
for i in "${!LABELS[@]}"; do
  for name in $OUTPUTS; do
    rerun="$(sha256sum "$ROOT/${LABELS[$i]}/$BATCH/output/$name" | cut -d' ' -f1)"
    evidence="$(sha256sum "${FOLDERS[$i]}/output/$name" | cut -d' ' -f1)"
    if [ "$rerun" = "$evidence" ]; then verdict=identical; else verdict=DIFFERENT; fi
    echo "${LABELS[$i]} $name rerun=$rerun evidence=$evidence $verdict" >> "$ROOT/identity_check.txt"
    [ "$verdict" = identical ] || { say "OUTPUT DIFFERS from the evidence run: ${LABELS[$i]} $name"; exit 1; }
  done
done
say "outputs byte-identical to the evidence runs (identity_check.txt)"

export OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4
ARGS=()
for label in "${LABELS[@]}"; do ARGS+=(--batch-dir "$ROOT/$label/$BATCH"); done
PYTHONPATH="$CODE" EQASIM_BS_COMMIT="$COMMIT" "$PYTHON" "$HERE/locate_conservation.py" \
  "${ARGS[@]}" --labels "${LABELS[@]}" --out "$ROOT/located" > "$ROOT/locate.log" 2>&1 \
  || { say "LOCATE FAILED"; exit 1; }
for label in "${LABELS[@]}"; do rm -f "$ROOT/$label/$BATCH/output/pipeline.h5"; done
say "located; stores deleted"
echo done > "$ROOT/LOCATE_DONE"
