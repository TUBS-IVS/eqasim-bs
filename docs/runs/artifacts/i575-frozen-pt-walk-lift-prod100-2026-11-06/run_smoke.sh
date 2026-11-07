#!/bin/bash
# #575 ADR-0189 smoke: MATSim start-up (lastIteration 0) on the lifted sample of prod100_20261031, run's own jar and config.
set -u
CACHE=/home/felix/prod100_20261031/cache
CFG=$CACHE/braunschweig.matsim.simulation.prepare__57fd011e45464705ad66d48134be0b63.cache/braunschweig_100pct_prod20261031_config.xml
JAR=$CACHE/matsim.runtime.eqasim__1c974c0f10b34f4f9c03abe2720b742f.cache/eqasim-java/braunschweig/target/braunschweig-2.3.1.jar
SAMPLE=$1
OUT=$2
/home/felix/tools/jdk-25.0.3+9/bin/java -cp $JAR -Xmx40G -Djava.io.tmpdir=/home/felix/scratch_i575p/smoke/java_temp -Dmatsim.useLocalDtds=true org.eqasim.braunschweig.RunSimulation --config-path $CFG --config:controler.lastIteration 0 --config:controler.writeEventsInterval 1 --config:controler.writePlansInterval 1 --config:global.numberOfThreads 8 --config:qsim.numberOfThreads 4 --config:controler.compressionType gzip --config:controler.outputDirectory $OUT --config:plans.inputPlansFile $SAMPLE
echo EXIT=$?
