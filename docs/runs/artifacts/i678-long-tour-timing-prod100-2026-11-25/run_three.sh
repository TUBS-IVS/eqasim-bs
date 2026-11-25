#!/bin/bash
# #678 B9 step 1: the three cut files in turn, 16 threads, chunk size 1; the 13-trip file under a 4 h cap.
PY=$HOME/miniforge3/envs/eqasim/bin/python
JAR=$HOME/i678_work/eqasim-java-bs/braunschweig/target/braunschweig-2.3.1.jar
JAVA=$HOME/tools/jdk-25.0.3+9/bin/java
T=$HOME/i678_timing
for b in long_9_11 long_7_8; do
  $PY $T/i678_run_initial_choice.py $HOME/i678_work/eqasim-bs $T/run_$b $b.xml.gz $JAR $JAVA 16 40G 1 > $T/driver_${b}_chunk1.txt 2>&1
done
$PY $T/i678_run_initial_choice.py $HOME/i678_work/eqasim-bs $T/run_long_12_13 long_12_13.xml.gz $JAR $T/java_capped_4h.sh 16 48G 1 > $T/driver_long_12_13_chunk1.txt 2>&1
