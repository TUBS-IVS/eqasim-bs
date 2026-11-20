#!/usr/bin/env bash
# Post hoc (issue #646): run one controlled-rounding batch again with its checkpoint store kept, check that
# its outputs are byte-identical to the evidence run, and split the rounding shift of one control by 1 km
# parent (locate_rounding_shift.py). The evidence runs deleted their stores after the diagnostics, and the
# per-parent float sums can only be read from a store.
#
# Usage: run_locate.sh <root> <populationsim_project_dir> <code_dir> <commit> <version> <ladder_src> \
#            <reference_batch_dir> <batch>
#   root                 new directory for the batch folder, logs and tables
#   code_dir             eqasim-bs code (git archive) at <commit>
#   version              populationsim_version the folder is prepared for (the installed build must report it)
#   ladder_src           the arm-2 folders the evidence arms were prepared from
#   reference_batch_dir  the batch folder of the evidence run (arm 3) the outputs must equal
#
# The batch is prepared like the arm "controlled_on" of run_evidence.sh: rounding "controlled", both
# run-time options of issue #656 on (the outputs do not depend on them: manifest
# popsim-run-performance-2026-11-18), random_seed 1234. The store is deleted at the end.
set -euo pipefail
ROOT="$1"; PROJECT="$2"; CODE="$3"; COMMIT="$4"; VERSION="$5"; LADDER_SRC="$6"; REFERENCE="$7"; BATCH="$8"
HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHON=~/miniforge3/envs/eqasim/bin/python
[ ! -e "$ROOT" ] || { echo "refuse: $ROOT exists" >&2; exit 1; }
mkdir -p "$ROOT"
say() { echo "$(date +%F_%T) $*" >> "$ROOT/progress.log"; }

say "prepare (code $COMMIT, version $VERSION)"
PYTHONPATH="$CODE" "$PYTHON" "$HERE/prepare_variant.py" --src "$LADDER_SRC" --arm-dir "$ROOT/arm" \
  --batches "$BATCH" --random-seed 1234 --rounding controlled --populationsim-version "$VERSION" \
  --store-positive-rows --fast-paths > "$ROOT/prepare.log" 2>&1
say "start run $BATCH"
"$HERE/run_arm.sh" "$ROOT/arm" "$PROJECT" "$BATCH" > "$ROOT/run.log" 2>&1
say "end run: $(cat "$ROOT/arm/$BATCH.exit")"
[ "$(cat "$ROOT/arm/$BATCH.exit")" = "exit 0" ] || { say "RUN FAILED"; exit 1; }

for name in final_expanded_household_ids.csv synthetic_households.csv synthetic_persons.csv; do
  cmp "$ROOT/arm/$BATCH/output/$name" "$REFERENCE/output/$name" \
    || { say "OUTPUT DIFFERS from the evidence run: $name"; exit 1; }
done
say "outputs byte-identical to $REFERENCE"

export OPENBLAS_NUM_THREADS=4 OMP_NUM_THREADS=4
PYTHONPATH="$CODE" EQASIM_BS_COMMIT="$COMMIT" "$PYTHON" "$HERE/locate_rounding_shift.py" \
  --batch "$ROOT/arm/$BATCH" --out "$ROOT/located" > "$ROOT/locate.log" 2>&1 \
  || { say "LOCATE FAILED"; exit 1; }
rm -f "$ROOT/arm/$BATCH/output/pipeline.h5"
say "located; store deleted"
echo done > "$ROOT/LOCATE_DONE"
