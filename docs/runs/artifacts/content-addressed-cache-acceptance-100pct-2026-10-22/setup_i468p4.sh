#!/usr/bin/env bash
# Set up the #468 Phase 3/4 server acceptance on felix (copy only, production caches untouched).
# Layout ~/i468p4/{eqasim-bs (PR head), eqasim-bs-rev (pre-refactor REV), eqasim-java-bs (fork main)}, all
# detached worktrees, so the base value eqasim_source_path ../eqasim-java-bs resolves to the Java worktree
# from both checkouts. The 100 % working directory is a physical copy (cp -a, never cp -al) under the SAME
# relative name in the head worktree, so working_directory / analysis_working_directory strings and with
# them the stage hashes stay unchanged; the REV worktree reaches the same copy through a symlink (read-only
# use there: only the explainer runs at REV).
# Usage: bash setup_i468p4.sh <eqasim-bs head ref> <eqasim-bs REV> <eqasim-java-bs ref>
set -euo pipefail
ROOT=$HOME/i468p4
HEAD_REF=$1; REV=$2; JAVA_REF=$3
CACHE=cache_bs_100pct_allfeat_popsim
mkdir -p "$ROOT/logs"
git -C "$HOME/eqasim-bs" fetch -q origin chore/i468-early-cutoff main
git -C "$HOME/eqasim-java-bs" fetch -q origin
test -e "$ROOT/eqasim-bs" || git -C "$HOME/eqasim-bs" worktree add --detach "$ROOT/eqasim-bs" "$HEAD_REF"
test -e "$ROOT/eqasim-bs-rev" || git -C "$HOME/eqasim-bs" worktree add --detach "$ROOT/eqasim-bs-rev" "$REV"
test -e "$ROOT/eqasim-java-bs" || git -C "$HOME/eqasim-java-bs" worktree add --detach "$ROOT/eqasim-java-bs" "$JAVA_REF"
FARM="$ROOT/eqasim-bs/docs/runs/artifacts/freight-boundary-links-ab-1pct-2026-10-16/symlink_farm.py"
python3 "$FARM" "$HOME/eqasim-bs/eqasim-data" "$ROOT/eqasim-bs/eqasim-data"
python3 "$FARM" "$HOME/eqasim-bs/eqasim-data" "$ROOT/eqasim-bs-rev/eqasim-data"
if [ ! -e "$ROOT/eqasim-bs/eqasim-data/$CACHE" ]; then
    echo "[i468p4] cp -a $CACHE start $(date -Is)"
    cp -a "$HOME/eqasim-bs/eqasim-data/$CACHE" "$ROOT/eqasim-bs/eqasim-data/$CACHE"
    echo "[i468p4] cp -a $CACHE done $(date -Is)"
fi
test -e "$ROOT/eqasim-bs-rev/eqasim-data/$CACHE" || ln -s "$ROOT/eqasim-bs/eqasim-data/$CACHE" "$ROOT/eqasim-bs-rev/eqasim-data/$CACHE"
source "$HOME/miniforge3/etc/profile.d/conda.sh" && conda activate eqasim
for WT in "$ROOT/eqasim-bs" "$ROOT/eqasim-bs-rev"; do
    # The helper scripts are copied next to the worktrees (they are committed after the run).
    python "$ROOT/make_overlay_i468p4.py" "$WT" "$WT/eqasim-data/i468p4_100pct.yml"
done
echo "eqasim-bs:      $(git -C "$ROOT/eqasim-bs" log --oneline -1)"
echo "eqasim-bs-rev:  $(git -C "$ROOT/eqasim-bs-rev" log --oneline -1)"
echo "eqasim-java-bs: $(git -C "$ROOT/eqasim-java-bs" log --oneline -1)"
du -sh "$ROOT/eqasim-bs/eqasim-data/$CACHE"
