#!/usr/bin/env bash
# Create one isolated arm of the #444 smoke: <root>/<arm>/eqasim-bs and <root>/<arm>/eqasim-java-bs as
# detached worktrees, so the base value eqasim_source_path ../eqasim-java-bs resolves to the arm's own
# Java tree without changing the config value (and therefore the stage hashes).
# Usage: bash setup_arm.sh <arm> <eqasim-bs ref> <eqasim-java-bs ref>
set -euo pipefail
ROOT=$HOME/i444-smoke
ARM=$1; BS_REF=$2; JAVA_REF=$3
mkdir -p "$ROOT/$ARM" "$ROOT/logs"
git -C "$HOME/eqasim-bs" fetch -q origin
git -C "$HOME/eqasim-java-bs" fetch -q origin
git -C "$HOME/eqasim-bs" worktree add --detach "$ROOT/$ARM/eqasim-bs" "$BS_REF"
git -C "$HOME/eqasim-java-bs" worktree add --detach "$ROOT/$ARM/eqasim-java-bs" "$JAVA_REF"
python3 "$ROOT/symlink_farm.py" "$HOME/eqasim-bs/eqasim-data" "$ROOT/$ARM/eqasim-bs/eqasim-data"
echo "eqasim-bs:      $(git -C "$ROOT/$ARM/eqasim-bs" log --oneline -1)"
echo "eqasim-java-bs: $(git -C "$ROOT/$ARM/eqasim-java-bs" log --oneline -1)"
