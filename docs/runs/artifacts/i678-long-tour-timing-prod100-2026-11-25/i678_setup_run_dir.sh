#!/bin/bash
# #678 B9 step 1: one run directory per cut file. Symbolic links to the files of the run's prepare stage directory
# (read-only), a COPY of the final config (write_initial_config writes its second file next to it), and a link to
# the cut population file. Usage: i678_setup_run_dir.sh <run dir> <cut population file>
set -euo pipefail
PREPARE=$HOME/prod100_20261031/cache/braunschweig.matsim.simulation.prepare__57fd011e45464705ad66d48134be0b63.cache
CONFIG=braunschweig_100pct_prod20261031_config.xml
RUN=$1
CUT=$2
mkdir -p "$RUN"
for f in "$PREPARE"/*; do
  name=$(basename "$f")
  case "$name" in
    "$CONFIG"|__java_temp|braunschweig_100pct_prod20261031_population.xml.gz|plan_check.json) ;;
    *) ln -sfn "$f" "$RUN/$name" ;;
  esac
done
cp "$PREPARE/$CONFIG" "$RUN/$CONFIG"
ln -sfn "$CUT" "$RUN/$(basename "$CUT")"
cmp "$PREPARE/$CONFIG" "$RUN/$CONFIG" && echo "config copied unchanged: $(sha256sum "$RUN/$CONFIG" | cut -d' ' -f1)"
