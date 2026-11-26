#!/usr/bin/env bash
# Driver of the 0.10.0+bs.3 release-candidate evidence run (issue #677).
# Arms: bs2_on = tree A (d9a2e28) on the controlled_on* folders; rc_on = tree RC (4b54064) on the same folders;
# rc_off = tree RC on the controlled_off* folders (both run-time options off, runs once per batch).
# Rounds, one after the other (at most 3 of our processes at once):
#   batch_028 x 3: bs2_on + rc_on start together (timing pair)
#   batch_000: bs2_on + rc_on + rc_off together;  batch_032: the same
#   batch_028: rc_off alone as round 4 (about 32 GB RSS, kept out of the timing rounds)
set -uo pipefail
R="$HOME/i677/rel"
T="$HOME/i677/trees"
EV="$HOME/i646/ev"
run() { "$(dirname "$(readlink -f "$0")")/run_round.sh" "$@" || echo "ROUND FAILED: $*" >> "$R/run_all.errors"; }
mkdir -p "$R"
for r in 1 2 3; do
  run b028 "$r" 20 "bs2_on=$T/A=$EV/controlled_on_b028/batch_028" "rc_on=$T/RC=$EV/controlled_on_b028/batch_028"
done
run b000 1 45 "bs2_on=$T/A=$EV/controlled_on/batch_000" "rc_on=$T/RC=$EV/controlled_on/batch_000" \
  "rc_off=$T/RC=$EV/controlled_off/batch_000"
run b032 1 45 "bs2_on=$T/A=$EV/controlled_on/batch_032" "rc_on=$T/RC=$EV/controlled_on/batch_032" \
  "rc_off=$T/RC=$EV/controlled_off/batch_032"
run b028 4 45 "rc_off=$T/RC=$EV/controlled_off_b028/batch_028"
echo done > "$R/ALL_DONE"
