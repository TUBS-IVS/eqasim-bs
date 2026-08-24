"""Builder for a fake ``/proc`` tree, so the run resource recorder is testable off Linux.

``braunschweig.monitoring`` reads ``/proc`` through an injectable root directory
(``proc_root``), which lets a test lay out exactly the process tree it wants --
including the races the real thing must survive, such as a pid that disappears
between being listed and being read -- deterministically and on any platform,
Windows development machines included.

Field positions follow proc(5). ``stat`` is written with the full field count and
placeholder values so the OFFSETS are realistic: a parser must locate values by
position AFTER the ``comm`` field, never by counting from the left, because
``comm`` may itself contain spaces and parentheses.
"""
from __future__ import annotations

from pathlib import Path

# proc(5) /proc/<pid>/stat: 1 pid, 2 comm, 3 state, 4 ppid, 14 utime, 15 stime,
# 20 num_threads. Indices below are 0-based into the whole field list.
_STAT_FIELDS = 52
_STAT_PID = 0
_STAT_COMM = 1
_STAT_STATE = 2
_STAT_PPID = 3
_STAT_UTIME = 13
_STAT_STIME = 14
_STAT_THREADS = 19

# Kernel clock ticks per second assumed by the fixtures (Linux default, SC_CLK_TCK).
CLOCK_TICKS_PER_SECOND = 100


def write_process(proc_root, pid, ppid=1, utime_ticks=0, stime_ticks=0, threads=1,
                  rss_kb=None, peak_rss_kb=None, cmdline="python", state="R",
                  read_bytes=None, write_bytes=None, comm=None):
    """Write ``<proc_root>/<pid>/{stat,status,cmdline,io}`` for one fake process.

    ``rss_kb`` / ``peak_rss_kb`` / ``read_bytes`` / ``write_bytes`` left at None omit
    the corresponding line or file entirely, which is how a test reproduces the
    "value not readable" case that must surface as None rather than as zero.
    """
    directory = Path(proc_root) / str(pid)
    directory.mkdir(parents=True, exist_ok=True)

    fields = ["0"] * _STAT_FIELDS
    fields[_STAT_PID] = str(pid)
    # Parenthesised comm containing a space and a nested paren: the realistic worst
    # case for a naive left-to-right field split.
    fields[_STAT_COMM] = "(%s)" % (comm if comm is not None else "py (worker)")
    fields[_STAT_STATE] = state
    fields[_STAT_PPID] = str(ppid)
    fields[_STAT_UTIME] = str(utime_ticks)
    fields[_STAT_STIME] = str(stime_ticks)
    fields[_STAT_THREADS] = str(threads)
    (directory / "stat").write_text(" ".join(fields) + "\n", encoding="utf-8")

    status_lines = ["Name:\t%s" % (cmdline.split()[0] if cmdline else "python"),
                    "State:\t%s (running)" % state]
    if rss_kb is not None:
        status_lines.append("VmRSS:\t%8d kB" % rss_kb)
    if peak_rss_kb is not None:
        status_lines.append("VmHWM:\t%8d kB" % peak_rss_kb)
    status_lines.append("Threads:\t%d" % threads)
    (directory / "status").write_text("\n".join(status_lines) + "\n", encoding="utf-8")

    (directory / "cmdline").write_bytes(
        ("\0".join(cmdline.split()) + "\0").encode("utf-8") if cmdline else b"")

    if read_bytes is not None or write_bytes is not None:
        (directory / "io").write_text(
            "rchar: 0\nwchar: 0\nread_bytes: %d\nwrite_bytes: %d\n"
            % (read_bytes or 0, write_bytes or 0), encoding="utf-8")
    return directory


def write_system(proc_root, mem_total_kb=131072000, mem_available_kb=65536000,
                 mem_free_kb=32768000, swap_total_kb=8192000, swap_free_kb=8192000,
                 load=(1.5, 2.0, 3.0), cpu_count=64,
                 sectors_read=None, sectors_written=None, device="sda"):
    """Write the system-wide files: ``meminfo``, ``loadavg``, ``cpuinfo``, ``diskstats``."""
    root = Path(proc_root)
    root.mkdir(parents=True, exist_ok=True)

    root.joinpath("meminfo").write_text(
        "MemTotal:       %8d kB\n"
        "MemFree:        %8d kB\n"
        "MemAvailable:   %8d kB\n"
        "SwapTotal:      %8d kB\n"
        "SwapFree:       %8d kB\n"
        % (mem_total_kb, mem_free_kb, mem_available_kb, swap_total_kb, swap_free_kb),
        encoding="utf-8")

    root.joinpath("loadavg").write_text(
        "%s %s %s 5/1234 9999\n" % load, encoding="utf-8")

    root.joinpath("cpuinfo").write_text(
        "".join("processor\t: %d\nmodel name\t: fake\n\n" % i for i in range(cpu_count)),
        encoding="utf-8")

    if sectors_read is not None or sectors_written is not None:
        # proc(5) diskstats: 1 major, 2 minor, 3 name, 4 reads, 5 merged,
        # 6 sectors read, 7 ms reading, 8 writes, 9 merged, 10 sectors written.
        root.joinpath("diskstats").write_text(
            "   8       0 %s 100 0 %d 500 200 0 %d 700 0 0 0\n"
            % (device, sectors_read or 0, sectors_written or 0), encoding="utf-8")
    return root
