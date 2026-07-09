"""Host vitals (CPU / RAM / disk) -- honest: unavailable fields are None.

Linux hosts (both target kinds): /proc/meminfo, /proc/loadavg (load1/ncpu as
a CPU-utilisation proxy), df. Windows local hosts: disk via shutil.disk_usage,
RAM via GlobalMemoryStatusEx and CPU via GetSystemTimes -- both plain stdlib
ctypes Win32 calls, so no psutil dependency. CPU utilisation is only defined
as a DELTA between two GetSystemTimes samples: the very first call in a
process returns cpu_percent=None (source 'windows_ctypes:first_sample')
instead of an invented value; every later call reports the busy share of the
interval since the previous call. Fields whose ctypes call fails are None and
named in the source string, mirroring collect_from_proc.
"""
from __future__ import annotations

import ctypes
import os
import shutil
from dataclasses import dataclass


@dataclass
class Vitals:
    cpu_percent: float | None
    ram_avail_gb: float | None
    disk_avail_gb: float | None
    source: str


def collect_from_proc(target, df_output: str) -> Vitals:
    cpu = ram = disk = None
    missing = []
    try:
        mem = target.read_text("/proc/meminfo")
        for line in mem.splitlines():
            if line.startswith("MemAvailable:"):
                ram = round(int(line.split()[1]) / 1024 / 1024, 1)
    except (FileNotFoundError, ValueError, IndexError):
        missing.append("meminfo")
    try:
        load1 = float(target.read_text("/proc/loadavg").split()[0])
        ncpu = target.read_text("/proc/cpuinfo").count("processor")
        if ncpu:
            cpu = round(load1 / ncpu * 100, 1)
    except (FileNotFoundError, ValueError, IndexError, ZeroDivisionError):
        missing.append("loadavg")
    for line in df_output.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[3].endswith("G"):
            disk = float(parts[3][:-1])
    # df yielded no G-suffixed available column -> surface it, never a silent None
    if disk is None:
        missing.append("df")
    source = "proc" if not missing else f"proc_unavailable:{','.join(missing)}"
    return Vitals(cpu, ram, disk, source)


# ---- Windows local host (ctypes, no psutil) -------------------------------

class _MemoryStatusEx(ctypes.Structure):
    # Matches MEMORYSTATUSEX (WinBase.h); dwLength must be set before the call.
    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


# (idle, kernel, user) 100-ns tick counters from the previous GetSystemTimes call;
# CPU% is only defined as a delta between two samples, so the first call yields None.
_last_system_times: tuple[int, int, int] | None = None


def _cpu_percent_from_deltas(idle_delta: int, kernel_delta: int, user_delta: int) -> float:
    """Busy share of an interval from GetSystemTimes deltas, clamped to 0..100.

    In this Win32 API the kernel time INCLUDES idle time, so
    busy = (kernel - idle) + user over a total of kernel + user.
    """
    total = kernel_delta + user_delta
    if total <= 0:
        return 0.0
    busy = (kernel_delta - idle_delta) + user_delta
    return round(min(100.0, max(0.0, busy / total * 100.0)), 1)


def _windows_ram_avail_gb() -> float:
    stat = _MemoryStatusEx()
    stat.dwLength = ctypes.sizeof(stat)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
        raise OSError("GlobalMemoryStatusEx failed")
    return round(stat.ullAvailPhys / 1024 ** 3, 1)


def _windows_system_times() -> tuple[int, int, int]:
    """Cumulative (idle, kernel, user) times in 100-ns FILETIME ticks since boot."""
    from ctypes import wintypes
    idle, kernel, user = wintypes.FILETIME(), wintypes.FILETIME(), wintypes.FILETIME()
    ok = ctypes.windll.kernel32.GetSystemTimes(ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user))
    if not ok:
        raise OSError("GetSystemTimes failed")

    def as_int(ft: "wintypes.FILETIME") -> int:
        return (ft.dwHighDateTime << 32) | ft.dwLowDateTime

    return as_int(idle), as_int(kernel), as_int(user)


def collect_local_windows(repo_path: str) -> Vitals:
    global _last_system_times
    disk = round(shutil.disk_usage(repo_path).free / 1e9, 1)
    if os.name != "nt":
        # Defensive only: app.py routes non-Windows local hosts through /proc, so this
        # branch is unreachable in practice -- but if it were hit, returning the old
        # honest partial shape beats crashing on a missing kernel32.
        return Vitals(None, None, disk, "windows_partial")
    cpu = ram = None
    missing = []
    first_sample = False
    try:
        ram = _windows_ram_avail_gb()
    except OSError:
        missing.append("ram")
    try:
        times = _windows_system_times()
        if _last_system_times is None:
            first_sample = True                     # no interval yet -> no invented CPU value
        else:
            deltas = tuple(now - before for now, before in zip(times, _last_system_times))
            cpu = _cpu_percent_from_deltas(*deltas)
        _last_system_times = times
    except OSError:
        missing.append("cpu")
    if missing:
        source = f"windows_ctypes_unavailable:{','.join(missing)}"
    elif first_sample:
        source = "windows_ctypes:first_sample"
    else:
        source = "windows_ctypes"
    return Vitals(cpu, ram, disk, source)
