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
        return value == 0
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


def _resolve_ceiling(key: str, configured, ceiling: int, *, unit: str) -> Resolution:
    """Shared pin/clamp/derive logic for one COUNT key against one ceiling."""
    if is_auto(configured):
        return Resolution(
            key=key, configured=configured, effective=ceiling, origin="derived",
            note=f"derived from the resource budget ({ceiling} {unit})",
        )
    requested = int(configured)
    if requested < 0:
        raise ValueError(
            f"{key} must be 0 (auto) or a positive count, got {configured!r}.")
    if requested > ceiling:
        return Resolution(
            key=key, configured=requested, effective=ceiling, origin="clamped",
            note=(f"configured {requested} exceeds the resource budget; "
                  f"clamped to {ceiling} {unit}"),
        )
    return Resolution(
        key=key, configured=requested, effective=requested, origin="pinned",
        note=f"configured value fits the resource budget ({unit})",
    )


def resolve_java_memory(configured, budget: ResourceBudget) -> Resolution:
    """Resolve the JVM heap size against the memory budget.

    Operational only: the heap size becomes ``-Xmx`` and cannot change results,
    so clamping it down on a smaller machine is safe and needs no config change.
    A pin that fits is echoed VERBATIM -- reformatting it would silently shrink
    a sub-gigabyte-granular value such as "1500M".
    """
    budget_text = format_memory_gb(budget.memory_gb)
    if is_auto(configured):
        return Resolution(
            key="java_memory", configured=configured, effective=budget_text,
            origin="derived",
            note=f"derived from the memory budget ({budget_text})",
        )
    requested_gb = parse_memory_gb(configured)
    if requested_gb <= 0:
        raise ValueError(
            f"java_memory must be a positive size, got {configured!r}.")
    if requested_gb > budget.memory_gb:
        return Resolution(
            key="java_memory", configured=configured, effective=budget_text,
            origin="clamped",
            note=(f"configured {configured} exceeds the memory budget; "
                  f"clamped to {budget_text}"),
        )
    return Resolution(
        key="java_memory", configured=configured, effective=str(configured),
        origin="pinned", note="configured value fits the memory budget",
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


#: Default measured peak memory of one PopulationSim batch worker, in gigabytes.
#: Traceable to the 2026-07-10 OOM post-mortem recorded in
#: configs/overlays/test_100pct.yml ("the measured 25-30 GB per-worker peak");
#: the upper bound is taken because under-estimating it is what OOMs the box.
DEFAULT_POPSIM_WORKER_MEMORY_GB = 30.0

KEY_WORKER_MEMORY_GB = "braunschweig.population.popsim.worker_memory_gb"


class ResourceValidationError(RuntimeError):
    """Raised at startup when the configuration cannot fit the detected machine."""


@dataclass(frozen=True)
class Violation:
    """One configuration/machine mismatch found at startup.

    ``"error"`` aborts the run; ``"warning"`` is logged and carried into the run
    provenance but does not stop anything.
    """

    key: str
    severity: str
    message: str


@dataclass(frozen=True)
class ResourceReport:
    """Everything the run start needs to log, validate and record."""

    machine: MachineResources
    budget: ResourceBudget
    resolutions: tuple
    violations: tuple

    def format_log(self) -> str:
        lines = [
            f"[resources] machine: {self.machine.cores} cores "
            f"({self.machine.cores_source}), {self.machine.memory_gb:.1f} GB "
            f"({self.machine.memory_source})",
            f"[resources] budget for this run: {self.budget.cores} cores, "
            f"{self.budget.memory_gb:.1f} GB",
        ]
        for resolution in self.resolutions:
            lines.append(
                f"[resources]   {resolution.key} = {resolution.effective} "
                f"[{resolution.origin}] ({resolution.note})"
            )
        for violation in self.violations:
            lines.append(f"[resources]   {violation.severity.upper()}: {violation.message}")
        return "\n".join(lines)

    def as_dict(self) -> dict:
        return {
            "machine": {
                "cores": self.machine.cores,
                "memory_gb": round(self.machine.memory_gb, 2),
                "cores_source": self.machine.cores_source,
                "memory_source": self.machine.memory_source,
            },
            "budget": {
                "cores": self.budget.cores,
                "memory_gb": round(self.budget.memory_gb, 2),
            },
            "resolutions": {
                r.key: {"configured": r.configured, "effective": r.effective,
                        "origin": r.origin, "note": r.note}
                for r in self.resolutions
            },
            "violations": [
                {"key": v.key, "severity": v.severity, "message": v.message}
                for v in self.violations
            ],
        }


def build_report(config: dict, machine: Optional[MachineResources] = None,
                 env: Optional[dict] = None) -> ResourceReport:
    """Resolve every resource key of a resolved synpp config against the machine.

    Uses the same ``resolve_*`` functions the stages call at their point of use,
    so the startup report cannot drift from what the run actually does.
    """
    budget = resolve_budget(machine=machine, env=env)
    worker_memory_gb = float(config.get(KEY_WORKER_MEMORY_GB, DEFAULT_POPSIM_WORKER_MEMORY_GB))

    resolutions = [
        resolve_java_memory(config.get("java_memory", "auto"), budget),
        resolve_processes(config.get("processes", "auto"), budget),
        resolve_popsim_workers(
            config.get("braunschweig.population.popsim.num_workers", "auto"),
            budget, worker_memory_gb,
        ),
    ]

    violations = []
    workers = next(r for r in resolutions
                   if r.key == "braunschweig.population.popsim.num_workers")
    if workers.effective * worker_memory_gb > budget.memory_gb:
        violations.append(Violation(
            key="braunschweig.population.popsim.num_workers", severity="error",
            message=(
                f"Even a single PopulationSim worker needs {worker_memory_gb:g} GB but "
                f"only {budget.memory_gb:.1f} GB is budgeted on a "
                f"{budget.machine.memory_gb:.1f} GB machine. Reduce "
                f"{KEY_WORKER_MEMORY_GB}, run on a larger machine, or raise "
                f"{ENV_MEM_BUDGET}."
            ),
        ))

    # matsim_threads / matsim_qsim_threads are NEVER clamped: their effect on
    # results is unverified (issue #410) and silently changing them could change
    # science. Oversubscription is slow, not wrong, so this only warns.
    for key in ("matsim_threads", "matsim_qsim_threads"):
        configured = config.get(key)
        if configured and int(configured) > budget.cores:
            violations.append(Violation(
                key=key, severity="warning",
                message=(
                    f"{key} is {configured} but only {budget.cores} cores are "
                    f"budgeted; the run will oversubscribe the machine. This value "
                    f"is left untouched on purpose (issue #410)."
                ),
            ))

    return ResourceReport(
        machine=budget.machine, budget=budget,
        resolutions=tuple(resolutions), violations=tuple(violations),
    )
