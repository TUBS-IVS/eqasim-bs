#!/usr/bin/env bash
#
# monitor_run_health.sh - structured health snapshot for a running eqasim/popsim
# pipeline run on the Linux run server.
#
# Emits one compact block covering every failure class observed in production so a
# monitoring loop can detect and react to them directly:
#   - numpy/OpenBLAS segfaults (thread oversubscription)  -> segfaults_total
#   - disk-full / ENOSPC ("No space left on device")      -> disk_avail
#   - RAM exhaustion / OOM                                 -> ram_avail_gb
#   - dead or stalled PopulationSim batches                -> popsim_procs, log_age_s, markers
#   - errors / tracebacks in the run log                   -> recent_errors
#   - run / process death                                  -> tmux, last_log
#
# Usage: monitor_run_health.sh [LOG] [WORKDIR] [TMUX_SESSION]
#   LOG     default: newest ~/eqasim-bs/logs/run_*.log
#   WORKDIR default: ~/eqasim-bs/eqasim-data/popsim_work_allfeat (PopulationSim batches)
#   SESSION default: bs25
#
# Read-only: it never kills, deletes, or restarts anything -- the caller decides.

LOG="${1:-$(ls -t "$HOME"/eqasim-bs/logs/run_*.log 2>/dev/null | head -1)}"
WORKDIR="${2:-$HOME/eqasim-bs/eqasim-data/popsim_work_allfeat}"
SESS="${3:-bs25}"

echo "=== health @ $(date "+%F %T") ==="
echo "log: $LOG"
echo "tmux($SESS): $(tmux ls 2>/dev/null | grep -q "^$SESS:" && echo UP || echo DOWN)"
echo "stage: $(grep -aE "Executing stage|Pipeline progress" "$LOG" 2>/dev/null | tail -1 | sed "s/.*synpp[^E]*//")"
echo "markers: $(find "$WORKDIR" -name final_expanded_household_ids.csv 2>/dev/null | wc -l)/33"
echo "popsim_procs: $(ps -eo comm | grep -c populationsim)"
echo "segfaults_total: $(dmesg 2>/dev/null | grep -c "populationsim.*segfault")"
echo "disk_avail: $(df -h "$HOME" | awk "NR==2{print \$4\" (\"\$5\" used)\"}")"
echo "ram_avail_gb: $(free -g | awk "/Mem:/{print \$7}")"
echo "log_age_s: $(( $(date +%s) - $(stat -c %Y "$LOG" 2>/dev/null || echo "$(date +%s)") ))"
echo "last_log: $(tail -1 "$LOG" 2>/dev/null | cut -c1-110)"
echo "recent_errors:"
grep -aiE "error|traceback|exception|no space|killed process|segfault|failed|MemoryError" "$LOG" 2>/dev/null \
  | tail -4 | cut -c1-110 | sed "s/^/  /"
