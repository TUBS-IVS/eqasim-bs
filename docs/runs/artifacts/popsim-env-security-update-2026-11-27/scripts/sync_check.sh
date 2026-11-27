#!/usr/bin/env bash
# Check 7 of popsim-env-security-update-2026-11-27: the start check of the popsim stage
# (`uv sync --frozen --check --offline`, braunschweig.popsim.tool_version.require_environment_in_sync) in three cases:
#   old    the project and environment of arm old
#   new    the project and environment of arm new
#   stale  the project files of arm new over the INSTALLED environment of arm old (a server not re-synced after the
#          merge): <root>/stale holds copies of new's pyproject.toml and uv.lock and a symlink .venv -> <root>/old/.venv
# Each case runs with and without --offline. `--check` changes nothing. Writes <root>/sync_check_<case>_<mode>.log and
# .exit (mode offline or online).
# Usage: sync_check.sh <root>
set -uo pipefail
ROOT="$1"
UV="$HOME/.local/bin/uv"
unset PYTHONPATH
mkdir -p "$ROOT/stale"
cp "$ROOT/new/pyproject.toml" "$ROOT/new/uv.lock" "$ROOT/stale/"
[ -e "$ROOT/stale/.venv" ] || ln -s "$ROOT/old/.venv" "$ROOT/stale/.venv"
for case in old new stale; do
  for mode in offline online; do
    extra=(); [ "$mode" = offline ] && extra=(--offline)
    rc=0
    ( cd "$ROOT/$case" && "$UV" sync --frozen --check "${extra[@]}" ) > "$ROOT/sync_check_${case}_${mode}.log" 2>&1 || rc=$?
    echo "exit $rc" > "$ROOT/sync_check_${case}_${mode}.exit"
    echo "$case $mode: exit $rc"
  done
done
