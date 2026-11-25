#!/usr/bin/env bash
# Driver of the #677 measurement: batch_028 three rounds, batch_000 one round (arms A, B, C start together),
# then one untimed pyinstrument profile of arm A on batch_028 (per-function view only).
set -uo pipefail
ROOT="$HOME/i677"
B28="$HOME/i646/ev/controlled_on_b028/batch_028"
B00="$HOME/i646/ev/controlled_on/batch_000"
for r in 1 2 3; do
  "$ROOT/scripts/run_round.sh" "$B28" b028 "$r" A B C
done
"$ROOT/scripts/run_round.sh" "$B00" b000 1 A B C
# profile run (not timed)
PY="$HOME/i646/env_bs2/.venv/bin/python"
d="$ROOT/runs/b028_Aprofile"
mkdir -p "$d"; cp -r "$B28/configs" "$B28/data" "$d/"; mkdir "$d/output"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
( cd "$d" && PYTHONPATH="$ROOT/trees/A" "$PY" -m pyinstrument -r pyisession -o "$ROOT/profile_A_b028.pyisession" -m populationsim -w "$d" > "$d.stdout" 2>&1; echo "exit $?" > "$d.exit" )
"$PY" -m pyinstrument --load "$ROOT/profile_A_b028.pyisession" -r html -o "$ROOT/profile_A_b028.html"
"$PY" -m pyinstrument --load "$ROOT/profile_A_b028.pyisession" -r text --no-color --no-unicode -o "$ROOT/profile_A_b028.txt"
echo done > "$ROOT/ALL_DONE"
