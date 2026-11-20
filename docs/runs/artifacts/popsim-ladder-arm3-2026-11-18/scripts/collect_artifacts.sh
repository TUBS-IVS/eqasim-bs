#!/usr/bin/env bash
# Collect the small text records of the evidence runs of issues #646 and #656 into <root>/collected/:
# run times, peak memory, exit states and output hashes of every arm, the solver-path records, the
# prepare records, the installed tool, the package list of the environment and the fork test summaries.
# Usage: collect_artifacts.sh <root> <arm2_dir> <env_dir> <suite_log_prefix>
#   root              the directory run_evidence.sh wrote
#   arm2_dir          the arm-2 folders (their solver-path records and times are copied for comparison)
#   env_dir           the evidence environment (its .freeze.txt and .install.log lie next to it)
#   suite_log_prefix  path prefix of the three fork test logs (<prefix>_py312.log, _py39.log, _prod.log)
set -euo pipefail
ROOT="$1"; ARM2="$2"; ENVDIR="$3"; SUITES="$4"
OUT="$ROOT/collected"
[ ! -e "$OUT" ] || { echo "refuse: $OUT exists" >&2; exit 1; }
mkdir -p "$OUT/integerizer_paths" "$OUT/prepare"
{
  for arm in round_off round_on controlled_off controlled_on controlled_off_repeat \
             round_off_b028 round_on_b028 controlled_off_b028 controlled_on_b028; do
    for time_file in "$ROOT/$arm"/batch_*.time; do
      batch="$(basename "$time_file" .time)"
      echo "== $arm $batch"
      grep -E "Elapsed|Maximum resident" "$time_file"
      cat "$ROOT/$arm/$batch.exit"
      echo "checkpoint store bytes: $(cat "$ROOT/$arm/$batch.store_bytes" 2>/dev/null)"
    done
    cat "$ROOT/$arm/outputs_sha256.txt"
  done
  for batch in batch_000 batch_032; do
    echo "== arm 2 (popsim-ladder-arm2-2026-11-17) $batch"
    grep -E "Elapsed|Maximum resident" "$ARM2/$batch.time"
    sha256sum "$ARM2/$batch/output/final_expanded_household_ids.csv"
  done
  for diag in diag_round_off diag_controlled_off diag_controlled_on; do
    echo "== $diag"
    grep -E "Elapsed|Maximum resident" "$ROOT/$diag.time"
    tail -1 "$ROOT/$diag.log"
  done
} > "$OUT/run_times_and_hashes.txt"
for arm in round_off controlled_off controlled_off_repeat; do
  for record in "$ROOT/$arm"/batch_*/output/integerizer_paths.csv; do
    batch="$(basename "$(dirname "$(dirname "$record")")")"
    cp "$record" "$OUT/integerizer_paths/${arm}_${batch}.csv"
  done
done
for batch in batch_000 batch_032; do
  cp "$ARM2/$batch/output/integerizer_paths.csv" "$OUT/integerizer_paths/arm2_${batch}.csv"
done
for arm in round_off round_on controlled_off controlled_on controlled_off_repeat \
           round_off_b028 round_on_b028 controlled_off_b028 controlled_on_b028; do
  cp "$ROOT/$arm/prepare_variant.json" "$OUT/prepare/prepare_variant_$arm.json"
done
cp "$ROOT/progress.log" "$OUT/progress.log"
cp "$ROOT/controlled_off/tool.json" "$OUT/tool.json"
cp "$ENVDIR.freeze.txt" "$OUT/environment_packages.txt"
tail -3 "$ENVDIR.install.log" > "$OUT/environment_install.txt"
{
  for env in py312 py39 prod; do
    log="${SUITES}_$env.log"
    echo "== $env"
    echo "fork commit: $(head -1 "$log")"
    grep -a -m1 -E "ortools [0-9]" "$log"
    grep -a -E "^[0-9]+ passed|passed|failed" "$log" | tail -1
    tail -1 "$log"
  done
} > "$OUT/fork_test_suites.txt"
ls -R "$OUT" | head -60
