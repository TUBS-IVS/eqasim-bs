#!/bin/bash
# #678 B9 step 2 diagnosis of the NullPointerException of the initial mode choice: run the standalone runner on the
# first 500 freight persons, once with the initial config (IsolatedOutsideTrips, BorderTourVehicleContinuity,
# length 11) and once with the final config of the replay (ActivityBased, VehicleContinuity, length 11), with full
# stack traces (-XX:-OmitStackTraceInFastThrow), 4 threads, chunk size 100. Links to the replay stage files.
set -uo pipefail
BASE=$HOME/i678_replay
DIAG=$BASE/diag_freight
PREPARE=$HOME/prod100_20261031/cache/braunschweig.matsim.simulation.prepare__57fd011e45464705ad66d48134be0b63.cache
mkdir -p "$DIAG"
for f in "$BASE"/stage/*; do
  name=$(basename "$f")
  case "$name" in
    braunschweig_100pct_prod20261031_population.xml.gz|mode_choice|__java_temp|replay_*) ;;
    *) ln -sfn "$f" "$DIAG/$name" ;;
  esac
done
"$HOME/miniforge3/envs/eqasim/bin/python" "$BASE/i678_cut_subpopulation.py" \
  "$PREPARE/braunschweig_100pct_prod20261031_population.xml.gz" "$DIAG/freight_500.xml.gz" 500
cd "$DIAG"
for variant in initial final; do
  if [ "$variant" = initial ]; then CONFIG=braunschweig_100pct_prod20261031_config_initial_mode_choice.xml;
  else CONFIG=braunschweig_100pct_prod20261031_config.xml; fi
  mkdir -p "$DIAG/tmp_$variant"
  "$HOME/tools/jdk-25.0.3+9/bin/java" -cp "$BASE/eqasim-java-bs/braunschweig/target/braunschweig-2.3.1.jar" -Xmx24G \
    -XX:-OmitStackTraceInFastThrow -Djava.io.tmpdir="$DIAG/tmp_$variant" -Dmatsim.useLocalDtds=true \
    org.eqasim.core.standalone_mode_choice.RunStandaloneModeChoice --config-path "$CONFIG" \
    --config:plans.inputPlansFile freight_500.xml.gz --config:standaloneModeChoice.outputDirectory "mode_choice_$variant" \
    --config:standaloneModeChoice.removePersonsWithNoValidAlternatives true --config:global.numberOfThreads 4 \
    --config:standaloneModeChoice.chunkSize 100 --write-input-csv-trips true --write-output-csv-trips true \
    --compression gzip --skip-scenario-check true \
    --eqasim-configurator org.eqasim.braunschweig.BraunschweigConfigurator > "java_$variant.txt" 2>&1 &
  pid=$!
  # The runner can hang after a worker exception (a non-daemon progress thread keeps the JVM alive): cap it.
  timeout_s=900; waited=0
  while kill -0 $pid 2>/dev/null; do
    if grep -q "Exception in thread" "java_$variant.txt" && ! grep -q "ParallelProgress.*100.00%" "java_$variant.txt"; then
      sleep 20; kill $pid 2>/dev/null; echo "$variant: killed after the main thread exception"; break
    fi
    if [ $waited -ge $timeout_s ]; then kill $pid; echo "$variant: killed after $timeout_s s"; break; fi
    sleep 10; waited=$((waited + 10))
  done
  wait $pid; echo "$variant: exit $?"
done
echo DIAG_DONE > "$DIAG/DIAG_DONE"
