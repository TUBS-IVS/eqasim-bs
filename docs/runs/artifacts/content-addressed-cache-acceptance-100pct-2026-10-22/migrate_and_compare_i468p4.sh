#!/usr/bin/env bash
# #468 Task 15 Step 4: migration acceptance on the cp -a copy of the 100 % cache.
# A = own-reason stale set under the pre-refactor REV (synpp 1.6.2 rules, md5 module hashes, timestamps),
# B = own-reason stale set after scripts/migrate_cache_metadata.py under the PR head (fork rules).
# Acceptance: A == B (the migration makes nothing stale that the old code would not recompute anyway).
# Usage: bash migrate_and_compare_i468p4.sh   (after setup_i468p4.sh)
set -uo pipefail
ROOT=$HOME/i468p4
OUT=$ROOT/out
OV=eqasim-data/i468p4_100pct.yml
mkdir -p "$OUT"
source "$HOME/miniforge3/etc/profile.d/conda.sh" && conda activate eqasim
export JAVA_HOME=$HOME/tools/jdk-25.0.3+9
export PATH=$JAVA_HOME/bin:$HOME/.local/bin:$PATH
step() { echo "[i468p4] $1 $(date -Is)"; }

step "A: explainer at REV start"
( cd "$ROOT/eqasim-bs-rev" && python scripts/explain_stale_stages.py configs/base_bs.yml "$OV" \
    --json "$OUT/A_rev.json" --expect "" > "$OUT/A_rev.txt" 2> "$OUT/A_rev.err" ); echo "A exit=$?"
step "migration dry-run start"
( cd "$ROOT/eqasim-bs" && python scripts/migrate_cache_metadata.py configs/base_bs.yml "$OV" --dry-run \
    > "$OUT/migrate_dry.txt" 2>&1 ); echo "dry-run exit=$?"
step "migration start"
( cd "$ROOT/eqasim-bs" && python scripts/migrate_cache_metadata.py configs/base_bs.yml "$OV" \
    > "$OUT/migrate.txt" 2>&1 ); echo "migrate exit=$?"
step "B: explainer at head start"
( cd "$ROOT/eqasim-bs" && python scripts/explain_stale_stages.py configs/base_bs.yml "$OV" \
    --json "$OUT/B_head.json" --expect "" > "$OUT/B_head.txt" 2> "$OUT/B_head.err" ); echo "B exit=$?"
step "compare"
python "$ROOT/compare_stale_sets_i468p4.py" "$OUT/A_rev.json" "$OUT/B_head.json" | tee "$OUT/compare_A_B.txt"
step "done"
