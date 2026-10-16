#!/usr/bin/env bash
# Launch one arm of the #444 smoke detached (setsid nohup), logging to ~/i444-smoke/logs/run_<arm>.log.
# Usage: bash launch_arm.sh <arm>
set -euo pipefail
ARM=$1
WT=$HOME/i444-smoke/$ARM/eqasim-bs
LOG=$HOME/i444-smoke/logs/run_${ARM}_$(date +%Y%m%dT%H%M%S).log
test -d "$WT/eqasim-data/cache_i444_1pct_$ARM" || { echo "missing working directory copy" >&2; exit 1; }
cd "$WT"
setsid nohup bash -c "
  source \$HOME/miniforge3/etc/profile.d/conda.sh && conda activate eqasim
  export JAVA_HOME=\$HOME/tools/jdk-25.0.3+9
  export PATH=\$JAVA_HOME/bin:\$HOME/.local/bin:\$PATH
  echo \"[i444-smoke] arm=$ARM eqasim-bs=\$(git rev-parse HEAD) eqasim-java-bs=\$(git -C ../eqasim-java-bs rev-parse HEAD) start=\$(date -Is)\"
  python scripts/run_synpp.py configs/base_bs.yml eqasim-data/i444_1pct_$ARM.yml
  echo \"[i444-smoke] arm=$ARM exit=\$? end=\$(date -Is)\"
" < /dev/null >> "$LOG" 2>&1 &
sleep 2
echo "launched arm $ARM, log $LOG"
pgrep -af "[r]un_synpp.py configs/base_bs.yml eqasim-data/i444_1pct_$ARM" || true
