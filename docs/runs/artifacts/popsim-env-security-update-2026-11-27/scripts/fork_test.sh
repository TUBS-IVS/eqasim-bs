#!/usr/bin/env bash
# Check 6 of popsim-env-security-update-2026-11-27 (ADR-0200 decision 5): run the fork's balancer fast-path tests at the
# pinned release tag in the environment of each arm, as docs/codebase/notes/populationsim-fork.md describes.
# Usage: fork_test.sh <root>
# Clones TUBS-IVS/populationsim at tag v0.10.0+bs.3 into <root>/fork (once), then per arm writes
# <root>/pytest_<arm>.log (first line: the commit of the clone) and <root>/pytest_<arm>.exit.
set -uo pipefail
ROOT="$1"
UV="$HOME/.local/bin/uv"
TAG="v0.10.0+bs.3"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
if [ ! -d "$ROOT/fork" ]; then
  git clone -q --depth 1 --branch "$TAG" https://github.com/TUBS-IVS/populationsim "$ROOT/fork" || exit 1
fi
for arm in old new; do
  log="$ROOT/pytest_$arm.log"
  ( cd "$ROOT/fork"
    git rev-parse HEAD > "$log"
    rc=0
    PYTHONPATH="$ROOT/fork" "$UV" run --project "$ROOT/$arm" --no-sync --with pytest \
      python -m pytest tests/test_bs_balancer_fast_path.py -q -p no:cacheprovider >> "$log" 2>&1 || rc=$?
    echo "exit $rc" > "$ROOT/pytest_$arm.exit" )
done
