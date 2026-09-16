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


#: Cores left free for the OS and the orchestrating synpp driver when deriving a
#: budget. Matches ``parallelism.DEFAULT_CORE_RESERVE`` so the two mechanisms
#: cannot disagree about how much of the box a run may take.
DEFAULT_CORE_RESERVE = 2

#: Memory left free for the OS, the page cache and the synpp driver, in gigabytes.
DEFAULT_MEMORY_RESERVE_GB = 8.0

ENV_CPU_BUDGET = "EQASIM_CPU_BUDGET"
ENV_MEM_BUDGET = "EQASIM_MEM_BUDGET"

#: Configured values meaning "derive this from the budget". A key left at a
#: sentinel scales with the machine; any real value is treated as a ceiling.
_AUTO_SENTINELS = (None, "", "auto")


@dataclass(frozen=True)
class ResourceBudget:
    """What a single run may use of the machine.

    ``cores`` and ``memory_gb`` are the machine's resources minus the OS reserve,
    or the operator's explicit ``EQASIM_CPU_BUDGET`` / ``EQASIM_MEM_BUDGET``.
    """

    cores: int
    memory_gb: float
    machine: MachineResources


@dataclass(frozen=True)
class Resolution:
    """The effective value of one resource key and why it has that value.

    ``origin`` is ``"pinned"`` (the configured value fitted and was used as-is),
    ``"clamped"`` (it did not fit and was reduced) or ``"derived"`` (the key was
    left at an auto sentinel). ``note`` is the human-readable explanation that
    goes into the log and the run provenance.
    """

    key: str
    configured: object
    effective: object
    origin: str
    note: str


def is_auto(value) -> bool:
    """True when a configured value asks to be derived from the budget.

    ``0`` counts as a sentinel for count-like keys (the spelling already used by
    ``braunschweig.chainsolvers.processes``); a memory string like "100G" never
    does.
    """
    if isinstance(value, str):
        return value.strip().lower() in ("", "auto")
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value <= 0
    return value is None


def resolve_budget(machine: Optional[MachineResources] = None,
                   core_reserve: int = DEFAULT_CORE_RESERVE,
                   memory_reserve_gb: float = DEFAULT_MEMORY_RESERVE_GB,
                   env: Optional[dict] = None) -> ResourceBudget:
    """Derive what this run may use from the machine and the environment.

    An explicit ``EQASIM_CPU_BUDGET`` / ``EQASIM_MEM_BUDGET`` is taken verbatim:
    the operator has already decided how much of a shared box to claim, so no
    further reserve is subtracted from it.
    """
    machine = detect_machine() if machine is None else machine
    env = os.environ if env is None else env

    cpu_override = env.get(ENV_CPU_BUDGET)
    if cpu_override:
        cores = max(1, int(cpu_override))
    else:
        cores = max(1, machine.cores - max(0, int(core_reserve)))

    memory_override = env.get(ENV_MEM_BUDGET)
    if memory_override:
        memory_gb = max(1.0, parse_memory_gb(memory_override))
    else:
        memory_gb = max(1.0, machine.memory_gb - max(0.0, float(memory_reserve_gb)))

    return ResourceBudget(cores=cores, memory_gb=memory_gb, machine=machine)


def _resolve_ceiling(key: str, configured, ceiling, *, unit: str,
                     render=lambda value: value) -> Resolution:
    """Shared pin/clamp/derive logic for one key against one ceiling."""
    if is_auto(configured):
        return Resolution(
            key=key, configured=configured, effective=render(ceiling), origin="derived",
            note=f"derived from the resource budget ({render(ceiling)} {unit})",
        )
    requested = configured
    if requested > ceiling:
        return Resolution(
            key=key, configured=render(configured), effective=render(ceiling),
            origin="clamped",
            note=(f"configured {render(configured)} exceeds the resource budget; "
                  f"clamped to {render(ceiling)} {unit}"),
        )
    return Resolution(
        key=key, configured=render(configured), effective=render(configured),
        origin="pinned", note=f"configured value fits the resource budget ({unit})",
    )


def resolve_java_memory(configured, budget: ResourceBudget) -> Resolution:
    """Resolve the JVM heap size against the memory budget.

    Operational only: the heap size becomes ``-Xmx`` and cannot change results,
    so clamping it down on a smaller machine is safe and needs no config change.
    """
    if is_auto(configured):
        return Resolution(
            key="java_memory", configured=configured,
            effective=format_memory_gb(budget.memory_gb), origin="derived",
            note=f"derived from the memory budget ({format_memory_gb(budget.memory_gb)})",
        )
    return _resolve_ceiling(
        "java_memory", parse_memory_gb(configured), budget.memory_gb,
        unit="memory", render=format_memory_gb,
    )


def resolve_popsim_workers(configured, budget: ResourceBudget,
                           worker_memory_gb: float) -> Resolution:
    """Resolve the PopulationSim batch worker count against the MEMORY budget.

    This key is memory-bound, not core-bound: each worker drives its own
    PopulationSim subprocess whose measured peak is 25-30 GB (2026-07-10 OOM
    post-mortem, recorded in ``configs/overlays/test_100pct.yml``). Deriving it
    from the core count is what would OOM the box.

    Worker count cannot change results: ``popsim.batch.run_batches`` submits one
    independent subprocess per batch folder and no seed depends on the worker
    index, so clamping is safe.
    """
    memory_bound = max(1, int(math.floor(budget.memory_gb / max(0.1, float(worker_memory_gb)))))
    ceiling = max(1, min(memory_bound, budget.cores))
    return _resolve_ceiling(
        "braunschweig.population.popsim.num_workers", configured, ceiling,
        unit=f"workers at {worker_memory_gb:g} GB each",
    )


def resolve_processes(configured, budget: ResourceBudget) -> Resolution:
    """Resolve the generic synpp worker count against the core budget."""
    return _resolve_ceiling("processes", configured, budget.cores, unit="cores")
