#!/usr/bin/env bash
# Install the two arms of popsim-env-security-update-2026-11-27 on the run server.
# Usage: setup_envs.sh <root>
# <root>/old and <root>/new each hold pyproject.toml and uv.lock (the blobs of main at b7472c00 and of the branch
# commit). Per arm: check that the lock fits its pyproject (`uv lock --check`), install it as the README of
# environments/populationsim says, and record `uv pip freeze` and which PopulationSim the environment imports.
# Writes <root>/<arm>.lock_check.log, <arm>.sync.log, <arm>.freeze.txt and <arm>.tool.json.
set -euo pipefail
ROOT="$1"
UV="$HOME/.local/bin/uv"
unset PYTHONPATH
REPORT='import json, platform
from importlib import metadata
import populationsim
direct_url = metadata.distribution("populationsim").read_text("direct_url.json")
commit = json.loads(direct_url).get("vcs_info", {}).get("commit_id", "unknown") if direct_url else "unknown"
print(json.dumps({"populationsim_version": metadata.version("populationsim"), "commit": commit,
                  "populationsim_file": populationsim.__file__, "python": platform.python_version()}))'
for arm in old new; do
  d="$ROOT/$arm"
  [ -f "$d/pyproject.toml" ] && [ -f "$d/uv.lock" ] || { echo "missing project files in $d" >&2; exit 1; }
  [ ! -e "$d/.venv" ] || { echo "refuse: $d/.venv exists" >&2; exit 1; }
  ( cd "$d"
    "$UV" lock --check > "$ROOT/$arm.lock_check.log" 2>&1
    "$UV" sync --frozen --python /usr/bin/python3.12 > "$ROOT/$arm.sync.log" 2>&1
    "$UV" pip freeze > "$ROOT/$arm.freeze.txt"
    "$UV" run --no-sync python -c "$REPORT" > "$ROOT/$arm.tool.json" )
  echo "$arm: $(cat "$ROOT/$arm.tool.json")"
done
