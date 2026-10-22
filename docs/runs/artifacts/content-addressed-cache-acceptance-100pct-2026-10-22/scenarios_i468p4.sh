#!/usr/bin/env bash
# #468 Task 18 Step 3: server scenarios on the MIGRATED cp -a copy of the 100 % cache (after
# migrate_and_compare_i468p4.sh). The 100 % copy predates the branch, so 72 of its 107 stages are stale for
# own reasons anyway; the scenarios therefore use the cheap Java runtime subgraph (matsim.runtime.java/git/
# maven -> matsim.runtime.eqasim, braunschweig.freight.extraction_tool), which S0 makes valid under the fork.
#   S0  launch the runtime targets (fork run, provenance tracked)            -> explainer E0
#   S1  comment edits in matsim/runtime/java.py and secondary_chainsolvers/escort.py -> E1 == E0
#   S2  behaviour-neutral body edit in matsim/runtime/java.py -> E2: exactly matsim.runtime.java stale;
#       gated launch (synpp_expected_stale_stages [matsim.runtime.java]): java re-executes, its output is
#       unchanged, the jar stages stay cached (early cutoff); revert -> java stale again -> launch -> same
#   S3  Java tree replaced by a cp -a copy with every file touched (new mtimes) -> E3: both jar stages HIT
#   S4  explicit export of the two jar stages into a scratch store, prime a FRESH working directory, explainer
#       (jar stages STALE-IF: their parents are recomputed there), launch: the jars are not rebuilt
# Usage: bash scenarios_i468p4.sh
set -uo pipefail
ROOT=$HOME/i468p4
WT=$ROOT/eqasim-bs
OUT=$ROOT/out
FULL=eqasim-data/i468p4_100pct.yml
mkdir -p "$OUT"
source "$HOME/miniforge3/etc/profile.d/conda.sh" && conda activate eqasim
export JAVA_HOME=$HOME/tools/jdk-25.0.3+9
export PATH=$JAVA_HOME/bin:$HOME/.local/bin:$PATH
cd "$WT"
step() { echo "[i468p4] $1 $(date -Is)"; }
explain() {  # explain <name> [overlay]
    python scripts/explain_stale_stages.py configs/base_bs.yml "${2:-$FULL}" --json "$OUT/$1.json" \
        > "$OUT/$1.txt" 2> "$OUT/$1.err"; echo "$1 exit=$? $(grep '^# [0-9]* stages' "$OUT/$1.txt")"
}
launch() {  # launch <name> <overlay>
    python scripts/run_synpp.py configs/base_bs.yml "$2" > "$OUT/$1.log" 2>&1; echo "$1 exit=$?"
    grep -E "Executing stage|unchanged|devalidat|early cutoff kept|Stale before execution|legacy timestamp rule|aborting|Traceback|Error" \
        "$OUT/$1.log" | sed 's/^.*\(synpp\|braunschweig\)[^ ]* //' | head -40
}
compare() { python "$ROOT/compare_stale_sets_i468p4.py" "$OUT/$1.json" "$OUT/$2.json" > "$OUT/compare_$1_$2.txt"; \
    echo "compare $1 $2 exit=$? $(sed -n 2p "$OUT/compare_$1_$2.txt")"; grep -E "only " "$OUT/compare_$1_$2.txt"; }
JAVA_PY=matsim/runtime/java.py
ESCORT=braunschweig/synthesis/locations/secondary_chainsolvers/escort.py
test -z "$(git status --porcelain -- "$JAVA_PY" "$ESCORT")" || { echo "dirty probe files" >&2; exit 1; }
python "$ROOT/make_runtime_overlays_i468p4.py" "$WT" > "$OUT/overlays.txt"

step "S0 launch runtime targets"; launch S0_runtime eqasim-data/i468p4_runtime.yml
explain E0

step "S1 comment edits"
printf '\n# probe comment (i468 S1)\n' >> "$JAVA_PY"; printf '\n# probe comment (i468 S1)\n' >> "$ESCORT"
explain E1; compare E0 E1
git checkout -- "$JAVA_PY" "$ESCORT"

step "S2 body edit"
printf '\nJAVA_PROBE = 1\n' >> "$JAVA_PY"
explain E2; compare E0 E2
launch S2_gated eqasim-data/i468p4_runtime_gate.yml
explain E2b; compare E0 E2b
git checkout -- "$JAVA_PY"
explain E2c; compare E0 E2c
launch S2_revert eqasim-data/i468p4_runtime.yml
explain E2d; compare E0 E2d

step "S3 Java tree cp -a + touch"
JAVA_TREE=$ROOT/eqasim-java-bs
mv "$JAVA_TREE" "$ROOT/eqasim-java-bs.orig"
cp -a "$ROOT/eqasim-java-bs.orig" "$JAVA_TREE"
find "$JAVA_TREE" -type f -not -path "*/.git/*" -exec touch {} +
explain E3; compare E0 E3
python - "$OUT/E3.json" <<'EOF'
import json, sys
report = json.load(open(sys.argv[1]))
for name in ("matsim.runtime.eqasim", "braunschweig.freight.extraction_tool"):
    print(f"S3 {name}: reasons={report[name]['reasons']} stale_if={report[name]['stale_if']}")
EOF

step "S4 export + prime fresh directory"
rm -rf eqasim-data/cache_i468p4_store eqasim-data/cache_i468p4_fresh
mkdir -p eqasim-data/cache_i468p4_store eqasim-data/cache_i468p4_fresh
python scripts/cache_share.py export --working-directory eqasim-data/cache_bs_100pct_allfeat_popsim \
    --store eqasim-data/cache_i468p4_store --modules matsim.runtime.eqasim,braunschweig.freight.extraction_tool \
    > "$OUT/S4_export.txt" 2>&1; echo "export exit=$?"; cat "$OUT/S4_export.txt"
python scripts/cache_share.py prime --working-directory eqasim-data/cache_i468p4_fresh \
    --store eqasim-data/cache_i468p4_store --modules matsim.runtime.eqasim,braunschweig.freight.extraction_tool \
    > "$OUT/S4_prime.txt" 2>&1; echo "prime exit=$?"; cat "$OUT/S4_prime.txt"
explain E4 eqasim-data/i468p4_fresh.yml
grep -E "matsim.runtime.eqasim|extraction_tool|^# [0-9]* stages" "$OUT/E4.txt"
launch S4_fresh eqasim-data/i468p4_fresh.yml
explain E4b eqasim-data/i468p4_fresh.yml
step "done"
