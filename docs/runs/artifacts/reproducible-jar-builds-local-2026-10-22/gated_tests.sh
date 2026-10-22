#!/usr/bin/env bash
# Issue #474: run the two gated reproducibility tests (two real Maven builds per jar) from the
# committed state of the worktree, record the toolchain, and report the output hashes of the
# builds each test left behind (report_gated_dirs.py).
# Usage: gated_tests.sh <worktree> <fork work tree> <output directory (long path form)>
set -u
WT="$1"
FORK="$2"
OUT="$3"
HERE="$(cd "$(dirname "$0")" && pwd)"
export JAVA_HOME="/c/Program Files/Eclipse Adoptium/jdk-25.0.4.101-hotspot"
export PATH="/c/Users/bienzeisler/tools/apache-maven-3.9.9/bin:$PATH"
PY=/c/Users/bienzeisler/AppData/Local/miniforge3/envs/eqasim/python.exe
cd "$WT" || exit 2
echo "[i474] start $(date -Is)"
echo "[i474] eqasim-bs HEAD $(git rev-parse HEAD); uncommitted .py files: $(git status --porcelain -- '*.py' | wc -l)"
echo "[i474] eqasim-java-bs HEAD $(git -C "$FORK" rev-parse HEAD); git status --short:"
git -C "$FORK" status --short
"$JAVA_HOME/bin/java" -version 2>&1
mvn -version 2>&1 | grep -v WARNING
echo "[i474] freight tool jar"
EQASIM_BS_FREIGHT_TOOL_BUILD=1 "$PY" -m pytest tests/test_freight_extraction_tool.py -k reproducible -v \
    -p no:cacheprovider --basetemp="$OUT/gated_tool" 2>&1 | grep -E "PASSED|FAILED|ERROR|passed|failed"
"$PY" "$HERE/report_gated_dirs.py" "$OUT/gated_tool/test_execute_builds_a_byte_rep0" braunschweig.freight.extraction_tool
echo "[i474] eqasim jar"
EQASIM_BS_EQASIM_SOURCE="$(cygpath -w "$FORK")" "$PY" -m pytest tests/test_eqasim_runtime_token.py -k reproducible -v \
    -p no:cacheprovider --basetemp="$OUT/gated_eqasim" 2>&1 | grep -E "PASSED|FAILED|ERROR|passed|failed"
"$PY" "$HERE/report_gated_dirs.py" "$OUT/gated_eqasim/test_execute_builds_a_byte_rep0" matsim.runtime.eqasim
echo "[i474] end $(date -Is)"
