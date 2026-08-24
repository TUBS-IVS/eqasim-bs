"""Reduce a recorded resource series to the fields a run manifest wants (issue #350).

Acceptance criterion of issue #350: after a run, one can state without further
measurement the peak per-worker RSS, whether a silent phase was working, and the
wall/CPU split per phase. This module produces exactly that, as a JSON record and
as a markdown block that can be pasted into ``docs/runs/<run_id>.yml``.

Two accounting subtleties, both of which would silently fabricate numbers if
ignored:

1. **The tree CPU total is not monotonic.** It is the sum over the processes ALIVE
   at that instant, so a PopulationSim worker exiting takes its accumulated CPU
   time out of the sum. Summing raw differences would report negative work, and a
   drop would look like a stall. Where the series carries per-process rows, CPU is
   therefore accounted PER PID (each pid contributes what it gained while it was
   observed); otherwise only the positive tree increments are summed, which
   undercounts exited workers -- the ``cpu_accounting`` field says which of the two
   produced the number.
2. **A stage's wall clock is measured between its first and last SAMPLE**, so it is
   short by up to one sampling interval at each end. The exact per-stage wall clock
   comes from the run log via ``braunschweig.analysis.runtime``; what this module
   adds is the resource dimension the log cannot carry.

Everything unreadable stays ``None``. A count of zero and "the counter could not be
read" are different statements and are never merged (CLAUDE.md).
"""
from __future__ import annotations

import json
import logging
import os

from braunschweig.progress import format_duration

logger = logging.getLogger(__name__)

# How CPU seconds were accounted; recorded in the summary so a reader knows whether
# the number is exact for the observed window or a documented lower bound.
CPU_ACCOUNTING_PER_PID = "per_pid"
CPU_ACCOUNTING_TREE_DELTA = "tree_delta_lower_bound"
CPU_ACCOUNTING_NONE = "unavailable"

# A step counts as progress if the tree gained at least this much CPU time. A parked
# thread on a timed wait produces noise-level growth; real work produces orders of
# magnitude more (same reasoning as matsim/runtime/process_watchdog.py).
DEFAULT_STALL_MIN_CPU_SECONDS = 1.0

# Shortest span of no-progress steps reported as a stall. 10 minutes is far beyond
# any pause a working stage takes while still turning a silent multi-hour stall into
# a visible record.
DEFAULT_STALL_MIN_SECONDS = 600.0

_KIB_PER_GIB = 1024 * 1024
_BYTES_PER_GIB = 1024 ** 3


def load_series(path) -> list:
    """Read a JSONL series, skipping lines that are not complete JSON objects.

    A killed run's last line is routinely half-written -- that is precisely the run
    whose measurement matters most -- so a truncated tail is dropped with a warning
    instead of failing the whole analysis.
    """
    rows = []
    skipped = 0
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except ValueError:
                skipped += 1
    if skipped:
        logger.warning("[monitoring] %s: skipped %d unparsable line(s) "
                       "(expected for a run that was killed mid-write).", path, skipped)
    return rows


def _minimum(values):
    present = [value for value in values if value is not None]
    return min(present) if present else None


def _maximum(values):
    present = [value for value in values if value is not None]
    return max(present) if present else None


def _counter_difference(rows, field):
    """Last minus first value of a cumulative counter, or ``None`` if unreadable.

    Returns ``None`` when either end is missing -- an unknown start makes the
    difference unknown, and reporting the absolute value instead would silently turn
    "read since boot" into "read during this run" -- and also when the difference
    comes out NEGATIVE: the dmesg ring buffer wraps and block-device counters reset,
    so a negative result means the counter is no longer comparable, not that minus
    eight OOM kills happened.
    """
    present = [row.get(field) for row in rows if row.get(field) is not None]
    if len(present) < 2:
        return None
    difference = present[-1] - present[0]
    if difference < 0:
        logger.warning("[monitoring] counter '%s' decreased over the series (%s -> %s): "
                       "wrapped or reset, reported as unknown.",
                       field, present[0], present[-1])
        return None
    return difference


def _has_process_rows(rows):
    """Do the samples carry per-process CPU numbers to account with?

    An EMPTY process list is not a usable signal: it is what an unreadable tree
    produces, and treating it as per-pid accounting reported 0 CPU seconds for a run
    that was never measured at all (found by the first end-to-end smoke).
    """
    if not rows or not all(row.get("processes") is not None for row in rows):
        return False
    return any(process.get("cpu_seconds") is not None
               for row in rows for process in row["processes"])


def _has_tree_cpu(rows):
    return any(row.get("tree_cpu_seconds") is not None for row in rows)


def _cpu_accounting_for(rows):
    """Which accounting the series supports -- or none at all, stated as such."""
    if _has_process_rows(rows):
        return CPU_ACCOUNTING_PER_PID
    if _has_tree_cpu(rows):
        return CPU_ACCOUNTING_TREE_DELTA
    return CPU_ACCOUNTING_NONE


def _cpu_seconds_per_pid(rows):
    """CPU seconds gained by each pid while it was observed, summed over pids."""
    first_seen = {}
    last_seen = {}
    for row in rows:
        for process in row.get("processes") or []:
            cpu_seconds = process.get("cpu_seconds")
            if cpu_seconds is None:
                continue
            pid = process.get("pid")
            first_seen.setdefault(pid, cpu_seconds)
            last_seen[pid] = cpu_seconds
    return sum(last_seen[pid] - first_seen[pid] for pid in last_seen)


def _cpu_seconds_tree_delta(rows):
    """Sum of the POSITIVE tree-CPU increments (a drop is a worker exiting)."""
    total = 0.0
    previous = None
    for row in rows:
        current = row.get("tree_cpu_seconds")
        if current is None:
            continue
        if previous is not None and current > previous:
            total += current - previous
        previous = current
    return total


def _cpu_seconds(rows, accounting):
    if accounting == CPU_ACCOUNTING_PER_PID:
        return _cpu_seconds_per_pid(rows)
    if accounting == CPU_ACCOUNTING_TREE_DELTA:
        return _cpu_seconds_tree_delta(rows)
    return None


def _wall_seconds(rows):
    times = [row.get("unix_time") for row in rows if row.get("unix_time") is not None]
    if not times:
        return None
    return float(times[-1] - times[0])


def _efficiency(cpu_seconds, wall_seconds, cpu_count):
    """CPU seconds per available core-second: 1.0 means the whole box was busy."""
    if not cpu_seconds or not wall_seconds or not cpu_count:
        return None
    return cpu_seconds / (wall_seconds * cpu_count)


def _peak_process(rows):
    """``(peak_rss_kb, pid, tag)`` of the hungriest process anywhere in the series."""
    best = (None, None, None)
    for row in rows:
        peak = row.get("peak_process_rss_kb")
        if peak is None:
            for process in row.get("processes") or []:
                candidate = process.get("peak_rss_kb")
                if candidate is not None and (best[0] is None or candidate > best[0]):
                    best = (candidate, process.get("pid"), process.get("tag"))
            continue
        if best[0] is None or peak > best[0]:
            best = (peak, row.get("peak_process_rss_pid"), row.get("peak_process_rss_tag"))
    return best


def _filesystem_minima(rows):
    """Minimum free space per filesystem path over the series (disk high-water mark)."""
    minima = {}
    order = []
    for row in rows:
        for filesystem in row.get("filesystems") or []:
            path = filesystem.get("path")
            if path not in minima:
                minima[path] = {"path": path, "min_free_bytes": None, "total_bytes": None}
                order.append(path)
            free = filesystem.get("free_bytes")
            if free is not None:
                current = minima[path]["min_free_bytes"]
                minima[path]["min_free_bytes"] = free if current is None else min(current, free)
            if filesystem.get("total_bytes") is not None:
                minima[path]["total_bytes"] = filesystem["total_bytes"]
    return [minima[path] for path in order]


def _stage_spans(rows):
    """Consecutive runs of samples carrying the same stage tag, in order."""
    spans = []
    for row in rows:
        stage = row.get("stage")
        if spans and spans[-1][0] == stage:
            spans[-1][1].append(row)
        else:
            spans.append((stage, [row]))
    return spans


def _summarize_stages(rows, accounting):
    """Per-stage wall clock, CPU, efficiency and resource peaks.

    Samples of the same stage that are interrupted by another stage (synpp can
    revisit a stage) are merged into one entry, so a stage appears once.
    """
    merged = {}
    order = []
    for stage, span_rows in _stage_spans(rows):
        if stage not in merged:
            merged[stage] = []
            order.append(stage)
        merged[stage].append(span_rows)

    stages = []
    for stage in order:
        spans = merged[stage]
        flat = [row for span in spans for row in span]
        wall = sum(_wall_seconds(span) or 0.0 for span in spans)
        # None, not 0.0, when the series carries no CPU signal at all: a stage that
        # was not measurable must not look like a stage that did no work.
        cpu = (None if accounting == CPU_ACCOUNTING_NONE
               else sum(_cpu_seconds(span, accounting) or 0.0 for span in spans))
        peak_rss, peak_pid, peak_tag = _peak_process(flat)
        cpu_counts = [row.get("cpu_count") for row in flat]
        stages.append({
            "stage": stage,
            "sample_count": len(flat),
            "wall_seconds": wall,
            "cpu_seconds": cpu,
            "cpu_efficiency": _efficiency(cpu, wall, _maximum(cpu_counts)),
            "peak_process_rss_kb": peak_rss,
            "peak_process_rss_pid": peak_pid,
            "peak_process_rss_tag": peak_tag,
            "peak_process_count": _maximum([row.get("process_count") for row in flat]),
            "peak_thread_count": _maximum([row.get("thread_count") for row in flat]),
            "min_memory_available_kb": _minimum(
                [row.get("memory_available_kb") for row in flat]),
        })
    return stages


STEP_PROGRESS = "progress"
STEP_NO_PROGRESS = "no_progress"
STEP_UNMEASURABLE = "unmeasurable"


def _step_cpu_seconds(previous, current, accounting):
    """CPU gained between two samples, or ``None`` when neither carries a signal."""
    if accounting == CPU_ACCOUNTING_NONE:
        return None
    if accounting == CPU_ACCOUNTING_TREE_DELTA and (
            previous.get("tree_cpu_seconds") is None
            or current.get("tree_cpu_seconds") is None):
        return None
    if accounting == CPU_ACCOUNTING_PER_PID and not any(
            process.get("cpu_seconds") is not None
            for row in (previous, current) for process in row.get("processes") or []):
        return None
    return _cpu_seconds([previous, current], accounting)


def _step_verdict(previous, current, accounting, min_cpu_seconds):
    """What happened between two samples: progress, no progress, or not measurable.

    Three independent progress signals, any of which is enough: CPU accumulated, the
    set of processes changed (a worker started or exited), or the log grew. Requiring
    all three would have flagged the healthy chainsolver pool of 2026-08-24 as
    stalled -- the very misdiagnosis this records against.

    The third verdict matters as much as the other two: when there is no CPU signal
    at all, the step is UNMEASURABLE, never a stall. "We could not see it" and "it
    was not working" are different statements and must not be merged.
    """
    previous_size = previous.get("log_size_bytes")
    current_size = current.get("log_size_bytes")
    if (previous_size is not None and current_size is not None
            and current_size > previous_size):
        return STEP_PROGRESS
    if previous.get("process_count") != current.get("process_count"):
        return STEP_PROGRESS

    cpu = _step_cpu_seconds(previous, current, accounting)
    if cpu is None:
        return STEP_UNMEASURABLE
    return STEP_PROGRESS if cpu >= min_cpu_seconds else STEP_NO_PROGRESS


def _find_stalls(rows, accounting, min_cpu_seconds, min_seconds):
    """Spans of measured no-progress steps longer than ``min_seconds``.

    An unmeasurable step ends the current span without reporting it: a stall claim
    must rest on a measurement.
    """
    stalls = []
    span_start = None
    previous = None
    for row in rows:
        if previous is not None:
            verdict = _step_verdict(previous, row, accounting, min_cpu_seconds)
            if verdict == STEP_NO_PROGRESS:
                if span_start is None:
                    span_start = previous
            else:
                if verdict == STEP_PROGRESS:
                    stalls.extend(_closed_stall(span_start, previous, min_seconds))
                span_start = None
        previous = row
    stalls.extend(_closed_stall(span_start, previous, min_seconds))
    return stalls


def _closed_stall(span_start, span_end, min_seconds):
    if span_start is None or span_end is None:
        return []
    seconds = _wall_seconds([span_start, span_end]) or 0.0
    if seconds < min_seconds:
        return []
    return [{
        "stage": span_start.get("stage"),
        "from_timestamp": span_start.get("timestamp"),
        "to_timestamp": span_end.get("timestamp"),
        "seconds": seconds,
    }]


def summarize(rows, stall_min_seconds=DEFAULT_STALL_MIN_SECONDS,
              stall_min_cpu_seconds=DEFAULT_STALL_MIN_CPU_SECONDS) -> dict:
    """Reduce a recorded series to one manifest-ready record."""
    if not rows:
        return {
            "sample_count": 0, "first_timestamp": None, "last_timestamp": None,
            "wall_seconds": None, "sources": [], "cpu_accounting": CPU_ACCOUNTING_NONE,
            "cpu_seconds": None, "cpu_efficiency": None,
            "peak_process_rss_kb": None, "peak_process_rss_pid": None,
            "peak_process_rss_tag": None, "peak_memory_used_kb": None,
            "min_memory_available_kb": None, "peak_swap_used_kb": None,
            "peak_process_count": None, "peak_thread_count": None,
            "disk_read_bytes": None, "disk_write_bytes": None,
            "oom_kill_count": None, "segfault_count": None,
            "filesystems": [], "stages": [], "stalls": [],
        }

    accounting = _cpu_accounting_for(rows)
    wall_seconds = _wall_seconds(rows)
    cpu_seconds = _cpu_seconds(rows, accounting)
    peak_rss, peak_pid, peak_tag = _peak_process(rows)

    return {
        "sample_count": len(rows),
        "first_timestamp": rows[0].get("timestamp"),
        "last_timestamp": rows[-1].get("timestamp"),
        "wall_seconds": wall_seconds,
        "sources": sorted({row.get("source") for row in rows if row.get("source")}),
        "cpu_accounting": accounting,
        "cpu_seconds": cpu_seconds,
        "cpu_efficiency": _efficiency(cpu_seconds, wall_seconds,
                                      _maximum([row.get("cpu_count") for row in rows])),
        "peak_process_rss_kb": peak_rss,
        "peak_process_rss_pid": peak_pid,
        "peak_process_rss_tag": peak_tag,
        "peak_memory_used_kb": _maximum([row.get("memory_used_kb") for row in rows]),
        "min_memory_available_kb": _minimum(
            [row.get("memory_available_kb") for row in rows]),
        "peak_swap_used_kb": _maximum([row.get("swap_used_kb") for row in rows]),
        "peak_process_count": _maximum([row.get("process_count") for row in rows]),
        "peak_thread_count": _maximum([row.get("thread_count") for row in rows]),
        "disk_read_bytes": _counter_difference(rows, "disk_read_bytes"),
        "disk_write_bytes": _counter_difference(rows, "disk_write_bytes"),
        "oom_kill_count": _counter_difference(rows, "oom_kill_count"),
        "segfault_count": _counter_difference(rows, "segfault_count"),
        "filesystems": _filesystem_minima(rows),
        "stages": _summarize_stages(rows, accounting),
        "stalls": _find_stalls(rows, accounting, stall_min_cpu_seconds,
                               stall_min_seconds),
    }


def _gib_from_kb(value_kb):
    return None if value_kb is None else value_kb / _KIB_PER_GIB


def _gib_from_bytes(value_bytes):
    return None if value_bytes is None else value_bytes / _BYTES_PER_GIB


def _format_gib(value):
    return "unknown" if value is None else "%.1f GiB" % value


def _format_count(value):
    return "unknown" if value is None else str(value)


def _format_ratio(value):
    return "unknown" if value is None else "%.2f" % value


def render_markdown(record) -> str:
    """Render a summary as a markdown block for a run manifest.

    Memory is reported in GiB because ``/proc`` reports kB as 1024-byte units;
    naming the binary unit avoids the 7 % error a decimal-GB label would introduce.
    """
    if not record.get("sample_count"):
        return ("### Resource record\n\n"
                "No samples were recorded, so no resource statement can be made.\n")

    lines = ["### Resource record", ""]
    lines.append("- samples: %d (source: %s), wall clock %s"
                 % (record["sample_count"], ", ".join(record["sources"]) or "unknown",
                    format_duration(record["wall_seconds"] or 0.0)))
    lines.append("- peak process RSS: %s (pid %s, `%s`)"
                 % (_format_gib(_gib_from_kb(record["peak_process_rss_kb"])),
                    _format_count(record["peak_process_rss_pid"]),
                    record["peak_process_rss_tag"] or "unknown"))
    lines.append("- system memory: peak used %s, minimum available %s, peak swap used %s"
                 % (_format_gib(_gib_from_kb(record["peak_memory_used_kb"])),
                    _format_gib(_gib_from_kb(record["min_memory_available_kb"])),
                    _format_gib(_gib_from_kb(record["peak_swap_used_kb"]))))
    lines.append("- peak processes / threads: %s / %s"
                 % (_format_count(record["peak_process_count"]),
                    _format_count(record["peak_thread_count"])))
    lines.append("- CPU: %s s accounted as `%s`, efficiency %s of the machine"
                 % (_format_count(None if record["cpu_seconds"] is None
                                  else int(record["cpu_seconds"])),
                    record["cpu_accounting"], _format_ratio(record["cpu_efficiency"])))
    lines.append("- disk: read %s, written %s"
                 % (_format_gib(_gib_from_bytes(record["disk_read_bytes"])),
                    _format_gib(_gib_from_bytes(record["disk_write_bytes"]))))
    for filesystem in record["filesystems"]:
        lines.append("- minimum free space on `%s`: %s"
                     % (filesystem["path"],
                        _format_gib(_gib_from_bytes(filesystem["min_free_bytes"]))))
    lines.append("- kernel events during the run: %s OOM kill(s), %s segfault(s)"
                 % (_format_count(record["oom_kill_count"]),
                    _format_count(record["segfault_count"])))

    lines.extend(["", "| stage | wall | CPU s | efficiency | peak process RSS | procs | threads |",
                  "|---|---|---|---|---|---|---|"])
    for stage in record["stages"]:
        lines.append("| %s | %s | %s | %s | %s | %s | %s |"
                     % (stage["stage"] or "(no stage tag)",
                        format_duration(stage["wall_seconds"] or 0.0),
                        int(stage["cpu_seconds"] or 0),
                        _format_ratio(stage["cpu_efficiency"]),
                        _format_gib(_gib_from_kb(stage["peak_process_rss_kb"])),
                        _format_count(stage["peak_process_count"]),
                        _format_count(stage["peak_thread_count"])))

    lines.append("")
    if record["stalls"]:
        lines.append("Spans without any progress signal (no CPU, no process change, "
                     "no log growth):")
        for stall in record["stalls"]:
            lines.append("- `%s` %s -> %s (%s)"
                         % (stall["stage"] or "(no stage tag)", stall["from_timestamp"],
                            stall["to_timestamp"], format_duration(stall["seconds"])))
    else:
        lines.append("No span without a progress signal was recorded.")
    return "\n".join(lines) + "\n"


def write_summary(series_path, json_path=None, markdown_path=None, extra=None) -> dict:
    """Summarise a series file and write the JSON + markdown artifacts beside it.

    ``extra`` is merged into the record (the recorder adds its own counters, e.g.
    how many samples failed), so the artifact states the quality of its own data.
    """
    base = os.path.splitext(str(series_path))[0]
    record = summarize(load_series(series_path))
    if extra:
        record.update(extra)

    json_path = json_path or base + ".summary.json"
    markdown_path = markdown_path or base + ".summary.md"
    with open(json_path, "w", encoding="utf-8") as handle:
        json.dump(record, handle, indent=2, sort_keys=True)
    with open(markdown_path, "w", encoding="utf-8") as handle:
        handle.write(render_markdown(record))
    logger.info("[monitoring] summary written: %s and %s", json_path, markdown_path)
    return record
