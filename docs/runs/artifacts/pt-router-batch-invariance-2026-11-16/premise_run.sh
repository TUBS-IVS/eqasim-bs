set -e
D=$HOME/audit_h_prefetch_premise
C=$HOME/prod100_20261031/cache
J=$C/matsim.runtime.eqasim__1c974c0f10b34f4f9c03abe2720b742f.cache/eqasim-java/braunschweig/target/braunschweig-2.3.1.jar
cd "$D"
cp "$C/braunschweig.synthesis.outside_tour_modes.stage__e4615b7799e3661dd55de11d3ef273ff.cache/outside_tour_pt_router_config.xml" config.xml
python3 premise_build.py
for n in all part0 part1 part2 part3 tiny; do
  t=8
  if [ "$n" = all ]; then t=16; fi
  "$HOME/tools/jdk-25.0.3+9/bin/java" -cp "$J" -Xmx16G -Dmatsim.useLocalDtds=true \
    org.eqasim.core.tools.routing.RunBatchPublicTransportRouter --config-path config.xml --input-path "$n.csv" \
    --output-trips-path "$n.trips.csv" --output-legs-path "$n.legs.csv" --threads "$t" > "$n.log" 2>&1
  echo "done $n"
done
echo ALLDONE
