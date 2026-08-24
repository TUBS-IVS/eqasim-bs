"""Whole-process-tree CPU, memory and IO primitives read from ``/proc`` (issue #350).

Why the TREE and not a process
------------------------------
The 100 % run of 2026-08-23/24 had a ``secondary_chainsolvers`` stage killed as
"deadlocked" on the evidence of ``ps -C python --sort=-rss | head -6``, which
showed six processes with frozen CPU times. With ~62 forked workers the six
biggest-RSS processes were finished-by-design idle workers while the computing
ones sat outside that window; the stage had in fact completed. A CPU total summed
over EVERY process in the tree is a number no busy worker can fall outside of.

Why ``VmHWM``
-------------
``VmHWM`` is the kernel's peak resident-set high-water mark for a process. It is
the exact answer to "how much memory does one PopulationSim worker need at its
peak" (issue #281, where the only hard number so far is a 29.9 GB OOM-kill log
line), but it is readable only while the process lives -- hence sampling it.

Threads are counted, never walked
---------------------------------
``num_threads`` comes from the process' own ``stat`` line. Walking
``/proc/<pid>/task/*`` and adding those CPU times to the process' own would
double-count the same work, so the thread total is reported as a separate signal.
Thread oversubscription is a documented failure class here: see
``braunschweig/parallelism.py`` on the ~4000-thread libc segfaults that cost 12
PopulationSim batches.

Fallback transparency (CLAUDE.md)
---------------------------------
``/proc`` is the primary source. Where it does not exist (Windows development
machines) ``psutil`` is used INSTEAD, and every sample names the source it came
from (``proc`` / ``psutil`` / ``unavailable:<reason>``). An unreadable counter is
reported as ``None`` -- "no signal" -- and never as a measured zero, so no caller
can mistake an unmeasurable tree for an idle one.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)

DEFAULT_PROC_ROOT = "/proc"

# Sample provenance, reported per sample. Explicit strings rather than a boolean so
# a recorded series states WHICH mechanism produced its numbers.
SOURCE_PROC = "proc"
SOURCE_PSUTIL = "psutil"
SOURCE_UNAVAILABLE = "unavailable"

# Kernel clock ticks per second, used to convert the ``stat`` CPU counters into
# seconds. ``SC_CLK_TCK`` is 100 on every Linux the pipeline runs on; this constant
# is the value assumed where ``os.sysconf`` is unavailable (Windows).
FALLBACK_CLOCK_TICKS_PER_SECOND = 100

# Longest command line kept as a process tag. Long enough to tell a PopulationSim
# worker from a chainsolver shard, short enough to keep a JSONL row readable.
MAX_TAG_LENGTH = 120

# proc(5) ``/proc/<pid>/stat`` field positions, 0-based AFTER the parenthesised
# ``comm`` field: proc(5) numbers them 3 state, 4 ppid, 14 utime, 15 stime,
# 20 num_threads.
_STAT_STATE = 0
_STAT_PPID = 1
_STAT_UTIME = 11
_STAT_STIME = 12
_STAT_THREADS = 17


@dataclass
class ProcessSample:
    """One process at one instant. ``None`` means "not readable", never zero."""

    pid: int
    ppid: int
    state: str
    tag: str
    cpu_seconds: float | None
    rss_kb: int | None
    peak_rss_kb: int | None
    thread_count: int | None
    read_bytes: int | None = None
    write_bytes: int | None = None

    def to_dict(self) -> dict:
        return {
            "pid": self.pid,
            "ppid": self.ppid,
            "state": self.state,
            "tag": self.tag,
            "cpu_seconds": self.cpu_seconds,
            "rss_kb": self.rss_kb,
            "peak_rss_kb": self.peak_rss_kb,
            "thread_count": self.thread_count,
            "read_bytes": self.read_bytes,
            "write_bytes": self.write_bytes,
        }


@dataclass
class TreeSample:
    """A whole process tree at one instant.

    ``cpu_seconds`` is the summed user+system CPU time of every readable process in
    the tree, counted since each process started; a caller judges progress from the
    DIFFERENCE between two samples. ``None`` means no process could be read at all
    (see the module docstring on fallback transparency).
    """

    root_pid: int
    cpu_seconds: float | None
    process_count: int
    thread_count: int | None
    source: str
    processes: list = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "root_pid": self.root_pid,
            "cpu_seconds": self.cpu_seconds,
            "process_count": self.process_count,
            "thread_count": self.thread_count,
            "source": self.source,
            "processes": [process.to_dict() for process in self.processes],
        }


def default_clock_ticks_per_second() -> int:
    """Kernel clock ticks per second (``SC_CLK_TCK``), or the Linux default of 100."""
    try:
        ticks = int(os.sysconf("SC_CLK_TCK"))
    except (AttributeError, ValueError, OSError):
        return FALLBACK_CLOCK_TICKS_PER_SECOND
    return ticks if ticks > 0 else FALLBACK_CLOCK_TICKS_PER_SECOND


def _resolve_proc_root(proc_root) -> str:
    return DEFAULT_PROC_ROOT if proc_root is None else str(proc_root)


def _read_text(path):
    """File contents, or ``None`` when the path is gone or unreadable.

    Every ``/proc`` read races with process exit, so a missing file is normal and
    must not raise: the caller drops that process from the sample.
    """
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            return handle.read()
    except (OSError, ValueError):
        return None


def _parse_stat(text):
    """``(state, ppid, cpu_ticks, thread_count)`` from a ``/proc/<pid>/stat`` line.

    ``comm`` is parenthesised and may itself contain spaces and parentheses, so the
    fields are located after the LAST closing parenthesis rather than by counting
    from the left.
    """
    close = text.rfind(")")
    if close < 0:
        return None
    fields = text[close + 1:].split()
    try:
        return (
            fields[_STAT_STATE],
            int(fields[_STAT_PPID]),
            int(fields[_STAT_UTIME]) + int(fields[_STAT_STIME]),
            int(fields[_STAT_THREADS]),
        )
    except (IndexError, ValueError):
        return None


def _parse_labelled_value(line):
    """Second whitespace-separated token of ``line`` as an int, or ``None``."""
    parts = line.split()
    try:
        return int(parts[1])
    except (IndexError, ValueError):
        return None


def _parse_status_memory(text):
    """``(rss_kb, peak_rss_kb)`` from ``/proc/<pid>/status``; absent lines give None."""
    rss = peak = None
    for line in text.splitlines():
        if line.startswith("VmRSS:"):
            rss = _parse_labelled_value(line)
        elif line.startswith("VmHWM:"):
            peak = _parse_labelled_value(line)
    return rss, peak


def _parse_io(text):
    """``(read_bytes, write_bytes)`` from ``/proc/<pid>/io``; absent keys give None."""
    read = write = None
    for line in text.splitlines():
        if line.startswith("read_bytes:"):
            read = _parse_labelled_value(line)
        elif line.startswith("write_bytes:"):
            write = _parse_labelled_value(line)
    return read, write


def _read_tag(proc_root, pid, fallback):
    """Short command-line tag for ``pid``, so a row can be attributed to a role."""
    text = _read_text(os.path.join(proc_root, str(pid), "cmdline"))
    if text:
        tag = " ".join(part for part in text.split("\0") if part)
        if tag:
            return tag[:MAX_TAG_LENGTH]
    return fallback


def read_process(pid, proc_root=None, ticks_per_second=None):
    """One :class:`ProcessSample` for ``pid``, or ``None`` if it cannot be read."""
    root = _resolve_proc_root(proc_root)
    ticks = ticks_per_second or default_clock_ticks_per_second()

    stat_text = _read_text(os.path.join(root, str(pid), "stat"))
    if stat_text is None:
        return None
    parsed = _parse_stat(stat_text)
    if parsed is None:
        return None
    state, ppid, cpu_ticks, thread_count = parsed

    status_text = _read_text(os.path.join(root, str(pid), "status"))
    rss_kb, peak_rss_kb = _parse_status_memory(status_text) if status_text else (None, None)

    io_text = _read_text(os.path.join(root, str(pid), "io"))
    read_bytes, write_bytes = _parse_io(io_text) if io_text else (None, None)

    return ProcessSample(
        pid=int(pid), ppid=ppid, state=state,
        tag=_read_tag(root, pid, fallback="pid:%s" % pid),
        cpu_seconds=cpu_ticks / float(ticks),
        rss_kb=rss_kb, peak_rss_kb=peak_rss_kb, thread_count=thread_count,
        read_bytes=read_bytes, write_bytes=write_bytes,
    )


def _parent_map(proc_root):
    """``{pid: ppid}`` for every readable process, from one ``/proc`` listing."""
    parents = {}
    try:
        entries = list(os.scandir(proc_root))
    except OSError:
        return parents
    for entry in entries:
        if not entry.name.isdigit():
            continue
        stat_text = _read_text(os.path.join(proc_root, entry.name, "stat"))
        if stat_text is None:
            continue
        parsed = _parse_stat(stat_text)
        if parsed is None:
            continue
        parents[int(entry.name)] = parsed[1]
    return parents


def iter_tree_pids(root_pid, proc_root=None) -> list:
    """Pids of ``root_pid`` and all its transitive children, breadth-first.

    Processes only -- threads are counted via ``num_threads`` instead (see the
    module docstring), because adding a thread's CPU time to its process' own would
    double-count the same work.
    """
    root = _resolve_proc_root(proc_root)
    parents = _parent_map(root)

    children = {}
    for pid, ppid in parents.items():
        children.setdefault(ppid, []).append(pid)

    ordered = [int(root_pid)]
    queue = [int(root_pid)]
    while queue:
        current = queue.pop(0)
        for child in sorted(children.get(current, [])):
            if child in ordered:
                continue
            ordered.append(child)
            queue.append(child)
    return ordered


def sample_tree(root_pid, proc_root=None, clock_ticks_per_second=None,
                allow_psutil=True) -> TreeSample:
    """Sample the whole process tree rooted at ``root_pid``.

    ``/proc`` is used when it exists, otherwise ``psutil`` (named in ``source``).
    Processes that exit mid-sample are skipped, so a partially readable tree still
    yields the CPU total of its survivors instead of failing.
    """
    root = _resolve_proc_root(proc_root)

    if not os.path.isdir(root):
        if allow_psutil:
            return _sample_with_psutil(root_pid, reason="no_proc")
        return _unavailable(root_pid, "no_proc")

    ticks = clock_ticks_per_second or default_clock_ticks_per_second()
    processes = []
    for pid in iter_tree_pids(root_pid, proc_root=root):
        process = read_process(pid, proc_root=root, ticks_per_second=ticks)
        if process is not None:
            processes.append(process)

    if not processes:
        # The root pid is gone (a finished or killed run): no signal, not zero work.
        return _unavailable(root_pid, "no_readable_process")

    cpu_values = [process.cpu_seconds for process in processes
                  if process.cpu_seconds is not None]
    thread_values = [process.thread_count for process in processes
                     if process.thread_count is not None]
    return TreeSample(
        root_pid=int(root_pid),
        cpu_seconds=sum(cpu_values) if cpu_values else None,
        process_count=len(processes),
        thread_count=sum(thread_values) if thread_values else None,
        source=SOURCE_PROC,
        processes=processes,
    )


def _unavailable(root_pid, reason) -> TreeSample:
    return TreeSample(root_pid=int(root_pid), cpu_seconds=None, process_count=0,
                      thread_count=None,
                      source="%s:%s" % (SOURCE_UNAVAILABLE, reason), processes=[])


def _sample_with_psutil(root_pid, reason) -> TreeSample:
    """Tree sample via ``psutil``, for hosts without ``/proc`` (Windows development).

    ``peak_rss_kb`` is filled only where the platform itself reports a high-water
    mark (``peak_wset`` on Windows); elsewhere it stays ``None`` rather than being
    silently substituted by the current RSS.
    """
    try:
        import psutil
    except ImportError:
        return _unavailable(root_pid, reason + ",no_psutil")

    try:
        root = psutil.Process(int(root_pid))
        members = [root] + root.children(recursive=True)
    except Exception:
        # psutil raises NoSuchProcess / AccessDenied / ZombieProcess here; none of
        # them is a measurement, so all of them mean "unmeasurable".
        return _unavailable(root_pid, reason + ",psutil_unreadable")

    processes = []
    for member in members:
        try:
            with member.oneshot():
                cpu_times = member.cpu_times()
                memory = member.memory_info()
                peak_wset = getattr(memory, "peak_wset", None)
                processes.append(ProcessSample(
                    pid=member.pid, ppid=member.ppid(), state=member.status(),
                    tag=(" ".join(member.cmdline()) or member.name())[:MAX_TAG_LENGTH],
                    cpu_seconds=float(cpu_times.user) + float(cpu_times.system),
                    rss_kb=int(memory.rss / 1024),
                    peak_rss_kb=None if peak_wset is None else int(peak_wset / 1024),
                    thread_count=member.num_threads(),
                ))
        except Exception:
            continue

    if not processes:
        return _unavailable(root_pid, reason + ",psutil_unreadable")

    return TreeSample(
        root_pid=int(root_pid),
        cpu_seconds=sum(process.cpu_seconds for process in processes
                        if process.cpu_seconds is not None),
        process_count=len(processes),
        thread_count=sum(process.thread_count for process in processes
                         if process.thread_count is not None),
        source=SOURCE_PSUTIL,
        processes=processes,
    )


def read_process_cpu_seconds(pid, proc_root=None, clock_ticks_per_second=None):
    """CPU seconds of ``pid`` ALONE (user+system), or ``None`` when unreadable."""
    ticks = clock_ticks_per_second or default_clock_ticks_per_second()
    process = read_process(pid, proc_root=proc_root, ticks_per_second=ticks)
    return None if process is None else process.cpu_seconds


def read_tree_cpu_seconds(pid, proc_root=None, clock_ticks_per_second=None):
    """CPU seconds accumulated by the WHOLE tree below and including ``pid``.

    The single definition of "is this tree doing work", shared by the resource
    recorder and by the Java hang watchdog (``matsim/runtime/process_watchdog.py``)
    so that the two cannot drift apart. Returns ``None`` when no CPU signal is
    available; callers must treat that as "unmeasurable", never as "no progress".
    """
    return sample_tree(pid, proc_root=proc_root,
                       clock_ticks_per_second=clock_ticks_per_second).cpu_seconds
