#!/usr/bin/env bash
# Issue #465 / ADR-0169 A/B continuation: 40 more iterations (replanning on) from the 1 % rerun's final plans.
# Usage: continue_ab.sh <arm> <scenario dir: replay_true (old facilities) | replay_fix (relinked)> <plans> <insertion>
set -euo pipefail
ARM=$1
SRC=$HOME/i465-link/$2
PLANS=$3
INSERTION=$4
D=$HOME/i465-link/cont_$ARM
mkdir -p "$D"
for f in "$SRC"/*; do [ "$(basename "$f")" = simulation_output ] || ln -sf "$f" "$D/"; done
JAR=$HOME/wt-i487-rerun/cache_rerun/matsim.runtime.eqasim__1c974c0f10b34f4f9c03abe2720b742f.cache/eqasim-java/braunschweig/target/braunschweig-2.3.1.jar
export JAVA_HOME=$HOME/tools/jdk-25.0.3+9
cd "$D"
echo "[cont] arm=$ARM src=$2 plans=$PLANS insertion=$INSERTION start=$(date -Is)"
"$JAVA_HOME/bin/java" -Xmx20g -cp "$JAR" org.eqasim.braunschweig.RunSimulation \
  --config-path config.xml \
  --config:plans.inputPlansFile "$PLANS" \
  --config:controler.outputDirectory "$D/simulation_output" \
  --config:controler.lastIteration 40 \
  --config:controler.writeEventsInterval 40 \
  --config:controler.writePlansInterval 40 \
  --config:global.numberOfThreads 12 \
  --config:qsim.numberOfThreads 12 \
  --config:controler.compressionType gzip \
  --config:qsim.insertingWaitingVehiclesBeforeDrivingVehicles "$INSERTION"
echo "[cont] arm=$ARM exit=$? end=$(date -Is)"
