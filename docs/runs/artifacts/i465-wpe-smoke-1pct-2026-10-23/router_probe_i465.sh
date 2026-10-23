#!/usr/bin/env bash
# Batch pt router checks of the #465 WP-E smoke, run by hand on felix with the run's own jar and network/schedule.
# 1. router_try: the first gate-choice batch (183 tasks, copied from the stage's pt_router/router_tasks.csv) with the
#    router config the fixed write_router_config writes (qsim personInitializedEvents=all, endTime 30:00:00).
# 2. router_x100: the same tasks replicated 100 times (18,300 tasks, identifiers suffixed #k), 32 threads, as a
#    runtime measurement at the task count of a 100 % sample. Duplicated tasks are routed independently (no cache).
# The egress check (pt trips with egress_distance_km <= 0) reads router_try/router_trips.csv with pandas.
set -euo pipefail
A=$HOME/wt-i465-wpe/analysis
STAGE=$HOME/wt-i465-wpe/cache_wpe/braunschweig.synthesis.portal_trips.stage__f19f2d15ddc5414759d8e8957d6f005f.cache
JAR=$HOME/wt-i465-wpe/cache_wpe/matsim.runtime.eqasim__1c974c0f10b34f4f9c03abe2720b742f.cache/eqasim-java/braunschweig/target/braunschweig-2.3.1.jar
JAVA=$HOME/tools/jdk-25.0.3+9/bin/java
run_router() {  # <dir> <config> <threads>
  mkdir -p "$1/tmp"
  ( cd "$1" && /usr/bin/time -v "$JAVA" -cp "$JAR" -Xmx20G -Djava.io.tmpdir="$1/tmp" -Dmatsim.useLocalDtds=true \
      org.eqasim.core.tools.routing.RunBatchPublicTransportRouter --config-path "$2" --input-path router_tasks.csv \
      --output-trips-path router_trips.csv --output-legs-path router_legs.csv --threads "$3" > java.log 2>&1 )
  grep -E "Elapsed|Maximum resident" "$1/java.log"
}
mkdir -p "$A/router_try"
cp "$STAGE/pt_router/router_tasks.csv" "$A/router_try/"   # copied right after the first, failed launch
# pt_router_config.xml = the stage's config plus the qsim module (identical to what write_router_config now writes)
run_router "$A/router_try" "$A/router_try/pt_router_config.xml" 56
mkdir -p "$A/router_x100"
python3 - "$A" <<'PY'
import sys, pandas as pd
a = sys.argv[1]
t = pd.read_csv(f"{a}/router_try/router_tasks.csv", dtype={"identifier": str})
pd.concat([t.assign(identifier=t["identifier"] + "#" + str(k)) for k in range(100)]).to_csv(
    f"{a}/router_x100/router_tasks.csv", index=False)
PY
run_router "$A/router_x100" "$A/router_try/pt_router_config.xml" 32
