#!/usr/bin/env bash
# Usage: run_arm.sh <arm-dir> <jar> <iterations> <threads>
set -euo pipefail
ARM="$1"; JAR="$2"; IT="${3:-10}"; TH="${4:-6}"
JAVA="/c/Program Files/Eclipse Adoptium/jdk-25.0.4.101-hotspot/bin/java"
cd "$ARM"
mkdir -p tmp
rm -rf simulation_output
"$JAVA" -Xmx10G -Djava.io.tmpdir="$ARM/tmp" -Dmatsim.useLocalDtds=true -cp "$JAR" org.eqasim.braunschweig.RunSimulation \
  --config-path braunschweig_cordon_gatecheck_config.xml \
  --config:controler.lastIteration "$IT" --config:controler.writeEventsInterval "$IT" --config:controler.writePlansInterval "$IT" \
  --config:global.numberOfThreads "$TH" --config:qsim.numberOfThreads "$TH" --config:controler.compressionType gzip \
  --config:controler.outputDirectory "$ARM/simulation_output" --config:qsim.personInitializedEvents all > run.log 2>&1
echo "exit=$?" >> run.log
