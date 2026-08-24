"""Record or summarise the resource series of a pipeline run (issue #350).

The pipeline records itself (``scripts/run_synpp.py`` starts the recorder for every
run), so this CLI exists for the two things that wiring cannot do:

1. **Attach to a run that is already in flight.** A 100 % run started before this
   feature existed, or one launched by hand in a tmux session, can still be
   recorded from the outside -- read-only, no root, no restart.
2. **Turn a finished series into the block a run manifest wants**, which is also
   how a series recorded by the pipeline is read afterwards.

Examples::

    # attach to a running pipeline (its whole process tree) and record every 30 s
    python scripts/monitor_run.py record --pattern "synpp" \
        --log ~/eqasim-bs/logs/run_20260824T060000.log \
        --out ~/eqasim-bs/logs/monitoring/resource_series_manual.jsonl

    # reduce a recorded series to peak RSS, per-stage wall/CPU and stalls
    python scripts/monitor_run.py summarize <series>.jsonl

Read-only and dependency-free: everything comes from ``/proc``, ``shutil`` and
``dmesg``; nothing is killed, restarted or written outside the given output paths.
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import time

# Running this file as a script puts scripts/ on sys.path[0] (NOT the repo root),
# so the braunschweig package would not resolve. Prepend the repo root, exactly as
# scripts/run_synpp.py does.
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from braunschweig.monitoring import process_tree, recorder, sampler, summary

logger = logging.getLogger("braunschweig.monitoring.cli")


def resolve_pid_by_pattern(pattern, proc_root=None):
    """Pid of the TOPMOST process whose command line contains ``pattern``.

    "Topmost" means a match whose parent is not itself a match, with the lowest pid
    breaking a tie: attaching to a worker instead of to the driver would record a
    subtree and miss exactly the processes the question is about. Returns ``None``
    when nothing matches -- never a guess.
    """
    root = process_tree.DEFAULT_PROC_ROOT if proc_root is None else str(proc_root)
    matches = {}
    try:
        entries = sorted(entry.name for entry in os.scandir(root) if entry.name.isdigit())
    except OSError:
        return None

    for name in entries:
        pid = int(name)
        process = process_tree.read_process(pid, proc_root=root)
        if process is not None and pattern in process.tag:
            matches[pid] = process.ppid

    if not matches:
        return None
    topmost = [pid for pid, ppid in matches.items() if ppid not in matches]
    return min(topmost) if topmost else min(matches)


def _build_parser():
    parser = argparse.ArgumentParser(
        prog="monitor_run.py",
        description="Record a pipeline run's resource time series, or summarise one.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    record = subparsers.add_parser(
        "record", help="sample a live process tree into a JSONL series")
    record.add_argument("--pid", type=int, default=None,
                        help="root pid of the tree to record")
    record.add_argument("--pattern", default=None,
                        help="resolve the root pid from a command-line substring")
    record.add_argument("--out", default=None,
                        help="series file (default: ./monitoring/resource_series_<ts>.jsonl)")
    record.add_argument("--interval-seconds", type=float,
                        default=recorder.DEFAULT_INTERVAL_SECONDS,
                        help="sampling interval in seconds (default: %(default)s)")
    record.add_argument("--duration-seconds", type=float, default=None,
                        help="stop after this many seconds (0 = one single sample; "
                             "omitted = until interrupted)")
    record.add_argument("--log", default=None,
                        help="run log to watch for growth and for the current stage")
    record.add_argument("--filesystem", action="append", default=[],
                        help="path whose free space is recorded (repeatable)")
    record.add_argument("--proc-root", default=None,
                        help="alternative /proc root (testing, containers)")
    record.add_argument("--no-process-rows", action="store_true",
                        help="record aggregates only, without the per-process detail")
    record.add_argument("--no-kernel-events", action="store_true",
                        help="do not probe dmesg for OOM kills and segfaults")
    record.add_argument("--no-summary", action="store_true",
                        help="do not write the summary artifacts when recording ends")

    summarize = subparsers.add_parser(
        "summarize", help="reduce a recorded series to manifest-ready fields")
    summarize.add_argument("series", help="the JSONL series written by record")
    summarize.add_argument("--json", default=None, help="summary JSON output path")
    summarize.add_argument("--markdown", default=None, help="summary markdown output path")
    summarize.add_argument("--quiet", action="store_true",
                           help="write the artifacts without printing the block")
    return parser


def _record(args) -> int:
    pid = args.pid
    if pid is None and args.pattern:
        pid = resolve_pid_by_pattern(args.pattern, proc_root=args.proc_root)
        if pid is None:
            print("no process matches --pattern %r; nothing recorded." % args.pattern,
                  file=sys.stderr)
            return 2
        logger.info("[monitoring] --pattern %r resolved to pid %d", args.pattern, pid)
    if pid is None:
        print("either --pid or --pattern is required.", file=sys.stderr)
        return 2

    series_path = args.out or recorder.series_path_for_run(
        os.path.join(os.getcwd(), recorder.DEFAULT_OUTPUT_SUBDIRECTORY))

    resource_sampler = sampler.ResourceSampler(
        root_pid=pid, proc_root=args.proc_root, log_path=args.log,
        filesystem_paths=args.filesystem,
        include_process_rows=not args.no_process_rows,
        collect_kernel_events=not args.no_kernel_events)
    resource_recorder = recorder.ResourceRecorder(series_path, resource_sampler,
                                                 interval_seconds=args.interval_seconds)

    # A monitor is routinely ended with SIGTERM (``kill``) or Ctrl-C; both must end
    # in the summary being written, not in a lost series.
    def _request_stop(signal_number, frame):
        logger.info("[monitoring] signal %s received -> stopping.", signal_number)
        resource_recorder.request_stop()

    for signal_name in ("SIGTERM", "SIGINT"):
        handler = getattr(signal, signal_name, None)
        if handler is not None:
            try:
                signal.signal(handler, _request_stop)
            except (OSError, ValueError):
                pass  # not the main thread, or unsupported on this platform

    deadline = (None if args.duration_seconds is None
                else time.monotonic() + args.duration_seconds)
    try:
        while True:
            resource_recorder.sample_once()
            if deadline is not None and time.monotonic() >= deadline:
                break
            if resource_recorder.wait_for_stop(args.interval_seconds):
                break
    except KeyboardInterrupt:
        logger.info("[monitoring] interrupted.")
    finally:
        print("%d sample(s) written to %s (%d failed)."
              % (resource_recorder.written_sample_count, series_path,
                 resource_recorder.failed_sample_count))
        if not args.no_summary:
            resource_recorder.write_summary()
    return 0


def _summarize(args) -> int:
    if not os.path.isfile(args.series):
        print("series file not found: %s" % args.series, file=sys.stderr)
        return 1

    record = summary.write_summary(args.series, json_path=args.json,
                                   markdown_path=args.markdown)
    if not args.quiet:
        print(summary.render_markdown(record))
    return 0


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
                        datefmt="%Y-%m-%dT%H:%M:%S")
    args = _build_parser().parse_args(sys.argv[1:] if argv is None else argv)
    if args.command == "record":
        return _record(args)
    return _summarize(args)


if __name__ == "__main__":
    raise SystemExit(main())
