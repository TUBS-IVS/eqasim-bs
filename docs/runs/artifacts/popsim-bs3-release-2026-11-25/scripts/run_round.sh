#!/usr/bin/env bash
# One round of the PopulationSim 0.10.0+bs.3 release-candidate evidence run (issue #677).
# Usage: run_round.sh <batch_tag> <round> <min_free_gb> <arm>=<tree>=<batch_src_dir> [...]
# For every spec: copy the prepared batch folder to ~/i677/rel/<tag>_<arm>_r<round>/ (output/ emptied),
# check that populationsim is imported from the arm's code tree, start PopulationSim under GNU time with
# PYTHONPATH=<tree> and wait for all arms of the round (they start at the same time).
# Before the start: record uptime and the number of foreign java/populationsim processes (processes whose
# command line does not mention i677) in <tag>_r<round>.load; wait until MemAvailable >= <min_free_gb>
# (the options-off arm peaks at about 32 GB RSS on a 94 GB server that a foreign job shares).
# Command and thread caps as run_round.sh of popsim-codex-variants-2026-11-25.
set -euo pipefail
TAG="$1"; RND="$2"; MINGB="$3"; shift 3
ROOT="$HOME/i677/rel"
PY="$HOME/i646/env_bs2/.venv/bin/python"
export OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 MKL_NUM_THREADS=1 NUMEXPR_NUM_THREADS=1
mkdir -p "$ROOT"
waited=0
while :; do
  avail_gb=$(awk '/MemAvailable/ {printf "%d", $2/1048576}' /proc/meminfo)
  [ "$avail_gb" -ge "$MINGB" ] && break
  echo "waiting for memory: ${avail_gb} GB available, need ${MINGB}" >&2
  sleep 60; waited=$((waited + 60))
  [ "$waited" -le 7200 ] || { echo "refuse: memory never free" >&2; exit 1; }
done
{
  echo "start=$(date -Is)"
  echo "uptime=$(uptime)"
  echo "foreign_procs=$(ps -eo args | grep -E 'java|populationsim' | grep -v grep | grep -vc '/i677/' || true)"
  echo "mem_available_gb=${avail_gb}"
  echo "waited_s=${waited}"
} > "$ROOT/${TAG}_r${RND}.load"
for spec in "$@"; do
  IFS== read -r arm tree src <<< "$spec"
  d="$ROOT/${TAG}_${arm}_r${RND}"
  [ ! -e "$d" ] || { echo "refuse: $d exists" >&2; exit 1; }
  pyfile=$(cd /tmp && PYTHONPATH="$tree" "$PY" -c "import populationsim; print(populationsim.__file__)")
  case "$pyfile" in "$tree"/*) ;; *) echo "refuse: populationsim imported from $pyfile, not $tree" >&2; exit 1;; esac
  mkdir -p "$d"
  cp -r "$src/configs" "$src/data" "$d/"
  mkdir "$d/output"
  echo "$pyfile" > "$d.pyfile"
done
for spec in "$@"; do
  IFS== read -r arm tree src <<< "$spec"
  d="$ROOT/${TAG}_${arm}_r${RND}"
  ( cd "$d" && PYTHONPATH="$tree" /usr/bin/time -v -o "$d.time" "$PY" -m populationsim -w "$d" \
      > "$d.stdout" 2>&1; echo "exit $?" > "$d.exit" ) &
done
wait
for spec in "$@"; do
  IFS== read -r arm tree src <<< "$spec"
  d="$ROOT/${TAG}_${arm}_r${RND}"
  sha256sum "$d/output/final_expanded_household_ids.csv" "$d/output/synthetic_households.csv" \
    "$d/output/synthetic_persons.csv" > "$d.sha256" 2>&1 || true
done
echo done > "$ROOT/${TAG}_r${RND}.DONE"
