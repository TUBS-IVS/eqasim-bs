#!/usr/bin/env bash
# #468 Task 18: early-cutoff scenarios on real stages, after scenarios_i468p4.sh.
# scenarios_i468p4.sh launched the jar stages as RUN TARGETS, which the launcher always re-executes
# (rerun_required=True), so no early cutoff could show there. This script runs the same targets through
# run_targets_cached_i468p4.py (fork run, rerun_required=False, provenance tracked):
#   X2  behaviour-neutral body edit in matsim/runtime/java.py: java re-executes; its output is unchanged,
#       so matsim.runtime.eqasim and braunschweig.freight.extraction_tool stay cached; revert -> same again
#   X4  prime a second FRESH working directory from the scratch store with the two jar stages: their
#       parents (java, git, maven) execute there, the jars are NOT rebuilt (decided by digest, R17-3)
# Usage: bash cutoff_scenarios_i468p4.sh
set -uo pipefail
ROOT=$HOME/i468p4
WT=$ROOT/eqasim-bs
OUT=$ROOT/out
FULL=eqasim-data/i468p4_100pct.yml
source "$HOME/miniforge3/etc/profile.d/conda.sh" && conda activate eqasim
export JAVA_HOME=$HOME/tools/jdk-25.0.3+9
export PATH=$JAVA_HOME/bin:$HOME/.local/bin:$PATH
cd "$WT"
step() { echo "[i468p4] $1 $(date -Is)"; }
explain() {  # explain <name> [overlay]
    python scripts/explain_stale_stages.py configs/base_bs.yml "${2:-$FULL}" --json "$OUT/$1.json" \
        > "$OUT/$1.txt" 2> "$OUT/$1.err"; echo "$1 exit=$? $(grep '^# [0-9]* stages' "$OUT/$1.txt")"
}
drive() {  # drive <name> <overlay>
    python "$ROOT/run_targets_cached_i468p4.py" configs/base_bs.yml "$2" > "$OUT/$1.log" 2>&1; echo "$1 exit=$?"
    grep -E "Executing stage|unchanged|devalidat|early cutoff kept|Stale before|legacy timestamp|- executed|- kept|Traceback|Error" \
        "$OUT/$1.log" | sed 's/^.*\(INFO\|WARNING\|ERROR\) [^ ]* //' | head -30
}
digests() {  # digests <label> <working dir>
    python - "$1" "$2" <<'EOF'
import json, sys
label, wd = sys.argv[1:3]
meta = json.load(open(f"{wd}/pipeline.json"))
for key, record in sorted(meta.items()):
    if key.split("__")[0] in ("matsim.runtime.java", "matsim.runtime.eqasim", "braunschweig.freight.extraction_tool"):
        digest = (record.get("output_hash") or {}).get("digest")
        print(f"{label} {key} updated={record.get('updated')} digest={digest}")
EOF
}
JAVA_PY=matsim/runtime/java.py
COPY=eqasim-data/cache_bs_100pct_allfeat_popsim
test -z "$(git status --porcelain -- "$JAVA_PY")" || { echo "dirty probe file" >&2; exit 1; }

step "X2 body edit, cached targets"
digests before "$COPY"
printf '\nJAVA_PROBE = 1\n' >> "$JAVA_PY"
explain X2_pre eqasim-data/i468p4_runtime.yml; grep -E "^(STALE|STALE-IF|HIT|RECOMPUTE) " "$OUT/X2_pre.txt"
drive X2_run eqasim-data/i468p4_runtime.yml
digests after_edit "$COPY"
git checkout -- "$JAVA_PY"
drive X2_revert eqasim-data/i468p4_runtime.yml
digests after_revert "$COPY"
explain X2_post eqasim-data/i468p4_runtime.yml; grep -E "^(STALE|STALE-IF|HIT|RECOMPUTE) " "$OUT/X2_post.txt"

step "X4 prime a second fresh directory"
sed 's#eqasim-data/cache_i468p4_fresh#eqasim-data/cache_i468p4_fresh2#' eqasim-data/i468p4_fresh.yml > eqasim-data/i468p4_fresh2.yml
rm -rf eqasim-data/cache_i468p4_fresh2 && mkdir -p eqasim-data/cache_i468p4_fresh2
python scripts/cache_share.py prime --working-directory eqasim-data/cache_i468p4_fresh2 \
    --store eqasim-data/cache_i468p4_store --modules matsim.runtime.eqasim,braunschweig.freight.extraction_tool \
    > "$OUT/X4_prime.txt" 2>&1; echo "prime exit=$?"; grep -E "prime" "$OUT/X4_prime.txt"
explain X4_pre eqasim-data/i468p4_fresh2.yml; grep -E "^(STALE|STALE-IF|HIT|RECOMPUTE) " "$OUT/X4_pre.txt"
drive X4_run eqasim-data/i468p4_fresh2.yml
explain X4_post eqasim-data/i468p4_fresh2.yml; grep -E "^(STALE|STALE-IF|HIT|RECOMPUTE) " "$OUT/X4_post.txt"
step "done"
