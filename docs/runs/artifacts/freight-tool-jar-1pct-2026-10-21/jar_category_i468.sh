#!/usr/bin/env bash
# Timing/determinism control for the #468 freight comparison: run ONE trip category with either the old
# fork jar or the new tool jar, with exactly the arguments the pipeline passes, into a scratch directory
# outside the synpp working directory. Two invocations started together give a same-conditions timing A/B.
# Usage: bash jar_category_i468.sh <old|new> <category> <tag>
set -euo pipefail
WHICH=$1; CAT=$2; TAG=$3
C=$HOME/i468-freighttool/eqasim-bs/eqasim-data/cache_bs_1pct_allfeat_popsim
REF=$C/braunschweig.freight.extraction__8e87a15edd2848eaede4b412f0b907b3.cache
if [ "$WHICH" = old ]; then
  JAR=$C/matsim.runtime.eqasim__1c974c0f10b34f4f9c03abe2720b742f.cache/eqasim-java/braunschweig/target/braunschweig-2.3.1.jar
  MAIN=org.eqasim.braunschweig.scenario.RunExtractFreightTrips
else
  JAR=$C/braunschweig.freight.extraction_tool__780b08cebcffa7cf01d8682865522b61.cache/freight-tool/target/freight-tool-2026.0-2026w12.jar
  MAIN=org.eqasim.braunschweig.freight.RunExtractFreightTrips
fi
OUT=$HOME/i468-freighttool/ctl_${WHICH}_${CAT}_${TAG}
DATA=$HOME/i468-freighttool/eqasim-bs/eqasim-data/data/braunschweig/freight/german-wide-freight-v3
mkdir -p "$OUT/tmp"
cp "$REF"/study_area.* "$OUT/"
cd "$OUT"
UPPER=$(echo "$CAT" | tr a-z A-Z)
echo "[i468-ctl] jar=$WHICH category=$CAT tag=$TAG start=$(date -Is) epoch=$(date +%s)"
"$HOME/tools/jdk-25.0.3+9/bin/java" -cp "$JAR" -Xmx86G -Djava.io.tmpdir="$OUT/tmp" -Dmatsim.useLocalDtds=true \
  "$MAIN" "$DATA/german_freight.100pct.plans.xml.gz" \
  --network "$DATA/germany-europe-network.xml.gz" --shp study_area.shp --shp-crs EPSG:25832 \
  --input-crs EPSG:25832 --target-crs EPSG:25832 --cut-on-boundary --legMode truck \
  --geographicalTripType "$UPPER" --subpopulation freight --output "$OUT/zgb_freight.$CAT.100pct.plans.xml.gz"
echo "[i468-ctl] jar=$WHICH category=$CAT tag=$TAG exit=$? end=$(date -Is) epoch=$(date +%s)"
