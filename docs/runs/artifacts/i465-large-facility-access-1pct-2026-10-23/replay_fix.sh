#!/usr/bin/env bash
# Issue #465 A/B replay of the ADR-0165 fix: one QSim iteration of the 1 % rerun's final plans after the
# large-facility access rule (plans_fixed.xml.gz, relinked facilities). Usage: replay_fix.sh <arm> <true|false>
set -euo pipefail
ARM=$1
INSERTION=$2
SRC=$HOME/i465-link/replay_fix
D=$HOME/i465-link/replay_$ARM
mkdir -p "$D"
for f in "$SRC"/*; do ln -sf "$f" "$D/"; done
rm -f "$D/simulation_output"
JAR=$HOME/wt-i487-rerun/cache_rerun/matsim.runtime.eqasim__1c974c0f10b34f4f9c03abe2720b742f.cache/eqasim-java/braunschweig/target/braunschweig-2.3.1.jar
export JAVA_HOME=$HOME/tools/jdk-25.0.3+9
cd "$D"
echo "[replay] arm=$ARM insertion=$INSERTION start=$(date -Is)"
"$JAVA_HOME/bin/java" -Xmx30g -cp "$JAR" org.eqasim.braunschweig.RunSimulation \
  --config-path config.xml \
  --config:plans.inputPlansFile "$SRC/plans_fixed.xml.gz" \
  --config:controler.outputDirectory "$D/simulation_output" \
  --config:controler.lastIteration 0 \
  --config:controler.writeEventsInterval 1 \
  --config:controler.writePlansInterval 1 \
  --config:global.numberOfThreads 12 \
  --config:qsim.numberOfThreads 12 \
  --config:controler.compressionType gzip \
  --config:qsim.insertingWaitingVehiclesBeforeDrivingVehicles "$INSERTION"
echo "[replay] arm=$ARM exit=$? end=$(date -Is)"
