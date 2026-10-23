#!/usr/bin/env bash
# Launch the #465 WP-E smoke detached (setsid nohup); log to ~/wt-i465-wpe/logs/run_wpe_<ts>.log.
# The overlay carries the launcher gate synpp_expected_stale_stages (the stale list of explain_before.log).
set -euo pipefail
ROOT=$HOME/wt-i465-wpe
LOG=$ROOT/logs/run_wpe_$(date +%Y%m%dT%H%M%S).log
cd "$ROOT/eqasim-bs"
setsid nohup bash -c "
  source \$HOME/miniforge3/etc/profile.d/conda.sh && conda activate eqasim
  export JAVA_HOME=\$HOME/tools/jdk-25.0.3+9
  export PATH=\$JAVA_HOME/bin:\$HOME/.local/bin:\$PATH
  export PYTHONUTF8=1
  echo \"[i465-wpe] eqasim-bs=\$(git rev-parse HEAD) eqasim-java-bs=\$(git -C ../eqasim-java-bs rev-parse HEAD) start=\$(date -Is)\"
  python scripts/run_synpp.py configs/base_bs.yml ../i465_wpe.yml
  echo \"[i465-wpe] exit=\$? end=\$(date -Is)\"
" < /dev/null >> "$LOG" 2>&1 &
sleep 3
echo "launched, log $LOG"
pgrep -af "[r]un_synpp.py configs/base_bs.yml ../i465_wpe.yml" || true
