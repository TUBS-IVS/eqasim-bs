#!/usr/bin/env bash
# Issue #474, felix check A: run the two gated reproducibility tests (two real Maven builds per
# jar) on the server's toolchain (system Maven, JDK 25 from ~/tools) and report the output hashes
# of the builds each test left behind (report_gated_dirs.py of the local run). Full pytest output
# goes to pytest_tool.log / pytest_eqasim.log in the output directory.
# Usage: gated_tests_felix.sh <eqasim-bs worktree> <eqasim-java-bs work tree> <output directory>
set -u
WT="$1"
FORK="$2"
OUT="$3"
export JAVA_HOME="$HOME/tools/jdk-25.0.3+9"
export PATH="$JAVA_HOME/bin:$PATH"
PY="$HOME/miniforge3/envs/eqasim/bin/python"
REPORT="$WT/docs/runs/artifacts/reproducible-jar-builds-local-2026-10-22/report_gated_dirs.py"
JAR=freight-tool/target/freight-tool-2026.0-2026w12.jar
cd "$WT" || exit 2
echo "[i474] start $(date -Is) on $(hostname), time zone $(cat /etc/timezone 2>/dev/null || date +%Z)"
echo "[i474] eqasim-bs HEAD $(git rev-parse HEAD); git status --short entries: $(git status --short | wc -l)"
echo "[i474] eqasim-java-bs HEAD $(git -C "$FORK" rev-parse HEAD); git status --short:"
git -C "$FORK" status --short
java -version 2>&1
mvn -version 2>&1
echo "[i474] freight tool jar"
EQASIM_BS_FREIGHT_TOOL_BUILD=1 "$PY" -m pytest tests/test_freight_extraction_tool.py -k reproducible -v \
    -p no:cacheprovider --basetemp="$OUT/gated_tool" 2>&1 | tee "$OUT/pytest_tool.log" \
    | grep -E "PASSED|FAILED|ERROR|passed|failed"
"$PY" "$REPORT" "$OUT/gated_tool/test_execute_builds_a_byte_rep0" braunschweig.freight.extraction_tool
sha256sum "$OUT/gated_tool/test_execute_builds_a_byte_rep0/a/jarstage.cache/$JAR"
echo "[i474] eqasim jar"
EQASIM_BS_EQASIM_SOURCE="$FORK" "$PY" -m pytest tests/test_eqasim_runtime_token.py -k reproducible -v \
    -p no:cacheprovider --basetemp="$OUT/gated_eqasim" 2>&1 | tee "$OUT/pytest_eqasim.log" \
    | grep -E "PASSED|FAILED|ERROR|passed|failed"
"$PY" "$REPORT" "$OUT/gated_eqasim/test_execute_builds_a_byte_rep0" matsim.runtime.eqasim
echo "[i474] end $(date -Is)"
