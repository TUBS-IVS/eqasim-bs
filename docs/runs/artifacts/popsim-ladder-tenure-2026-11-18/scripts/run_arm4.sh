#!/usr/bin/env bash
# Prepare, run and compare ladder arm 4 of issue #657 (the tenure rule): arm 3 with only the two tenure
# control columns rebuilt from the prepared cell grid.
#
# Usage: run_arm4.sh <root> <populationsim_project_dir> <code_dir> <commit> <arm3_work_dir> \
#            <arm3_diag_dir> <arm3_scripts_dir> <cells_parquet> <mid_households_csv> [<batch> ...]
#   root             new directory for the arm-4 folders, the prepare logs, the diagnostics and the comparison
#   code_dir         eqasim-bs code (git archive) at <commit>; the scripts import it
#   arm3_work_dir    the arm-3 batch folders (with their outputs)
#   arm3_diag_dir    the arm-3 diagnostics (with --misfit) of the same batches
#   arm3_scripts_dir the scripts of popsim-ladder-arm3-2026-11-18 (run_arm.sh and run_diag.sh are reused, not copied)
#   batches          default: batch_000 batch_032
# Steps: (1) self-check: --rule off into <root>/selfcheck must reproduce the arm-3 folders exactly, else stop;
# (2) --rule on into <root>/arm_4; (3) PopulationSim; (4) diagnostics; (5) comparison into <root>/comparison.
# The prepare logs are <root>/prepare_logs/<batch>.rule_off.json and <batch>.rule_on.json.
# Every step appends one line to <root>/progress.log; <root>/ARM4_DONE marks the end.
set -uo pipefail
ROOT="$1"; PROJECT="$2"; CODE="$3"; COMMIT="$4"; ARM3_WORK="$5"; ARM3_DIAG="$6"; ARM3_SCRIPTS="$7"
CELLS="$8"; MID="$9"; shift 9
BATCHES=("$@")
[ "${#BATCHES[@]}" -gt 0 ] || BATCHES=(batch_000 batch_032)
HERE="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${PYTHON:-$HOME/miniforge3/envs/eqasim/bin/python}"
[ ! -e "$ROOT" ] || { echo "refuse: $ROOT exists" >&2; exit 1; }
mkdir -p "$ROOT/prepare_logs"
say() { echo "$(date +%F_%T) $*" >> "$ROOT/progress.log"; }
export PYTHONPATH="$CODE" EQASIM_BS_COMMIT="$COMMIT"

say "self-check (rule off), code $COMMIT"
for b in "${BATCHES[@]}"; do
  "$PYTHON" "$HERE/prepare_tenure_arm.py" --base "$ARM3_WORK/$b" --out "$ROOT/selfcheck/$b" \
    --cells-100m "$CELLS" --rule off --log "$ROOT/prepare_logs/$b.rule_off.json" \
    > "$ROOT/prepare_logs/$b.rule_off.stdout" 2>&1 \
    || { say "SELF-CHECK FAILED $b: --rule off does not reproduce the arm-3 folder"; exit 1; }
done
say "prepare arm 4 (rule on)"
for b in "${BATCHES[@]}"; do
  "$PYTHON" "$HERE/prepare_tenure_arm.py" --base "$ARM3_WORK/$b" --out "$ROOT/arm_4/$b" \
    --cells-100m "$CELLS" --log "$ROOT/prepare_logs/$b.rule_on.json" \
    > "$ROOT/prepare_logs/$b.rule_on.stdout" 2>&1 \
    || { say "PREPARE FAILED $b"; exit 1; }
done
say "start PopulationSim ${BATCHES[*]}"
"$ARM3_SCRIPTS/run_arm.sh" "$ROOT/arm_4" "$PROJECT" "${BATCHES[@]}" > "$ROOT/arm_4.run.log" 2>&1
say "end PopulationSim: $(cat "$ROOT"/arm_4/*.exit 2>/dev/null | tr '\n' ' ')"
say "start diagnostics"
"$ARM3_SCRIPTS/run_diag.sh" "$ROOT/arm_4" "$ROOT/diag_arm_4" "$CODE" "$COMMIT" "$MID" "${BATCHES[@]}" \
  > "$ROOT/diag_arm_4.launch.log" 2>&1
say "end diagnostics: $(cat "$ROOT/diag_arm_4.DONE" 2>/dev/null)"
"$PYTHON" "$HERE/compare_tenure_arm.py" --base-diag "$ARM3_DIAG" --new-diag "$ROOT/diag_arm_4" \
  --base-work "$ARM3_WORK" --new-work "$ROOT/arm_4" --prepare-logs "$ROOT/prepare_logs" \
  --batches "${BATCHES[@]}" --out "$ROOT/comparison" > "$ROOT/comparison.log" 2>&1
say "comparison rc=$?"
echo done > "$ROOT/ARM4_DONE"
