"""Live TTY dashboard for the popsim_mid PopulationSim batch phase.

Read-only: it inspects the per-batch folders under a popsim work_dir and prints an
OVERALL progress bar plus one self-refreshing line per batch (status + phase +
elapsed). It NEVER touches the running pipeline.

Per-batch status is derived from the batch's ``output/`` dir:
  DONE  -- synthetic_households.csv written (PopulationSim finished this batch)
  RUN   -- populationsim.log present + recently modified (shows the current phase
           and, during integerizing, an OPTIMAL/INFEASIBLE zone tally)
  WAIT  -- folder assembled but PopulationSim has not started writing yet

PopulationSim emits no clean per-batch percentage (it balances, then integerizes an
a-priori-unknown number of zones), so per-batch shows phase, not a percent bar; the
OVERALL bar (finished/total) is exact.

Usage (on the server):
  python scripts/monitor_popsim_batches.py [--work-dir <popsim_work>] [--interval 3]
"""
from __future__ import annotations

import argparse
import glob
import os
import time

# Default to the all-features 1% run's work_dir; override with --work-dir.
DEFAULT_WORK_DIR = os.path.expanduser(
    "~/eqasim-bs/eqasim-data/cache_bs_1pct_allfeat_popsim/popsim_work")


def _phase_from_log(text_tail: str) -> str:
    """Coarse PopulationSim phase from the tail of a batch's populationsim.log."""
    # Latest-wins ordering: check the later pipeline steps first.
    if "write_synthetic_population" in text_tail or "expand_population" in text_tail:
        return "expand/write"
    if "integeriz" in text_tail.lower():
        return "integerize"
    if "sub_balancing" in text_tail:
        return "sub-balance"
    if "final_seed_balancing" in text_tail or "meta_control_factoring" in text_tail:
        return "final-balance"
    if "initial_seed_balancing" in text_tail or "balancer" in text_tail:
        return "seed-balance"
    if "setup_data_structures" in text_tail or "input_pre_processor" in text_tail:
        return "setup"
    return "running"


def _batch_state(batch_dir: str):
    """Return (status, phase, extra, mtime) for one batch folder."""
    out = os.path.join(batch_dir, "output")
    done_marker = os.path.join(out, "synthetic_households.csv")
    log = os.path.join(out, "populationsim.log")
    if os.path.exists(done_marker):
        return "DONE", "", "", os.path.getmtime(done_marker)
    if os.path.exists(log):
        try:
            with open(log, "r", encoding="utf-8", errors="replace") as f:
                # Read only the tail for speed.
                f.seek(0, os.SEEK_END)
                size = f.tell()
                f.seek(max(0, size - 20000))
                tail = f.read()
        except OSError:
            tail = ""
        phase = _phase_from_log(tail)
        extra = ""
        if phase == "integerize":
            opt = tail.count(": OPTIMAL")
            inf = tail.count("Integerizer failed for")
            extra = "zones ok=%d infeasible=%d" % (opt, inf)
        return "RUN", phase, extra, os.path.getmtime(log)
    return "WAIT", "", "", os.path.getmtime(batch_dir)


def _bar(done: int, total: int, width: int = 30) -> str:
    if total <= 0:
        return "[" + "-" * width + "]"
    filled = int(round(width * done / total))
    return "[" + "#" * filled + "-" * (width - filled) + "]"


def render(work_dir: str) -> str:
    batches = sorted(glob.glob(os.path.join(work_dir, "batch_*")))
    if not batches:
        return "no batch folders yet under %s" % work_dir
    rows = []
    done = 0
    now = time.time()
    for b in batches:
        status, phase, extra, mtime = _batch_state(b)
        if status == "DONE":
            done += 1
        age = int(now - mtime)
        name = os.path.basename(b)
        detail = phase + (" | " + extra if extra else "")
        rows.append("  %-12s %-5s %-14s %ds ago" % (name, status, detail, age))
    total = len(batches)
    pct = 100.0 * done / total if total else 0.0
    header = "popsim batches  %s %d/%d (%.0f%%)" % (_bar(done, total), done, total, pct)
    return header + "\n" + "\n".join(rows)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--work-dir", default=DEFAULT_WORK_DIR)
    parser.add_argument("--interval", type=float, default=3.0)
    parser.add_argument("--once", action="store_true", help="print once and exit")
    args = parser.parse_args(argv)
    while True:
        frame = render(args.work_dir)
        # Clear screen (ANSI) then draw; works in any VT100 terminal.
        print("\033[2J\033[H" + frame, flush=True)
        if args.once:
            return 0
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
