#!/usr/bin/env bash
# Launch the #468 freight tool jar run detached (setsid nohup); log in ~/i468-freighttool/logs.
# Usage: bash launch_i468.sh
set -euo pipefail
WT=$HOME/i468-freighttool/eqasim-bs
LOG=$HOME/i468-freighttool/logs/run_freighttool_$(date +%Y%m%dT%H%M%S).log
test -d "$WT/eqasim-data/cache_bs_1pct_allfeat_popsim" || { echo "missing working directory copy" >&2; exit 1; }
cd "$WT"
setsid nohup bash -c "
  source \$HOME/miniforge3/etc/profile.d/conda.sh && conda activate eqasim
  export JAVA_HOME=\$HOME/tools/jdk-25.0.3+9
  export PATH=\$JAVA_HOME/bin:\$HOME/.local/bin:\$PATH
  echo \"[i468] eqasim-bs=\$(git rev-parse HEAD) eqasim-java-bs=\$(git -C ../eqasim-java-bs rev-parse HEAD) start=\$(date -Is)\"
  python scripts/run_synpp.py configs/base_bs.yml eqasim-data/i468_1pct_freight.yml
  echo \"[i468] exit=\$? end=\$(date -Is)\"
" < /dev/null >> "$LOG" 2>&1 &
sleep 2
echo "launched, log $LOG"
pgrep -af "[r]un_synpp.py configs/base_bs.yml eqasim-data/i468_1pct_freight.yml" || true
