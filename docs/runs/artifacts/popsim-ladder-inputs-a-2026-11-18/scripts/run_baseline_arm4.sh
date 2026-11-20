#!/usr/bin/env bash
# Baseline runs of issue #658 (Part A, Task A8): ladder arm 4 for batch_013 and batch_028.
#
# Arm 4 (popsim-ladder-tenure-2026-11-18) exists for batch_000 and batch_032 only. The two other batches
# of the Part A evidence are prepared the way the earlier records prepared their folders, with the
# committed scripts of those records, in this order:
#   arm 1  popsim-ladder-arm1-2026-11-17/scripts/prepare_arm.py     (gumbel draw, fractional weights, 7200 s, seed 1234)
#   arm 2  popsim-ladder-arm2-2026-11-17/scripts/prepare_arm2.py    (seed 1234)
#   arm 3  popsim-ladder-arm3-2026-11-18/scripts/prepare_variant.py (controlled rounding, search, run-time options off)
#   arm 4  popsim-ladder-tenure-2026-11-18/scripts/prepare_tenure_arm.py (self-check with --rule off first)
# batch_028 already has an arm-3 folder (popsim-run-performance-2026-11-18); the chain is run for it too
# and must reproduce that folder file for file (sha256 of configs/ and data/), which checks the chain
# before it is trusted for batch_013. Then PopulationSim runs on both arm-4 folders (run_arm.sh of the
# arm-3 record, checkpoint stores kept) and the diagnostics with --misfit (run_diag.sh of the arm-3 record,
# which deletes the stores after a successful run). No code of Part A takes part in these runs.
#
# Usage: run_baseline_arm4.sh <root> <code_dir> <commit> <prod_work_dir> <arm3_b028_dir> \
#            <populationsim_project_dir> <cells_parquet> <mid_households_csv>
# Every step appends one line to <root>/progress.log; <root>/BASELINE_DONE marks the end.
set -uo pipefail
ROOT="$1"; CODE="$2"; COMMIT="$3"; PROD="$4"; ARM3_B028="$5"; PROJECT="$6"; CELLS="$7"; MID="$8"; CODE1="$9"; CODE2="${10}"; CODE3="${11}"
BATCHES=(batch_013 batch_028)
ART="$CODE/docs/runs/artifacts"
PYTHON="${PYTHON:-$HOME/miniforge3/envs/eqasim/bin/python}"
[ ! -e "$ROOT" ] || { echo "refuse: $ROOT exists" >&2; exit 1; }
mkdir -p "$ROOT/logs"
say() { echo "$(date +%F_%T) $*" >> "$ROOT/progress.log"; }
fail() { say "FAILED: $*"; echo done > "$ROOT/BASELINE_DONE"; exit 1; }
export PYTHONPATH="$CODE" EQASIM_BS_COMMIT="$COMMIT" OPENBLAS_NUM_THREADS=2
cd "$HOME"

say "arm 1 preparation (code $CODE1); arm 4 and diagnostics: code $COMMIT"
PYTHONPATH="$CODE1" "$PYTHON" "$ART/popsim-ladder-arm1-2026-11-17/scripts/prepare_arm.py" --src "$PROD" --arm-dir "$ROOT/arm1" \
  --batches "${BATCHES[@]}" --draw gumbel --keep-fractional --timeout 7200 --random-seed 1234 \
  > "$ROOT/logs/arm1.log" 2>&1 || fail "arm 1 preparation"
say "arm 2 preparation"
PYTHONPATH="$CODE2" "$PYTHON" "$ART/popsim-ladder-arm2-2026-11-17/scripts/prepare_arm2.py" --src "$ROOT/arm1" --arm-dir "$ROOT/arm2" \
  --batches "${BATCHES[@]}" --random-seed 1234 > "$ROOT/logs/arm2.log" 2>&1 || fail "arm 2 preparation"
say "arm 3 preparation"
PYTHONPATH="$CODE3" "$PYTHON" "$ART/popsim-ladder-arm3-2026-11-18/scripts/prepare_variant.py" --src "$ROOT/arm2" --arm-dir "$ROOT/arm3" \
  --batches "${BATCHES[@]}" --random-seed 1234 --rounding controlled --controlled-method search \
  --populationsim-version 0.10.0+bs.2 --timeout 7200 > "$ROOT/logs/arm3.log" 2>&1 || fail "arm 3 preparation"

say "reproduction check: the chain's batch_028 against the arm-3 folder of popsim-run-performance-2026-11-18"
( cd "$ROOT/arm3/batch_028" && find configs data -type f | sort | xargs sha256sum ) > "$ROOT/logs/b028_chain.sha256"
( cd "$ARM3_B028" && find configs data -type f | sort | xargs sha256sum ) > "$ROOT/logs/b028_recorded.sha256"
if ! diff "$ROOT/logs/b028_recorded.sha256" "$ROOT/logs/b028_chain.sha256" > "$ROOT/logs/b028_reproduction.diff"; then
  fail "the chain does not reproduce the recorded arm-3 folder of batch_028 (logs/b028_reproduction.diff)"
fi
say "reproduction check passed (all configs/ and data/ files identical)"

T="$ART/popsim-ladder-tenure-2026-11-18/scripts/prepare_tenure_arm.py"
say "arm 4 self-check (rule off) and preparation (rule on)"
for b in "${BATCHES[@]}"; do
  "$PYTHON" "$T" --base "$ROOT/arm3/$b" --out "$ROOT/selfcheck/$b" --cells-100m "$CELLS" --rule off \
    --log "$ROOT/logs/$b.rule_off.json" > "$ROOT/logs/$b.rule_off.stdout" 2>&1 || fail "self-check $b"
  "$PYTHON" "$T" --base "$ROOT/arm3/$b" --out "$ROOT/arm_4/$b" --cells-100m "$CELLS" \
    --log "$ROOT/logs/$b.rule_on.json" > "$ROOT/logs/$b.rule_on.stdout" 2>&1 || fail "arm 4 preparation $b"
done

say "start PopulationSim ${BATCHES[*]}"
bash "$ART/popsim-ladder-arm3-2026-11-18/scripts/run_arm.sh" "$ROOT/arm_4" "$PROJECT" "${BATCHES[@]}" \
  > "$ROOT/logs/arm_4.run.log" 2>&1
say "end PopulationSim: $(cat "$ROOT"/arm_4/*.exit 2>/dev/null | tr '\n' ' ')"
say "start diagnostics"
bash "$ART/popsim-ladder-arm3-2026-11-18/scripts/run_diag.sh" "$ROOT/arm_4" "$ROOT/diag_arm_4" "$CODE" "$COMMIT" \
  "$MID" "${BATCHES[@]}" > "$ROOT/logs/diag_arm_4.launch.log" 2>&1
say "end diagnostics: $(cat "$ROOT/diag_arm_4.DONE" 2>/dev/null)"
echo done > "$ROOT/BASELINE_DONE"
