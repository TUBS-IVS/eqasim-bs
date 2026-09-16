"""Machine-resource detection and budget derivation for the pipeline.

The run server is a KVM VM whose CPU/RAM allocation changes depending on who
uses it, so every resource-sensitive configuration value pinned for one
allocation silently drifts when the VM is resized (measured 2026-09-16: the box
reports 94 GB while ``configs/base_bs.yml`` still asked the JVM for 100 GB).
This module is the single place that answers "how big is this machine, and what
may a run use of it".

Two rules keep the mechanism scientifically safe:

1. The RESOLVED value never goes back into the config file. synpp recomputes a
   stage hash from the stage's config dependencies (see
   ``braunschweig/cache_share.py``), so writing a machine-dependent number into
   the config would give every machine a different hash and destroy the shared
   cache. Resolution happens in code, at the point of use.
2. A pinned value is a CEILING. It is used verbatim when it fits the budget and
   clamped down when it does not, which is why no config value -- and therefore
   no stage hash -- has to change for a run to survive a smaller machine.

Only OPERATIONAL keys may be clamped (worker counts, JVM heap): a key that
changes results must never be adjusted behind the researcher's back. See
``docs/superpowers/specs/2026-09-16-resource-adaptive-config-design.md`` for the
per-key classification and its evidence.
"""
from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

BYTES_PER_GIGABYTE = 1024 ** 3

#: Multipliers for the size suffixes used by ``java_memory`` ("100G") and by
#: human-written budgets ("512M"), expressed in gigabytes.
_MEMORY_SUFFIX_GB = {"K": 1 / 1024 ** 2, "M": 1 / 1024, "G": 1.0, "T": 1024.0}

_MEMORY_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT])?B?\s*$", re.IGNORECASE)


class ResourceDetectionError(RuntimeError):
    """Raised when the machine's cores or memory cannot be determined.

    Deliberately fatal: a guessed value would size every worker pool and JVM heap
    of the run wrongly while looking perfectly healthy in the log.
    """


def parse_memory_gb(value) -> float:
    """Parse a memory size into gigabytes.

    Accepts the ``java_memory`` spelling ("100G", "512M"), a bare number of
    gigabytes ("94", 94, 94.5) and lowercase suffixes. Raises ``ValueError`` for
    anything else rather than guessing.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    match = _MEMORY_PATTERN.match(str(value))
    if match is None:
        raise ValueError(
            f"Cannot parse memory size {value!r}. Expected a number of gigabytes "
            f"(94) or a suffixed size (100G, 512M)."
        )
    amount, suffix = match.group(1), (match.group(2) or "G").upper()
    return float(amount) * _MEMORY_SUFFIX_GB[suffix]


def format_memory_gb(memory_gb: float) -> str:
    """Format gigabytes as a JVM ``-Xmx`` argument, rounding DOWN.

    Rounding down matters: rounding up would hand the JVM a heap larger than the
    budget the figure was derived from.
    """
    return f"{max(1, int(math.floor(memory_gb)))}G"


@dataclass(frozen=True)
class MachineResources:
    """What the machine reports about itself, plus how it was learned.

    ``cores_source`` / ``memory_source`` make the detection fallback observable
    (CLAUDE.md): a run that silently fell back to a coarser reader is visible in
    the log and in the run provenance instead of looking identical to a clean
    detection.
    """

    cores: int
    memory_gb: float
    cores_source: str
    memory_source: str


def _affinity_cores() -> Optional[int]:
    """Cores this process may actually run on, or None where unsupported.

    Preferred over ``os.cpu_count()`` because it honours ``taskset`` and a cpuset
    restriction; ``sched_getaffinity`` does not exist on Windows or macOS.
    """
    getter = getattr(os, "sched_getaffinity", None)
    if getter is None:
        return None
    return len(getter(0))


def _meminfo_total_gb() -> Optional[float]:
    """Total RAM from ``/proc/meminfo``, or None when the file is unavailable."""
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 / 1024
    except OSError:
        return None
    return None


def _psutil_total_gb() -> Optional[float]:
    """Total RAM via psutil, or None when psutil is not importable."""
    try:
        import psutil
    except ImportError:
        return None
    return psutil.virtual_memory().total / BYTES_PER_GIGABYTE


def detect_cores(affinity_reader: Optional[Callable[[], Optional[int]]] = _affinity_cores,
                 cpu_count_reader: Callable[[], Optional[int]] = os.cpu_count) -> Tuple[int, str]:
    """Detect usable cores; returns ``(cores, source)``.

    Readers are injected so tests never depend on the machine they run on.
    """
    if affinity_reader is not None:
        cores = affinity_reader()
        if cores:
            return int(cores), "sched_getaffinity"
    cores = cpu_count_reader() if cpu_count_reader is not None else None
    if cores:
        logger.info("[resources] sched_getaffinity unavailable; using os.cpu_count().")
        return int(cores), "cpu_count"
    raise ResourceDetectionError(
        "Cannot determine the number of CPU cores: neither os.sched_getaffinity "
        "nor os.cpu_count reported a value. Set EQASIM_CPU_BUDGET to the number "
        "of cores this run may use."
    )


def detect_memory_gb(psutil_reader: Optional[Callable[[], Optional[float]]] = _psutil_total_gb,
                     meminfo_reader: Optional[Callable[[], Optional[float]]] = _meminfo_total_gb,
                     ) -> Tuple[float, str]:
    """Detect total RAM in gigabytes; returns ``(memory_gb, source)``."""
    if psutil_reader is not None:
        memory_gb = psutil_reader()
        if memory_gb:
            return float(memory_gb), "psutil"
    if meminfo_reader is not None:
        memory_gb = meminfo_reader()
        if memory_gb:
            logger.info("[resources] psutil unavailable; using /proc/meminfo.")
            return float(memory_gb), "proc_meminfo"
    raise ResourceDetectionError(
        "Cannot determine the machine's total memory: psutil is not installed and "
        "/proc/meminfo is unavailable. Install psutil (it is pinned in "
        "environment.yml) or set EQASIM_MEM_BUDGET (e.g. '60G')."
    )


def detect_machine(affinity_reader: Optional[Callable[[], Optional[int]]] = _affinity_cores,
                   cpu_count_reader: Callable[[], Optional[int]] = os.cpu_count,
                   psutil_reader: Optional[Callable[[], Optional[float]]] = _psutil_total_gb,
                   meminfo_reader: Optional[Callable[[], Optional[float]]] = _meminfo_total_gb,
                   ) -> MachineResources:
    """Detect cores and memory together, recording which reader supplied each.

    All reader arguments are explicit; misspelled argument names raise TypeError
    rather than silently falling through to real machine readers (CLAUDE.md:
    no silent fallbacks).
    """
    cores, cores_source = detect_cores(
        affinity_reader=affinity_reader, cpu_count_reader=cpu_count_reader
    )
    memory_gb, memory_source = detect_memory_gb(
        psutil_reader=psutil_reader, meminfo_reader=meminfo_reader
    )
    return MachineResources(
        cores=cores, memory_gb=memory_gb,
        cores_source=cores_source, memory_source=memory_source,
    )
