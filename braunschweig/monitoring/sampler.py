"""One flat resource sample of a running pipeline (issue #350).

A sample joins three things that were previously read by hand, separately, and
never written down:

1. the process tree (:mod:`braunschweig.monitoring.process_tree`) -- tree CPU,
   per-process ``VmHWM``, thread total, process states,
2. the system state -- RAM, swap, load, free disk space per filesystem, disk IO,
   and the kernel's own OOM-kill / segfault record,
3. the liveness of the work itself -- size and mtime age of the run log, plus the
   stage the pipeline is currently executing.

The stage tag is what makes a series analysable: every sample carries the stage
that produced it, so wall clock, CPU and memory peaks can be attributed per phase
afterwards. It is read incrementally (only the bytes appended since the previous
poll), because a 100 % run log grows to hundreds of megabytes and re-reading it
every 30 seconds would itself become the load.

Units are explicit in every field name (``_kb``, ``_bytes``, ``_seconds``). Any
value that cannot be read is ``None`` and the accompanying ``source`` field names
the reason -- a missing counter must never be mistaken for a measured zero
(CLAUDE.md, fallback transparency).
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time

from braunschweig.monitoring import process_tree

logger = logging.getLogger(__name__)

# Sample provenance for the kernel-event probe.
SOURCE_DMESG = "dmesg"
SOURCE_UNAVAILABLE = "unavailable"

# /proc/diskstats reports IO in 512-byte sectors regardless of the device's own
# block size (proc(5), Documentation/admin-guide/iostats.rst).
DISKSTATS_SECTOR_BYTES = 512

# Seconds allowed for the kernel-log probe. It is a diagnostic, so it must never
# be able to delay a sample noticeably.
DMESG_TIMEOUT_SECONDS = 10.0

# The pipeline's stage markers, written by synpp into the run log. The same lines
# are parsed after the fact by ``braunschweig.analysis.runtime`` for per-stage
# durations; here they only tag the live sample with its stage.
_STAGE_START_RE = re.compile(r"Executing stage (\S+)")
_STAGE_FINISH_RE = re.compile(r"Finished running (\S+?)\.?\s*$")

# Kernel log markers of the two failure classes this pipeline has actually hit:
# the OOM killer removing PopulationSim workers, and libc segfaults from BLAS
# thread oversubscription (see braunschweig/parallelism.py).
_OOM_KILL_RE = re.compile(r"Killed process", re.IGNORECASE)
_SEGFAULT_RE = re.compile(r"segfault", re.IGNORECASE)

# Longest log tail scanned in one poll. A pathological burst (a stage writing
# hundreds of MB between two samples) is truncated to the newest bytes rather than
# read whole, so the sampler cannot become the bottleneck.
MAX_LOG_CHUNK_BYTES = 8 * 1024 * 1024


def _read_proc_file(proc_root, name):
    path = os.path.join(process_tree.DEFAULT_PROC_ROOT if proc_root is None
                        else str(proc_root), name)
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except OSError:
        return None


def read_memory(proc_root=None) -> dict:
    """RAM and swap in kB from ``/proc/meminfo``; unreadable fields stay ``None``.

    ``memory_used_kb`` is the physically used amount (``MemTotal - MemFree``);
    ``memory_available_kb`` is the kernel's own estimate of what a new allocation
    could still get, which is the number that predicts an OOM kill.
    """
    text = _read_proc_file(proc_root, "meminfo")
    values = {}
    if text:
        for line in text.splitlines():
            parts = line.split()
            if len(parts) >= 2:
                try:
                    values[parts[0].rstrip(":")] = int(parts[1])
                except ValueError:
                    continue

    total = values.get("MemTotal")
    free = values.get("MemFree")
    swap_total = values.get("SwapTotal")
    swap_free = values.get("SwapFree")
    return {
        "memory_total_kb": total,
        "memory_available_kb": values.get("MemAvailable"),
        "memory_free_kb": free,
        "memory_used_kb": None if total is None or free is None else total - free,
        "swap_total_kb": swap_total,
        "swap_used_kb": (None if swap_total is None or swap_free is None
                         else swap_total - swap_free),
    }


def read_load(proc_root=None) -> dict:
    """Load averages and CPU count, the denominator for a utilisation statement."""
    load_text = _read_proc_file(proc_root, "loadavg")
    loads = [None, None, None]
    if load_text:
        parts = load_text.split()
        for index in range(min(3, len(parts))):
            try:
                loads[index] = float(parts[index])
            except ValueError:
                loads[index] = None

    cpu_text = _read_proc_file(proc_root, "cpuinfo")
    if cpu_text:
        cpu_count = sum(1 for line in cpu_text.splitlines()
                        if line.startswith("processor")) or None
    else:
        cpu_count = os.cpu_count()

    return {
        "load_average_1min": loads[0],
        "load_average_5min": loads[1],
        "load_average_15min": loads[2],
        "cpu_count": cpu_count,
    }


def read_disk_io(proc_root=None) -> dict:
    """Cumulative bytes read/written by all block devices, from ``/proc/diskstats``.

    Partitions are counted alongside their parent device in ``diskstats``, so the
    absolute totals are an upper bound; what a series uses is the DIFFERENCE
    between two samples, which is unaffected by that constant double count as long
    as the device set does not change.
    """
    text = _read_proc_file(proc_root, "diskstats")
    if not text:
        return {"disk_read_bytes": None, "disk_write_bytes": None}

    sectors_read = sectors_written = 0
    seen = False
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 10:
            continue
        try:
            sectors_read += int(parts[5])
            sectors_written += int(parts[9])
        except ValueError:
            continue
        seen = True

    if not seen:
        return {"disk_read_bytes": None, "disk_write_bytes": None}
    return {
        "disk_read_bytes": sectors_read * DISKSTATS_SECTOR_BYTES,
        "disk_write_bytes": sectors_written * DISKSTATS_SECTOR_BYTES,
    }


def read_filesystems(paths) -> list:
    """Free and total bytes for each configured path (ENOSPC is a known failure class).

    Uses ``shutil.disk_usage`` rather than parsing ``df`` so the same code works on
    the Linux run server and on a Windows development machine. A path that cannot
    be queried reports ``None`` instead of being dropped, so the series shows that
    the question was asked.
    """
    filesystems = []
    for path in paths or []:
        try:
            usage = shutil.disk_usage(str(path))
            filesystems.append({
                "path": str(path),
                "free_bytes": int(usage.free),
                "total_bytes": int(usage.total),
                "used_bytes": int(usage.used),
            })
        except OSError:
            filesystems.append({
                "path": str(path),
                "free_bytes": None,
                "total_bytes": None,
                "used_bytes": None,
            })
    return filesystems


def _dmesg_text():
    """Kernel ring buffer text, or ``None`` where it cannot be read.

    ``dmesg`` is unavailable on Windows and may be restricted
    (``kernel.dmesg_restrict``) on a hardened host; both cases are reported as
    "unavailable" rather than as zero events.
    """
    try:
        completed = subprocess.run(["dmesg", "-T"], capture_output=True, text=True,
                                   timeout=DMESG_TIMEOUT_SECONDS, check=False)
    except (OSError, subprocess.SubprocessError):
        return None
    if completed.returncode != 0:
        return None
    return completed.stdout


def read_kernel_events(dmesg_reader=_dmesg_text) -> dict:
    """Cumulative OOM-kill and segfault counts from the kernel log.

    The 2026-08-20 run lost 7 processes to the OOM killer and an earlier run lost
    12 PopulationSim batches to libc segfaults; both were reconstructed by hand
    afterwards. Counting them per sample turns them into a timestamped record.
    Counts are ``None`` -- not 0 -- when the kernel log cannot be read at all.
    """
    text = dmesg_reader()
    if text is None:
        return {"oom_kill_count": None, "segfault_count": None,
                "kernel_events_source": "%s:dmesg_unreadable" % SOURCE_UNAVAILABLE,
                "source": "%s:dmesg_unreadable" % SOURCE_UNAVAILABLE}
    return {
        "oom_kill_count": len(_OOM_KILL_RE.findall(text)),
        "segfault_count": len(_SEGFAULT_RE.findall(text)),
        "kernel_events_source": SOURCE_DMESG,
        "source": SOURCE_DMESG,
    }


class LogTail:
    """Incremental reader of the run log: which stage runs, and is the log growing.

    Keeps a byte offset and reads only what was appended since the previous
    :meth:`poll`, so polling a hundreds-of-megabytes run log every 30 seconds costs
    the size of the new lines rather than the size of the file. A log that shrank
    (truncated or rotated) resets the offset instead of returning nonsense.
    """

    def __init__(self, log_path, clock=time.time):
        self.log_path = str(log_path) if log_path else None
        self.clock = clock
        self.offset = 0
        self.bytes_read_total = 0
        self.stage = None
        self.stage_finished = False

    def poll(self) -> dict:
        """Read the appended bytes and return the current log/stage state."""
        if not self.log_path:
            return self._state(size_bytes=None, mtime_age_seconds=None)

        try:
            stat = os.stat(self.log_path)
        except OSError:
            return self._state(size_bytes=None, mtime_age_seconds=None)

        if stat.st_size < self.offset:
            # Truncated or rotated: start over rather than read from a stale offset.
            self.offset = 0
        if stat.st_size > self.offset:
            self._consume(from_offset=max(self.offset,
                                          stat.st_size - MAX_LOG_CHUNK_BYTES),
                          to_offset=stat.st_size)

        return self._state(size_bytes=stat.st_size,
                           mtime_age_seconds=max(0.0, self.clock() - stat.st_mtime))

    def _consume(self, from_offset, to_offset):
        try:
            with open(self.log_path, "rb") as handle:
                handle.seek(from_offset)
                chunk = handle.read(to_offset - from_offset)
        except OSError:
            return
        self.offset = to_offset
        self.bytes_read_total += len(chunk)

        for line in chunk.decode("utf-8", errors="replace").splitlines():
            start = _STAGE_START_RE.search(line)
            if start:
                self.stage = start.group(1)
                self.stage_finished = False
                continue
            finish = _STAGE_FINISH_RE.search(line)
            if finish and self.stage and finish.group(1) == self.stage:
                self.stage_finished = True

    def _state(self, size_bytes, mtime_age_seconds) -> dict:
        return {
            "stage": self.stage,
            "stage_finished": self.stage_finished,
            "log_size_bytes": size_bytes,
            "log_mtime_age_seconds": mtime_age_seconds,
        }


class ResourceSampler:
    """Produces one flat sample dict per call, ready to append to a JSONL series.

    All external readers are injectable (``proc_root``, ``dmesg_reader``, ``clock``)
    so the whole sampler is testable off Linux and deterministically.

    ``include_process_rows=False`` drops the per-process detail and keeps only the
    aggregates and the peak process, for a long run where the series size matters.
    """

    def __init__(self, root_pid, proc_root=None, log_path=None, filesystem_paths=(),
                 dmesg_reader=_dmesg_text, clock=time.time,
                 clock_ticks_per_second=None, include_process_rows=True,
                 collect_kernel_events=True):
        self.root_pid = int(root_pid)
        self.proc_root = proc_root
        self.filesystem_paths = list(filesystem_paths or [])
        self.dmesg_reader = dmesg_reader
        self.clock = clock
        self.clock_ticks_per_second = clock_ticks_per_second
        self.include_process_rows = include_process_rows
        self.collect_kernel_events = collect_kernel_events
        self.log_tail = LogTail(log_path, clock=clock)
        self.sample_index = 0

    def sample(self) -> dict:
        """One sample of tree, system state and work liveness at this instant."""
        now = self.clock()
        tree = process_tree.sample_tree(
            self.root_pid, proc_root=self.proc_root,
            clock_ticks_per_second=self.clock_ticks_per_second)

        row = {
            "sample_index": self.sample_index,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
            "unix_time": now,
            "root_pid": tree.root_pid,
            "tree_cpu_seconds": tree.cpu_seconds,
            "process_count": tree.process_count,
            "thread_count": tree.thread_count,
            "source": tree.source,
        }
        row.update(self._tree_aggregates(tree))
        row.update(self.log_tail.poll())
        row.update(read_memory(proc_root=self.proc_root))
        row.update(read_load(proc_root=self.proc_root))
        row.update(read_disk_io(proc_root=self.proc_root))
        row["filesystems"] = read_filesystems(self.filesystem_paths)

        if self.collect_kernel_events:
            events = read_kernel_events(dmesg_reader=self.dmesg_reader)
            row["oom_kill_count"] = events["oom_kill_count"]
            row["segfault_count"] = events["segfault_count"]
            row["kernel_events_source"] = events["kernel_events_source"]

        if self.include_process_rows:
            row["processes"] = [process.to_dict() for process in tree.processes]

        self.sample_index += 1
        return row

    def _tree_aggregates(self, tree) -> dict:
        """Peak/current memory and process-state counts across the tree.

        The peak process is kept explicitly (value, pid, tag) because "which
        process needed the most memory" is the question ``num_workers`` tuning
        actually asks (issue #281).
        """
        peak_process = None
        for process in tree.processes:
            if process.peak_rss_kb is None:
                continue
            if peak_process is None or process.peak_rss_kb > peak_process.peak_rss_kb:
                peak_process = process

        current = [process.rss_kb for process in tree.processes
                   if process.rss_kb is not None]
        states = {}
        for process in tree.processes:
            states[process.state] = states.get(process.state, 0) + 1

        io_reads = [process.read_bytes for process in tree.processes
                    if process.read_bytes is not None]
        io_writes = [process.write_bytes for process in tree.processes
                     if process.write_bytes is not None]

        return {
            "peak_process_rss_kb": None if peak_process is None else peak_process.peak_rss_kb,
            "peak_process_rss_pid": None if peak_process is None else peak_process.pid,
            "peak_process_rss_tag": None if peak_process is None else peak_process.tag,
            "tree_rss_kb": sum(current) if current else None,
            "process_states": states,
            "tree_read_bytes": sum(io_reads) if io_reads else None,
            "tree_write_bytes": sum(io_writes) if io_writes else None,
        }
