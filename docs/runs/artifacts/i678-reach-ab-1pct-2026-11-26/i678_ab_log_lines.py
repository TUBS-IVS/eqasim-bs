"""Select the log lines of one arm of the #678 1 % comparison from the launcher log of an isolated run.

Usage: python i678_ab_log_lines.py RUN_LOG

Prints, with the line number of the server log: the stage start lines with their time, the standalone runner's
exclusion and kept-trip lines, the last running line of the border-tour vehicle relaxation, the per-iteration kept-trip
lines of the MATSim loop (FilteredTripCounter), the plan check and lift summary lines, every line naming an Exception or
a Traceback, and the exit line of the launcher. Home paths are written as ~. Standard library only.
"""
import os
import re
import sys

PATTERNS = (
    re.compile(r"Executing stage "),
    re.compile(r"Excluded subpopulations"),
    re.compile(r"Standalone mode choice: "),
    re.compile(r"FilteredTripCounter"),
    re.compile(r"\[plan_check\]|\[frozen_pt_walk_lift\]|\[initial_mode_choice\]|\[mode_choice_reach\]"),
    re.compile(r"Exception|Traceback"),
    re.compile(r"\[isolated-run\]"),
    re.compile(r"[Ss]tuck"),
)
RELAXED = re.compile(r"relaxed for missing availability")
NOISE = re.compile(r"StuckAnalysis(Listener|Handler)|SLF4JLoggingException|PersonStuckEvent|StuckAgentDashboard|"
                   r"calling notify")


def main():
    home = os.path.expanduser("~")
    last_relaxed = None
    with open(sys.argv[1], encoding="utf-8", errors="replace") as handle:
        for number, line in enumerate(handle, 1):
            text = line.rstrip().replace(home, "~")
            if RELAXED.search(text):
                last_relaxed = (number, text)
                continue
            if NOISE.search(text):
                continue
            if any(p.search(text) for p in PATTERNS):
                print(f"{number}: {text[:600]}")
    if last_relaxed:
        print(f"{last_relaxed[0]}: (last relaxation line) {last_relaxed[1][:600]}")


if __name__ == "__main__":
    main()
