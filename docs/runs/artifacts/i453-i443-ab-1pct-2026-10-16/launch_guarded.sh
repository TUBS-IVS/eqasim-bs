#!/usr/bin/env bash
# Launch one arm of the i453/i443 1 % A/B and guard its devalidation list.
# Usage: bash launch_guarded.sh <arm> <worktree dir name> [extra allowed stage names...]
# The guard reads synpp's "Devalidating N stages:" block and kills the run (its own PIDs only) when a stage
# outside the expected recompute set (the MISS list of hash_<arm>.txt plus the extra names) would recompute.
set -euo pipefail
ARM="$1"; WT="$2"; shift 2
EXTRA="$*"
BASE="$HOME/wt-i453-ab"
cd "$BASE/$WT"
mkdir -p "$BASE/logs"
TS=$(date +%Y%m%dT%H%M%S)
LOG="$BASE/logs/run_${ARM}_${TS}.log"
ALLOWED="$BASE/allowed_${ARM}.txt"
grep "^#   MISS" "$BASE/hash_${ARM}.txt" | sed 's/#   MISS //' > "$ALLOWED"
for name in $EXTRA; do echo "$name" >> "$ALLOWED"; done
source "$HOME/miniforge3/etc/profile.d/conda.sh"
conda activate eqasim
export JAVA_HOME="$HOME/tools/jdk-25.0.3+9"
export PATH="$JAVA_HOME/bin:$PATH"
export PYTHONUTF8=1
{
  echo "[i453-ab] arm=$ARM eqasim-bs=$(git rev-parse HEAD) eqasim-java-bs=$(git -C ../eqasim-java-bs rev-parse HEAD) start=$(date -Is)"
  python scripts/run_synpp.py configs/base_bs.yml "../i453_${ARM}.yml"
  echo "[i453-ab] arm=$ARM exit=$? end=$(date -Is)"
} > "$LOG" 2>&1 &
RUN_PID=$!
echo "$RUN_PID" > "$BASE/run_${ARM}.pid"
echo "launched arm=$ARM pid=$RUN_PID log=$LOG"
# Guard: wait for the devalidation block (or the run's end), then compare.
for _ in $(seq 1 600); do
  if grep -q "Devalidating [0-9]* stages:" "$LOG"; then
    sleep 5
    break
  fi
  if ! kill -0 "$RUN_PID" 2>/dev/null; then
    echo "GUARD: run ended before the devalidation block; see $LOG"; exit 1
  fi
  sleep 2
done
awk '/Devalidating [0-9]+ stages:/{f=1; next} f && /- /{sub(/.*- /,""); sub(/__.*/,""); print; next} f{exit}' "$LOG" | sort -u > "$BASE/devalidated_${ARM}.txt"
UNEXPECTED=$(comm -23 "$BASE/devalidated_${ARM}.txt" <(sort -u "$ALLOWED") || true)
echo "devalidated $(wc -l < "$BASE/devalidated_${ARM}.txt") stages; expected set $(sort -u "$ALLOWED" | wc -l)"
if [ -n "$UNEXPECTED" ]; then
  echo "GUARD KILL: unexpected recompute of: $UNEXPECTED"
  pkill -P "$RUN_PID" || true
  kill "$RUN_PID" || true
  sleep 3
  pgrep -f "i453_${ARM}.yml" | xargs -r kill || true
  exit 2
fi
echo "GUARD OK"
