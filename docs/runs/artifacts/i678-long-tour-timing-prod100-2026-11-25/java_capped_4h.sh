#!/bin/bash
# Java with a hard 4 h cap (B9 step 1 stop rule); exit 124 when the cap fired.
exec timeout --kill-after=60 4h $HOME/tools/jdk-25.0.3+9/bin/java "$@"
