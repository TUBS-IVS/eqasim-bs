#!/usr/bin/env bash
#
# sample_load.sh - sample machine CPU/RAM utilization to a CSV during a run.
#
# Writes one row every INTERVAL seconds: ISO timestamp, CPU busy % across ALL
# cores (from /proc/stat deltas), RAM used (GB), and the core count. Paired with
# braunschweig.analysis.runtime, which joins these samples to each synpp stage's
# time window to show per-stage CPU utilization -- in particular "cores_busy ~ 1"
# flags a stage that is stuck on a single core (e.g. a serial solver).
#
# Usage (run in the background, kill when the pipeline finishes):
#   bash scripts/sample_load.sh logs/run_..._samples.csv 15 &
#   SAMPLER_PID=$!
#   ... run pipeline ...
#   kill "$SAMPLER_PID"
#
# Intentionally dependency-free (no psutil): reads /proc/stat and free.

set -u

out="${1:?usage: sample_load.sh <output.csv> [interval_seconds]}"
interval="${2:-15}"

echo "ts,cpu_pct,mem_used_gb,nproc" > "$out"
np="$(nproc)"

prev_idle=0
prev_total=0
first=1

while true; do
    # First line of /proc/stat: "cpu user nice system idle iowait irq softirq steal ..."
    read -r _cpu user nice system idle iowait irq softirq steal _rest < /proc/stat
    idle_all=$((idle + iowait))
    total=$((user + nice + system + idle + iowait + irq + softirq + steal))

    if [ "$first" -eq 0 ]; then
        d_idle=$((idle_all - prev_idle))
        d_total=$((total - prev_total))
        cpu_pct=0
        if [ "$d_total" -gt 0 ]; then
            cpu_pct="$(awk "BEGIN{printf \"%.1f\", 100*(1 - $d_idle/$d_total)}")"
        fi
        mem_gb="$(free -g | awk '/Mem:/{print $3}')"
        printf '%s,%s,%s,%s\n' "$(date '+%Y-%m-%dT%H:%M:%S')" "$cpu_pct" "$mem_gb" "$np" >> "$out"
    fi

    prev_idle="$idle_all"
    prev_total="$total"
    first=0
    sleep "$interval"
done
