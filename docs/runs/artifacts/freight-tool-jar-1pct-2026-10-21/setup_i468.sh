#!/usr/bin/env bash
# Set up the #468 Phase 2 freight tool jar comparison on felix (copy only, production caches untouched).
# Layout: ~/i468-freighttool/{eqasim-bs,eqasim-java-bs} as detached worktrees, so the base value
# eqasim_source_path ../eqasim-java-bs resolves to the fork worktree without a config change.
# The 1 % working directory is a physical copy (cp -a, never cp -al) under the SAME relative name, so
# working_directory / analysis_working_directory strings and with them the stage hashes stay unchanged.
# Usage: bash setup_i468.sh <eqasim-bs ref> <eqasim-java-bs ref>
set -euo pipefail
ROOT=$HOME/i468-freighttool
BS_REF=$1; JAVA_REF=$2
CACHE=cache_bs_1pct_allfeat_popsim
mkdir -p "$ROOT/logs"
git -C "$HOME/eqasim-bs" fetch -q origin
git -C "$HOME/eqasim-java-bs" fetch -q origin
test -e "$ROOT/eqasim-bs" || git -C "$HOME/eqasim-bs" worktree add --detach "$ROOT/eqasim-bs" "$BS_REF"
test -e "$ROOT/eqasim-java-bs" || git -C "$HOME/eqasim-java-bs" worktree add --detach "$ROOT/eqasim-java-bs" "$JAVA_REF"
python3 "$ROOT/eqasim-bs/docs/runs/artifacts/freight-boundary-links-ab-1pct-2026-10-16/symlink_farm.py" \
    "$HOME/eqasim-bs/eqasim-data" "$ROOT/eqasim-bs/eqasim-data"
if [ ! -e "$ROOT/eqasim-bs/eqasim-data/$CACHE" ]; then
    echo "[i468] cp -a $CACHE start $(date -Is)"
    cp -a "$HOME/eqasim-bs/eqasim-data/$CACHE" "$ROOT/eqasim-bs/eqasim-data/$CACHE"
    echo "[i468] cp -a $CACHE done $(date -Is)"
fi
source "$HOME/miniforge3/etc/profile.d/conda.sh" && conda activate eqasim
python "$ROOT/make_overlay_i468.py" "$ROOT/eqasim-bs" "$ROOT/eqasim-bs/eqasim-data/i468_1pct_freight.yml"
echo "eqasim-bs:      $(git -C "$ROOT/eqasim-bs" log --oneline -1)"
echo "eqasim-java-bs: $(git -C "$ROOT/eqasim-java-bs" log --oneline -1)"
du -sh "$ROOT/eqasim-bs/eqasim-data/$CACHE"
