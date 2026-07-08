"""Host vitals (CPU / RAM / disk) -- honest: unavailable fields are None.

Linux hosts (both target kinds): /proc/meminfo, /proc/loadavg (load1/ncpu as
a CPU-utilisation proxy), df. Windows local hosts: only disk via
shutil.disk_usage; CPU/RAM are None with source='windows_partial' -- shown as
'unknown' in the UI rather than invented (no psutil dependency)."""
from __future__ import annotations

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


def collect_local_windows(repo_path: str) -> Vitals:
    du = shutil.disk_usage(repo_path)
    return Vitals(None, None, round(du.free / 1e9, 1), "windows_partial")
