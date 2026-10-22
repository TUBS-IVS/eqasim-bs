#!/usr/bin/env bash
# Issue #474: root-cause listing and time-zone check for the freight tool jar.
# 1. probe_tool.py --baseline: the build before the fix (no outputTimestamp), Europe/Berlin.
# 2. probe_tool.py with MAVEN_OPTS=-Duser.timezone=UTC: the fixed build in another time zone.
# 3. sha256 of the UTC jar against the jar the gated test built (fixed, Europe/Berlin default).
# Usage: tool_probes.sh <output directory> <gated tool test directory>
set -u
OUT="$1"
GATED="$2"
HERE="$(cd "$(dirname "$0")" && pwd)"
export JAVA_HOME="/c/Program Files/Eclipse Adoptium/jdk-25.0.4.101-hotspot"
PY=/c/Users/bienzeisler/AppData/Local/miniforge3/envs/eqasim/python.exe
JAR=freight-tool/target/freight-tool-2026.0-2026w12.jar
echo "[i474] default time zone of this machine: $(tzutil //g 2>/dev/null || echo unknown)"
echo "[i474] 1. baseline (no outputTimestamp), default time zone"
"$PY" "$HERE/probe_tool.py" "$OUT/tool_baseline" --baseline 2>&1 | grep -v "^WARNING\|^$"
echo "[i474] 2. fixed, MAVEN_OPTS=-Duser.timezone=UTC"
MAVEN_OPTS="-Duser.timezone=UTC" "$PY" "$HERE/probe_tool.py" "$OUT/tool_fixed_utc" 2>&1 | grep -v "^WARNING\|^$"
echo "[i474] 3. jar sha256: UTC build vs the gated test's build (default time zone)"
sha256sum "$OUT/tool_fixed_utc/aaa/$JAR" "$GATED/a/jarstage.cache/$JAR" "$GATED/working_directory_with_a_longer_path/jarstage.cache/$JAR" | sed "s#  .*/\(tool_fixed_utc\|test_execute_builds_a_byte_rep0\)/#  \1/#"
