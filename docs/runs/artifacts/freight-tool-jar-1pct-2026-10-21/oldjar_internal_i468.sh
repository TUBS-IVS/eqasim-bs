#!/usr/bin/env bash
# Control run for the #468 freight comparison: the OLD fork jar (braunschweig-2.3.1.jar of the reference
# matsim.runtime.eqasim entry) runs the INTERNAL category with exactly the arguments the pipeline passes
# (copied from the running tool-jar process), into a scratch directory outside the cache. It measures
# (a) the runtime of the old jar under today's server conditions and (b) whether the old jar reproduces
# its own reference output (noise floor). Nothing inside the synpp working directory is written.
# Usage: bash oldjar_internal_i468.sh <category, e.g. internal>
set -euo pipefail
CAT=$1
C=$HOME/i468-freighttool/eqasim-bs/eqasim-data/cache_bs_1pct_allfeat_popsim
REF=$C/braunschweig.freight.extraction__8e87a15edd2848eaede4b412f0b907b3.cache
JAR=$C/matsim.runtime.eqasim__1c974c0f10b34f4f9c03abe2720b742f.cache/eqasim-java/braunschweig/target/braunschweig-2.3.1.jar
OUT=$HOME/i468-freighttool/oldjar_$CAT
DATA=$HOME/i468-freighttool/eqasim-bs/eqasim-data/data/braunschweig/freight/german-wide-freight-v3
mkdir -p "$OUT/tmp"
cp "$REF"/study_area.* "$OUT/"
cd "$OUT"
UPPER=$(echo "$CAT" | tr a-z A-Z)
echo "[i468-oldjar] category=$CAT start=$(date -Is)"
"$HOME/tools/jdk-25.0.3+9/bin/java" -cp "$JAR" -Xmx86G -Djava.io.tmpdir="$OUT/tmp" -Dmatsim.useLocalDtds=true \
  org.eqasim.braunschweig.scenario.RunExtractFreightTrips "$DATA/german_freight.100pct.plans.xml.gz" \
  --network "$DATA/germany-europe-network.xml.gz" --shp study_area.shp --shp-crs EPSG:25832 \
  --input-crs EPSG:25832 --target-crs EPSG:25832 --cut-on-boundary --legMode truck \
  --geographicalTripType "$UPPER" --subpopulation freight --output "$OUT/zgb_freight.$CAT.100pct.plans.xml.gz"
echo "[i468-oldjar] category=$CAT exit=$? end=$(date -Is)"
