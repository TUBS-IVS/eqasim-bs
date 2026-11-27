#!/bin/bash
PY=$HOME/miniforge3/envs/eqasim/bin/python
JAR=$HOME/i678_work/eqasim-java-bs/braunschweig/target/braunschweig-2.3.1.jar
JAVA=$HOME/tools/jdk-25.0.3+9/bin/java
for b in long_7_8 long_9_11; do
  $PY $HOME/i678_timing/i678_run_initial_choice.py $HOME/i678_work/eqasim-bs $HOME/i678_timing/run_$b $b.xml.gz $JAR $JAVA 16 40G > $HOME/i678_timing/driver_$b.txt 2>&1
done
