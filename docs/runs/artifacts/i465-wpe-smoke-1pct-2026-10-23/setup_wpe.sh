#!/usr/bin/env bash
# Set up the isolated WP-E smoke of #465 on felix (1 %, comparable to arm 2 of the i453/i443 A/B).
# Layout ~/wt-i465-wpe/{eqasim-bs,eqasim-java-bs}: detached worktrees of origin/main of both repositories,
# so the base value eqasim_source_path ../eqasim-java-bs resolves to this run's own Java tree. The shared
# checkouts ~/eqasim-bs and ~/eqasim-java-bs are only fetched, never switched or pulled.
# The gitignored inputs come in through a symlink farm (tracked files of the worktree are kept).
# The working directory is a physical copy of the baseline's cache_arms12 (never the original).
# Usage: bash setup_wpe.sh
set -euo pipefail
ROOT=$HOME/wt-i465-wpe
BASELINE_CACHE=$HOME/wt-i453-ab/cache_arms12
mkdir -p "$ROOT/logs"
git -C "$HOME/eqasim-bs" fetch -q origin
git -C "$HOME/eqasim-java-bs" fetch -q origin
test -e "$ROOT/eqasim-bs" || git -C "$HOME/eqasim-bs" worktree add --detach "$ROOT/eqasim-bs" origin/main
test -e "$ROOT/eqasim-java-bs" || git -C "$HOME/eqasim-java-bs" worktree add --detach "$ROOT/eqasim-java-bs" origin/main
python3 "$ROOT/symlink_farm.py" "$HOME/eqasim-bs/eqasim-data" "$ROOT/eqasim-bs/eqasim-data"
if [ ! -e "$ROOT/cache_wpe" ]; then
  echo "copying $BASELINE_CACHE -> $ROOT/cache_wpe ($(du -sh "$BASELINE_CACHE" | cut -f1))"
  cp -a "$BASELINE_CACHE" "$ROOT/cache_wpe"
fi
echo "eqasim-bs:      $(git -C "$ROOT/eqasim-bs" log --oneline -1)"
echo "eqasim-java-bs: $(git -C "$ROOT/eqasim-java-bs" log --oneline -1)"
echo "shared eqasim-bs:      $(git -C "$HOME/eqasim-bs" branch --show-current) $(git -C "$HOME/eqasim-bs" status --porcelain | wc -l) dirty"
echo "shared eqasim-java-bs: $(git -C "$HOME/eqasim-java-bs" branch --show-current) $(git -C "$HOME/eqasim-java-bs" status --porcelain | wc -l) dirty"
du -sh "$ROOT/cache_wpe"
df -h "$HOME" | tail -1
