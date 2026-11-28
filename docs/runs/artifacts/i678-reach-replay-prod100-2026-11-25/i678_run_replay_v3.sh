#!/bin/bash
# #678 B9 step 2, third attempt (branch 950c91a8, eqasim-java-bs main 3f398bdfe with the merged runner fixes of PR #66): set up the replay stage directory and run the four prepare steps of the branch on it.
# The stage directory gets symbolic links to the files of the run's prepare stage directory (read-only), a COPY of
# the final config and a COPY of the prepared population (the steps replace both in place). Usage:
#   i678_run_replay_v3.sh <threads> <Xmx>
set -euo pipefail
PREPARE=$HOME/prod100_20261031/cache/braunschweig.matsim.simulation.prepare__57fd011e45464705ad66d48134be0b63.cache
CONFIG=braunschweig_100pct_prod20261031_config.xml
POPULATION=braunschweig_100pct_prod20261031_population.xml.gz
BASE=$HOME/i678_replay3
STAGE=$BASE/stage
THREADS=$1
XMX=$2
mkdir "$STAGE"
for f in "$PREPARE"/*; do
  name=$(basename "$f")
  case "$name" in
    "$CONFIG"|"$POPULATION"|__java_temp|plan_check.json) ;;
    *) ln -s "$f" "$STAGE/$name" ;;
  esac
done
cp "$PREPARE/$CONFIG" "$STAGE/$CONFIG"
cp "$PREPARE/$POPULATION" "$STAGE/$POPULATION"
cmp "$PREPARE/$CONFIG" "$STAGE/$CONFIG"
cmp "$PREPARE/$POPULATION" "$STAGE/$POPULATION"
echo "copied unchanged: config $(sha256sum "$STAGE/$CONFIG" | cut -d' ' -f1), population $(sha256sum "$STAGE/$POPULATION" | cut -d' ' -f1)"
echo "start $(date -Iseconds); threads $THREADS, Xmx $XMX; $(uptime)"
set +e
/usr/bin/time -v "$HOME/miniforge3/envs/eqasim/bin/python" "$BASE/i678_reach_replay_v2.py" "$BASE/eqasim-bs" "$STAGE" \
  "$BASE/eqasim-java-bs/braunschweig/target/braunschweig-2.3.1.jar" "$HOME/tools/jdk-25.0.3+9/bin/java" "$THREADS" "$XMX"
code=$?
echo "end $(date -Iseconds); exit $code; $(uptime)"
echo "EXIT=$code" > "$BASE/REPLAY_EXIT"
