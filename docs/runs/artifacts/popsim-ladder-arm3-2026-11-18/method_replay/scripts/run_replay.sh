#!/usr/bin/env bash
# Replay captured simultaneous-integerizer inputs with the three rounding methods (issue #646), one
# process per batch, with the PopulationSim build installed in <populationsim_project_dir>.
# Usage: run_replay.sh <populationsim_project_dir> <out_dir> <instances_dir>=<random seed> [...]
#   <random seed> is the INTEGERIZER_RANDOM_SEED of the batch the instances were captured from.
# Writes <out_dir>/replay_<instances dir name>.csv and .log per batch, tool.json (the installed package),
# method_summary.csv and method_summary.txt (summarise_methods.py over all batches).
set -euo pipefail
PROJECT="$1"; OUT="$2"; shift 2
HERE="$(cd "$(dirname "$0")" && pwd)"
UV="$HOME/.local/bin/uv"
[ ! -e "$OUT" ] || { echo "refuse: $OUT exists" >&2; exit 1; }
mkdir -p "$OUT"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
cd "$PROJECT"
"$UV" run --no-sync python -c "import importlib.metadata as m, json; d=m.distribution('populationsim'); print(json.dumps({'populationsim': d.version, 'direct_url': d.read_text('direct_url.json')}))" > "$OUT/tool.json"
for spec in "$@"; do
  dir="${spec%%=*}"; seed="${spec##*=}"; name="$(basename "$dir")"
  ( "$UV" run --no-sync python "$HERE/replay_methods.py" --instances "$dir" --out "$OUT/replay_$name.csv" \
      --random-seed "$seed" > "$OUT/replay_$name.log" 2>&1; echo "exit $?" >> "$OUT/replay_$name.log" ) &
done
wait
"$UV" run --no-sync python "$HERE/summarise_methods.py" "$OUT/method_summary.csv" "$OUT"/replay_*.csv \
  > "$OUT/method_summary.txt" 2>&1
echo done > "$OUT/REPLAY_DONE"
