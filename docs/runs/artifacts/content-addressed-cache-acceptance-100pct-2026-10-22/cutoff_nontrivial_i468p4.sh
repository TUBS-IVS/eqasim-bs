#!/usr/bin/env bash
# #468 Task 18: early cutoff behind a NON-TRIVIAL parent output (follow-up of cutoff_scenarios_i468p4.sh,
# whose re-executed parents matsim.runtime.java/git/maven have a 4-byte output). On the migrated copy, the
# pickle of data.hts.entd.raw (output hash over ~20 MB, migrated record) is removed, so the parent is stale
# (cache_missing) and re-executes; its child data.hts.entd.cleaned (migrated record that carries the parent's
# digest) must stay cached when the new output digest equals the recorded one. Run through
# run_targets_cached_i468p4.py (rerun_required=False), target data.hts.entd.cleaned.
# Usage: bash cutoff_nontrivial_i468p4.sh
set -uo pipefail
ROOT=$HOME/i468p4
WT=$ROOT/eqasim-bs
OUT=$ROOT/out
source "$HOME/miniforge3/etc/profile.d/conda.sh" && conda activate eqasim
cd "$WT"
COPY=eqasim-data/cache_bs_100pct_allfeat_popsim
step() { echo "[i468p4] $1 $(date -Is)"; }
python - "$WT" <<'EOF'
import copy, sys, yaml
worktree = sys.argv[1]
doc = yaml.safe_load(open(f"{worktree}/eqasim-data/i468p4_runtime.yml", encoding="utf-8"))
doc["run"] = ["data.hts.entd.cleaned"]
yaml.safe_dump(doc, open(f"{worktree}/eqasim-data/i468p4_entd.yml", "w", encoding="utf-8"), sort_keys=False)
EOF
digests() {
    python - "$1" "$COPY" <<'EOF'
import json, sys
label, wd = sys.argv[1:3]
meta = json.load(open(f"{wd}/pipeline.json"))
for key, record in sorted(meta.items()):
    if key.split("__")[0] in ("data.hts.entd.raw", "data.hts.entd.cleaned") and "__" in key:
        oh = record.get("output_hash") or {}
        print(f"{label} {key} updated={record.get('updated')} digest={oh.get('digest')} bytes={oh.get('bytes')} "
              f"recorded_parent_digests={record.get('dependency_output_hashes')}")
EOF
}
step "X5 remove the pickle of data.hts.entd.raw"
digests before
RAW_P=$(ls $COPY/data.hts.entd.raw__*.p); echo "removing $RAW_P"; rm -f "$RAW_P"
python scripts/explain_stale_stages.py configs/base_bs.yml eqasim-data/i468p4_entd.yml > "$OUT/X5_pre.txt" 2>&1
grep -E "^(STALE|STALE-IF|HIT|RECOMPUTE) |^# [0-9]* stages" "$OUT/X5_pre.txt"
python "$ROOT/run_targets_cached_i468p4.py" configs/base_bs.yml eqasim-data/i468p4_entd.yml > "$OUT/X5_run.log" 2>&1; echo "X5_run exit=$?"
grep -E "Executing stage|Output hash for|unchanged|devalidat|early cutoff kept|Stale before|- executed|- kept|Traceback|Error" \
    "$OUT/X5_run.log" | sed 's/^.*\(INFO\|WARNING\|ERROR\) [^ ]* //' | tee "$OUT/X5_run_excerpt.txt"
digests after
python scripts/explain_stale_stages.py configs/base_bs.yml eqasim-data/i468p4_entd.yml > "$OUT/X5_post.txt" 2>&1
grep -E "^(STALE|STALE-IF|HIT|RECOMPUTE) |^# [0-9]* stages" "$OUT/X5_post.txt"
step "done"
