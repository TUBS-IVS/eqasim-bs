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

#: The suffix is captured whole (an optional size letter, an optional trailing
#: "B") so a LONE "B" with no size letter can be told apart from a real unit
#: ("G", "GB", "M", "MB", ...) and rejected instead of silently defaulting to
#: gigabytes -- see the lone-"B" check in :func:`parse_memory_gb`.
_MEMORY_PATTERN = re.compile(r"^\s*([0-9]+(?:\.[0-9]+)?)\s*([KMGT]?B?)\s*$", re.IGNORECASE)


class ResourceDetectionError(RuntimeError):
    """Raised when the machine's cores or memory cannot be determined.

    Deliberately fatal: a guessed value would size every worker pool and JVM heap
    of the run wrongly while looking perfectly healthy in the log.
    """


def parse_memory_gb(value) -> float:
    """Parse a memory size into gigabytes.

    Accepts the ``java_memory`` spelling ("100G", "512M"), a bare number of
    gigabytes ("94", 94, 94.5) and lowercase suffixes. Raises ``ValueError`` for
    anything else rather than guessing -- including a LONE "B" suffix with no
    size letter ("5B"): bytes are not a supported unit here, and silently
    treating "5B" as 5 gigabytes (the previous behaviour, since a lone "B" fell
    through to the "no suffix -> gigabytes" default) would be exactly the kind
    of silent misinterpretation CLAUDE.md forbids.
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    match = _MEMORY_PATTERN.match(str(value))
    if match is None:
        raise ValueError(
            f"Cannot parse memory size {value!r}. Expected a number of gigabytes "
            f"(94) or a suffixed size (100G, 512M)."
        )
    amount, suffix_text = match.group(1), match.group(2).upper()
    if suffix_text == "B":
        raise ValueError(
            f"Cannot parse memory size {value!r}: a lone 'B' suffix with no size "
            f"letter (K/M/G/T) is not a supported unit here. Expected a number of "
            f"gigabytes ({amount}, without the 'B') or a suffixed size "
            f"(100G, 512M, 100GB)."
        )
    suffix = (suffix_text[:-1] if suffix_text.endswith("B") else suffix_text) or "G"
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
    """Total RAM from ``/proc/meminfo``, or None when the file is unavailable.

    This is the LAST-RESORT reader, used only when ``psutil`` is not installed --
    on exactly the platform that matters (a Linux server without psutil). A
    malformed ``MemTotal:`` line (unexpected token count, a non-numeric field)
    must fall through to ``None`` -- and from there to
    :class:`ResourceDetectionError` -- rather than escape as an uncaught
    ``ValueError``/``IndexError``, which would break the fail-loud contract this
    module promises (CLAUDE.md: no silent fallbacks, but also no unexplained
    crash in its place).
    """
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) / 1024 / 1024
    except (OSError, ValueError, IndexError):
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
#: budget. Historically this NUMBER matched ``parallelism.DEFAULT_CORE_RESERVE``
#: while the two mechanisms still disagreed in practice: the chainsolver pool
#: used to resolve its worker count through ``parallelism.resolve_workers`` ->
#: ``available_cores()`` -> ``os.cpu_count()``, which ignored CPU affinity
#: (``taskset`` / a cpuset restriction) and ``EQASIM_CPU_BUDGET`` entirely,
#: while this module's ``detect_cores()`` deliberately prefers
#: ``sched_getaffinity`` and honours ``EQASIM_CPU_BUDGET``. ADR-0126's
#: memory-bound amendment closed that gap: the chainsolver pool now resolves
#: through THIS module (``resolve_chainsolver_workers`` /
#: ``effective_chainsolver_workers``, called from
#: ``braunschweig/synthesis/locations/secondary_chainsolvers/__init__.py``),
#: exactly like every other key, so it honours ``sched_getaffinity`` and
#: ``EQASIM_CPU_BUDGET`` like the rest of the mechanism.
#: ``parallelism.resolve_workers`` has been removed (no production caller
#: survived the switch); this constant is kept for the OS/driver reserve this
#: module itself applies, not to stay numerically aligned with a second
#: mechanism that no longer exists.
DEFAULT_CORE_RESERVE = 2

#: Memory left free for the OS, the page cache and the synpp driver, in gigabytes.
#: ASSUMPTION: this figure has no measured source (unlike
#: ``DEFAULT_POPSIM_WORKER_MEMORY_GB`` below, which is traceable to the
#: 2026-07-10 OOM post-mortem). It is also the SENSITIVE parameter that decides
#: whether a pinned ``num_workers: 3`` clamps to 2 on the measured 94 GB server:
#: at this 8 GB reserve, 3 x 30 GB = 90 GB > 86 GB budget clamps; at roughly a
#: 4 GB reserve -- what ``configs/overlays/test_100pct.yml``'s own "~90 GB on
#: the 128 GB box" budget implies for 3 workers on this machine -- it would not.
#: Not yet measured (see ADR-0126, Consequences); do not treat it as settled
#: infrastructure. A server measurement of the actual OS/driver/page-cache
#: footprint next to a real run is the missing evidence that would let this be
#: tightened or confirmed.
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

    ``origin`` is one of:

    - ``"pinned"`` -- the configured value fitted and was used as-is.
    - ``"clamped"`` -- it did not fit an OPERATIONAL key's budget and was
      reduced (``java_memory``, ``braunschweig.population.popsim.num_workers``).
    - ``"derived"`` -- the key was left at an auto sentinel and the budget
      supplied the value outright.
    - ``"reported"`` -- a REPORTING-ONLY key (currently only ``processes``, see
      :func:`resolve_processes`) whose configured value exceeds the budget but
      is never adjusted, because the key is result-affecting: ``effective``
      still equals ``configured`` verbatim, and ``note`` states what the
      budget would have allowed, so the mismatch is visible without a false
      claim that anything was clamped.

    ``note`` is the human-readable explanation that goes into the log and the
    run provenance.
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
    further reserve is subtracted from it. The environment is read BEFORE any
    detection runs, and detection is invoked only for the quantity an override
    does not already fix: an operator who has pinned BOTH budgets explicitly
    must be able to start a run even where ``sched_getaffinity`` /
    ``psutil``/``/proc/meminfo`` would raise (the two error messages in
    :func:`detect_cores` / :func:`detect_memory_gb` advertise exactly these
    two overrides as the remedy, so that remedy must actually be reachable).
    """
    env = os.environ if env is None else env
    cpu_override = env.get(ENV_CPU_BUDGET)
    memory_override = env.get(ENV_MEM_BUDGET)

    if machine is None:
        # Detect only the quantities not already fixed by an explicit override.
        # cores_source / memory_source record "env_override" rather than
        # claiming a detection that never ran, so the startup log and the run
        # provenance stay honest about what actually happened (CLAUDE.md: no
        # silent fallbacks).
        if cpu_override:
            machine_cores, machine_cores_source = int(cpu_override), "env_override"
        else:
            machine_cores, machine_cores_source = detect_cores()
        if memory_override:
            machine_memory_gb, machine_memory_source = parse_memory_gb(memory_override), "env_override"
        else:
            machine_memory_gb, machine_memory_source = detect_memory_gb()
        machine = MachineResources(
            cores=machine_cores, memory_gb=machine_memory_gb,
            cores_source=machine_cores_source, memory_source=machine_memory_source,
        )

    if cpu_override:
        cores = max(1, int(cpu_override))
    else:
        cores = max(1, machine.cores - max(0, int(core_reserve)))

    if memory_override:
        memory_gb = max(1.0, parse_memory_gb(memory_override))
    else:
        memory_gb = max(1.0, machine.memory_gb - max(0.0, float(memory_reserve_gb)))

    return ResourceBudget(cores=cores, memory_gb=memory_gb, machine=machine)


def _resolve_ceiling(key: str, configured, ceiling: int, *, unit: str) -> Resolution:
    """Shared pin/clamp/derive logic for one COUNT key against one ceiling.

    Matches the strictness ``_resolve_shard_attempts``
    (``braunschweig/synthesis/locations/secondary_chainsolvers/__init__.py``)
    documents as the standard: ``int()`` truncates toward zero, so a
    non-integral value would otherwise be silently accepted with a different
    effective count than the config states -- ``-0.5`` would become ``0``
    ("pinned", effective 0) and ``0.4`` would also become ``0``, and ``0``
    then reaches ``np.array_split(df, 0)`` as a bare ``ValueError`` deep
    inside a stage, with no hint that the config value was the real cause.
    Both non-integral and negative values are rejected explicitly instead. An
    integral float (a YAML ``3.0``) is a legitimate spelling of ``3`` and is
    accepted.
    """
    if is_auto(configured):
        return Resolution(
            key=key, configured=configured, effective=ceiling, origin="derived",
            note=f"derived from the resource budget ({ceiling} {unit})",
        )
    try:
        requested = int(configured)
        is_integral = float(configured).is_integer()
    except (TypeError, ValueError):
        requested, is_integral = None, False
    if requested is None or not is_integral or requested < 0:
        raise ValueError(
            f"{key} must be 0 (auto) or a positive integer count, got {configured!r}.")
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
    if is_auto(configured):
        budget_text = format_memory_gb(budget.memory_gb)
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
        budget_text = format_memory_gb(budget.memory_gb)
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

    Raises ``ValueError`` for a non-positive ``worker_memory_gb``: dividing the
    memory budget by zero or a negative number is meaningless. Validated ONCE
    here (this function used to floor it to ``0.1`` instead, while
    :func:`build_report` used the raw, unguarded value in its own violation
    check -- the two call sites could therefore disagree about what a
    non-positive figure means); :func:`build_report` relies on this raising
    rather than re-checking.
    """
    if worker_memory_gb <= 0:
        raise ValueError(
            f"worker_memory_gb must be a positive number of gigabytes, got "
            f"{worker_memory_gb!r}.")
    memory_bound = max(1, int(math.floor(budget.memory_gb / worker_memory_gb)))
    ceiling = max(1, min(memory_bound, budget.cores))
    return _resolve_ceiling(
        "braunschweig.population.popsim.num_workers", configured, ceiling,
        unit=f"workers at {worker_memory_gb:g} GB each",
    )


def resolve_processes(configured, budget: ResourceBudget) -> Resolution:
    """Resolve ``processes`` against the core budget for REPORTING only.

    ``processes`` is result-affecting at its two pure read sites
    (``synthesis/population/matched.py``,
    ``synthesis/population/spatial/secondary/locations.py``): it decides both the
    person-chunk partition (``np.array_split(..., processes)``) and how many
    random seeds are drawn (``random.randint(10000, size=processes)``), so it must
    never be silently clamped (see ADR-0126, Decision 3, and the corrected worked
    example in ``docs/codebase/notes/resource-budget.md``). This resolver exists
    only so ``build_report`` can WARN when a pinned value under-uses the machine
    and REPORT when a pin exceeds the budget; its result is never applied to the
    config or to a consumer.

    Unlike :func:`resolve_java_memory` / :func:`resolve_popsim_workers`
    (built on the shared ``_resolve_ceiling`` clamp-or-pass-through helper),
    ``effective`` here is ALWAYS the configured value verbatim -- nothing is
    ever reduced. A pin above the budget returns ``origin="reported"`` (never
    ``"clamped"``, which would claim a reduction that does not happen) with a
    note stating what the budget would have allowed, so the mismatch is still
    visible to the operator without asserting a false clamp (final review:
    the report previously said ``processes = 14 [clamped]`` while the run
    actually used the full pinned 32).
    """
    if is_auto(configured):
        return Resolution(
            key="processes", configured=configured, effective=budget.cores,
            origin="derived",
            note=f"derived from the resource budget ({budget.cores} cores)",
        )
    try:
        requested = int(configured)
        is_integral = float(configured).is_integer()
    except (TypeError, ValueError):
        requested, is_integral = None, False
    if requested is None or not is_integral or requested < 0:
        raise ValueError(
            f"processes must be 0 (auto) or a positive integer count, got {configured!r}.")
    if requested > budget.cores:
        return Resolution(
            key="processes", configured=requested, effective=requested,
            origin="reported",
            note=(f"configured {requested} exceeds the resource budget "
                  f"({budget.cores} cores); processes is result-affecting and is "
                  f"never adjusted, so the run will use {requested} verbatim -- "
                  f"only {budget.cores} cores are budgeted on this machine"),
        )
    return Resolution(
        key="processes", configured=requested, effective=requested,
        origin="pinned", note="configured value fits the resource budget (cores)",
    )


#: Default measured peak memory of one PopulationSim batch worker, in gigabytes.
#: Traceable to the 2026-07-10 OOM post-mortem recorded in
#: configs/overlays/test_100pct.yml ("the measured 25-30 GB per-worker peak");
#: the upper bound is taken because under-estimating it is what OOMs the box.
DEFAULT_POPSIM_WORKER_MEMORY_GB = 30.0

KEY_WORKER_MEMORY_GB = "braunschweig.population.popsim.worker_memory_gb"

#: Below this fraction of the core budget, a pinned ``processes`` value is
#: reported as wasting the machine. Informational only -- the value is never
#: raised automatically, because raising it needs the auto sentinel to be
#: understood at every fallback read site.
PROCESSES_UNDERUSE_FRACTION = 0.75


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

    def _header_lines(self) -> list:
        """Machine + budget + per-key resolution lines, WITHOUT violations.

        Split out from :meth:`format_log` so ``enforce_report`` can log this
        part once at INFO and log each violation once at its own severity,
        instead of every violation appearing twice (once embedded in the
        INFO-level ``format_log()`` dump, once again through the explicit
        per-severity loop).
        """
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
        return lines

    def format_log(self) -> str:
        """Full human-readable report: machine, budget, resolutions, violations.

        NOT on the production run-start path: ``enforce_report`` logs the
        header lines and each violation separately (see its docstring) so
        every violation is logged exactly once, at its own severity, rather
        than calling this method. As of this writing nothing in
        ``scripts/run_synpp.py`` or ``enforce_report`` calls ``format_log``;
        it is exercised directly only by
        ``tests/test_resources_report.py::test_format_log_names_machine_sources_and_every_origin``
        and referenced in ``enforce_report``'s docstring purely as a
        contrast. Kept as a single-string rendering for that test and for ad
        hoc/interactive inspection of a report.
        """
        lines = self._header_lines()
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


#: Config key selecting the population-generation workflow (see
#: ``braunschweig.population.methods``). Only the two PopulationSim methods
#: (``popsim_mid``, ``popsim_open``) ever start a PopulationSim worker; a run
#: that does not select one of them (e.g. ``simple_ipf_open``, or a
#: MATSim-only run that never declares this key) cannot hit a PopulationSim
#: memory mismatch and must not be aborted by one.
KEY_POPULATION_METHOD = "braunschweig.population.method"


def build_report(config: dict, machine: Optional[MachineResources] = None,
                 env: Optional[dict] = None) -> ResourceReport:
    """Resolve every resource key this module clamps or reports against the machine.

    Uses the same ``resolve_*`` functions the stages call at their point of use,
    so the startup report cannot drift from what the run actually does. Covers
    the two OPERATIONAL clamped keys (``java_memory``,
    ``braunschweig.population.popsim.num_workers``), the ``processes`` and
    ``matsim_threads`` / ``matsim_qsim_threads`` warn-only keys, and a
    REPORTING-ONLY preview of ``braunschweig.chainsolvers.processes``
    (``origin="reported"``): the chainsolver pool -- the largest process
    fan-out in the pipeline -- IS memory-bounded since ADR-0126's amendment
    (:func:`resolve_chainsolver_workers` / :func:`effective_chainsolver_workers`;
    measured basis: ``docs/runs/chainsolver-worker-private-memory-2026-09-17.yml``),
    but the full bound needs the LIVE driver RSS, which is only known once the
    stage actually starts (``_solve_problem_set`` calls
    ``effective_chainsolver_workers`` there). This startup report can therefore
    only state what the CORE budget alone would allow; the stage's own log
    line -- not this report -- is the source of truth for the value the run
    actually used.
    """
    budget = resolve_budget(machine=machine, env=env)
    worker_memory_gb = float(config.get(KEY_WORKER_MEMORY_GB, DEFAULT_POPSIM_WORKER_MEMORY_GB))
    chainsolver_worker_memory_gb = float(
        config.get(KEY_CHAINSOLVER_WORKER_MEMORY_GB, DEFAULT_CHAINSOLVER_WORKER_MEMORY_GB))

    chainsolver_configured = config.get("braunschweig.chainsolvers.processes")
    if chainsolver_configured is None:
        chainsolver_configured = config.get("processes", "auto")

    resolutions = [
        resolve_java_memory(config.get("java_memory", "auto"), budget),
        resolve_processes(config.get("processes", "auto"), budget),
        resolve_popsim_workers(
            config.get("braunschweig.population.popsim.num_workers", "auto"),
            budget, worker_memory_gb,
        ),
        Resolution(
            key="braunschweig.chainsolvers.processes",
            configured=chainsolver_configured,
            effective=budget.cores,
            origin="reported",
            note=(
                f"core budget alone allows up to {budget.cores} workers "
                f"({chainsolver_worker_memory_gb:g} GB/worker assumed); the memory bound "
                f"({KEY_CHAINSOLVER_WORKER_MEMORY_GB}, live driver RSS) is applied at "
                "stage start and may reduce this further -- see the stage's own log "
                "line for the value actually used, not this startup preview."
            ),
        ),
    ]

    violations = []
    workers = next(r for r in resolutions
                   if r.key == "braunschweig.population.popsim.num_workers")
    if workers.effective * worker_memory_gb > budget.memory_gb:
        # A PopulationSim memory mismatch can only ever fire on a run that actually
        # selects a PopulationSim population method (popsim_mid / popsim_open) --
        # see braunschweig.population.methods.requires_populationsim. A MATSim-only
        # run or a simple_ipf_open run never starts a PopulationSim worker, so
        # aborting it over this key would be a false-positive gate (issue found in
        # final review: a MATSim-only overlay on a <~38 GB developer machine was
        # aborted before any stage ran, even though it never touches PopulationSim).
        from braunschweig.population.methods import requires_populationsim
        population_method = config.get(KEY_POPULATION_METHOD)
        severity = "error" if requires_populationsim(population_method) else "warning"
        violations.append(Violation(
            key="braunschweig.population.popsim.num_workers", severity=severity,
            message=(
                f"Even a single PopulationSim worker needs {worker_memory_gb:g} GB but "
                f"only {budget.memory_gb:.1f} GB is budgeted on a "
                f"{budget.machine.memory_gb:.1f} GB machine. Reduce "
                f"{KEY_WORKER_MEMORY_GB}, run on a larger machine, or raise "
                f"{ENV_MEM_BUDGET}."
                + ("" if severity == "error" else
                   f" Not aborting: {KEY_POPULATION_METHOD} = {population_method!r} "
                   "does not select a PopulationSim workflow, so this run never "
                   "starts a PopulationSim worker.")
            ),
        ))

    processes = next(r for r in resolutions if r.key == "processes")
    if (not is_auto(config.get("processes"))
            and int(processes.effective) < PROCESSES_UNDERUSE_FRACTION * budget.cores):
        violations.append(Violation(
            key="processes", severity="warning",
            message=(
                f"processes is pinned to {processes.effective} but {budget.cores} "
                f"cores are budgeted on this machine; the run will leave capacity "
                f"unused. Raise the pin in the config if that is not intended."
            ),
        ))

    # matsim_threads / matsim_qsim_threads are NEVER clamped: their effect on
    # results is unverified (issue #410) and silently changing them could change
    # science. Oversubscription is slow, not wrong, so this only warns. This is
    # also the one place whose whole purpose is to never touch these two keys,
    # so a non-numeric configured value (e.g. "auto", which some overlays use
    # for other keys) must degrade to a warning rather than raise out of
    # build_report and abort the run over a key this module does not resolve.
    for key in ("matsim_threads", "matsim_qsim_threads"):
        configured = config.get(key)
        if not configured:
            continue
        try:
            configured_threads = int(configured)
        except (TypeError, ValueError):
            violations.append(Violation(
                key=key, severity="warning",
                message=(
                    f"{key} is {configured!r}, which is not an integer thread "
                    f"count; skipping the oversubscription check for it. This "
                    f"value is left untouched on purpose (issue #410)."
                ),
            ))
            continue
        if configured_threads > budget.cores:
            violations.append(Violation(
                key=key, severity="warning",
                message=(
                    f"{key} is {configured_threads} but only {budget.cores} cores "
                    f"are budgeted; the run will oversubscribe the machine. This "
                    f"value is left untouched on purpose (issue #410)."
                ),
            ))

    return ResourceReport(
        machine=budget.machine, budget=budget,
        resolutions=tuple(resolutions), violations=tuple(violations),
    )


def _log_resolution_deviation(resolution: Resolution) -> None:
    """Log a WARNING once when a resource key's effective value differs from
    what was configured (a clamp, or a value derived from the ``auto`` sentinel).

    A pin that fits stays silent. Shared by every ``effective_*`` wrapper below
    so the log format cannot drift between them and so each wrapper does not
    have to hardcode its own key name -- it always logs ``resolution.key``,
    which the matching ``resolve_*`` function already set.
    """
    if resolution.origin != "pinned":
        logger.warning("[resources] %s %s -> %s (%s)", resolution.key,
                       resolution.configured, resolution.effective, resolution.note)


def effective_java_memory(configured, machine: Optional[MachineResources] = None,
                          env: Optional[dict] = None) -> str:
    """Effective ``-Xmx`` value for a configured ``java_memory``, logged.

    Called at the point of use rather than resolved into the config, because
    ``java_memory`` is part of the ``matsim.runtime.java`` stage hash and is
    inherited by every Java-dependent stage: changing the configured value would
    invalidate the whole Java chain (network, routing, freight, MATSim), while
    clamping here changes nothing any stage hashes.
    """
    resolution = resolve_java_memory(configured, resolve_budget(machine=machine, env=env))
    _log_resolution_deviation(resolution)
    return resolution.effective


def effective_popsim_workers(configured, worker_memory_gb: float,
                             machine: Optional[MachineResources] = None,
                             env: Optional[dict] = None) -> int:
    """Effective PopulationSim batch worker count for this machine, logged.

    Called at the point of use rather than resolved into the config, for the
    same reason as ``effective_java_memory``: the configured value stays part
    of the popsim stage's cache key, so writing a machine-dependent number
    back into it would give every machine a different hash and destroy the
    shared Tier-B PopulationSim cache. Clamping here changes nothing any stage
    hashes.
    """
    resolution = resolve_popsim_workers(
        configured, resolve_budget(machine=machine, env=env), worker_memory_gb)
    _log_resolution_deviation(resolution)
    return int(resolution.effective)


#: Measured MAXIMUM private per-worker memory footprint of the chainsolver
#: process pool, in gigabytes. Traceable to the run manifest
#: ``docs/runs/chainsolver-worker-private-memory-2026-09-17.yml``: seven
#: deduplicated 100% ZGB-8 executions of
#: ``braunschweig.synthesis.locations.secondary_chainsolvers`` on the felix
#: server (2026-09-05 to 2026-09-09), each worker's PRIVATE footprint measured
#: as (available-memory drop during the stage, driver growth being 0.0 in
#: every run) / live worker count, ranging 0.475-0.862 GB/worker across the
#: seven runs. 0.86 is the observed MAXIMUM (the pb4_arm4 run) -- the upper
#: bound is taken because under-estimating it is what OOMs the box (same
#: reasoning as ``DEFAULT_POPSIM_WORKER_MEMORY_GB`` above). Per-worker RSS is
#: NOT usable for this bound: on the 125.78 GB server the summed worker RSS
#: reads 1200-1860 GB because more than 95% of each forked worker's RSS is
#: copy-on-write pages inherited from the driver at fork time, not memory the
#: pool actually needs.
DEFAULT_CHAINSOLVER_WORKER_MEMORY_GB = 0.86

KEY_CHAINSOLVER_WORKER_MEMORY_GB = "braunschweig.chainsolvers.worker_memory_gb"


def _psutil_process_rss_gb() -> Optional[float]:
    """This process's RSS via psutil, or None when psutil is not importable."""
    try:
        import psutil
    except ImportError:
        return None
    return psutil.Process().memory_info().rss / BYTES_PER_GIGABYTE


def _proc_self_status_rss_gb() -> Optional[float]:
    """This process's RSS from ``/proc/self/status``, or None when unavailable.

    LAST-RESORT reader, mirroring :func:`_meminfo_total_gb`: a malformed
    ``VmRSS:`` line must fall through to ``None`` rather than escape as an
    uncaught ``ValueError``/``IndexError``, which would break the fail-loud
    contract this module promises.
    """
    try:
        with open("/proc/self/status", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1]) / 1024 / 1024
    except (OSError, ValueError, IndexError):
        return None
    return None


def current_process_rss_gb(
    psutil_reader: Optional[Callable[[], Optional[float]]] = _psutil_process_rss_gb,
    status_reader: Optional[Callable[[], Optional[float]]] = _proc_self_status_rss_gb,
) -> Optional[float]:
    """This process's current resident set size in gigabytes, or ``None``.

    The chainsolver worker pool is FORKED from this (driver) process, so its
    private memory competes with the pool for the same memory budget --
    :func:`effective_chainsolver_workers` subtracts it before dividing the
    remainder by ``worker_memory_gb``. ``psutil`` is preferred; the last-resort
    ``/proc/self/status`` reader (the only other option on a Linux server
    without ``psutil``) is used only when ``psutil`` is unavailable, and that
    fallback is logged at INFO -- no silent fallback (CLAUDE.md). Readers are
    injected so tests never depend on the process they run in. ``None`` when
    neither source is available; the caller decides how to degrade, and must
    never silently treat that as "0 GB used".
    """
    if psutil_reader is not None:
        rss_gb = psutil_reader()
        if rss_gb:
            return float(rss_gb)
    if status_reader is not None:
        rss_gb = status_reader()
        if rss_gb:
            logger.info(
                "[resources] psutil unavailable; using /proc/self/status for "
                "this process's RSS."
            )
            return float(rss_gb)
    return None


def _chainsolver_worker_ceiling(budget: ResourceBudget, worker_memory_gb: float,
                                driver_rss_gb: float) -> int:
    """Ceiling arithmetic for the chainsolver worker pool (ADR-0126 amendment).

    ``workers = max(1, min(core_budget, floor((memory_budget_gb - driver_rss_gb)
    / worker_memory_gb)))`` -- the driver's own live memory is subtracted first
    because the worker pool is FORKED from it and both compete for the same
    budget (this is the run's own deterministic state, not other users'
    contention, so it is consistent with the "scale off the total allocation,
    not free capacity" decision, ADR-0126). See
    :data:`DEFAULT_CHAINSOLVER_WORKER_MEMORY_GB` for the measured basis of
    ``worker_memory_gb``. Raises ``ValueError`` for a non-positive
    ``worker_memory_gb`` (division by a non-positive number is meaningless) or
    a negative ``driver_rss_gb`` (a fabricated footprint).
    """
    if worker_memory_gb <= 0:
        raise ValueError(
            f"worker_memory_gb must be a positive number of gigabytes, got "
            f"{worker_memory_gb!r}.")
    if driver_rss_gb < 0:
        raise ValueError(f"driver_rss_gb must be >= 0, got {driver_rss_gb!r}.")
    memory_bound = int(math.floor((budget.memory_gb - driver_rss_gb) / worker_memory_gb))
    return max(1, min(budget.cores, memory_bound))


def resolve_chainsolver_workers(configured, budget: ResourceBudget,
                                worker_memory_gb: float, driver_rss_gb: float) -> Resolution:
    """Resolve ``braunschweig.chainsolvers.processes`` against BOTH the core
    budget and the measured memory footprint of the worker pool.

    Operational since ADR-0126's shard/worker split: this key no longer
    influences the secondary-location realisation (``braunschweig.chainsolvers.
    shards`` does that), so clamping it down when it does not fit is safe.
    Raises ``ValueError`` for a non-positive ``worker_memory_gb`` or a negative
    ``driver_rss_gb`` (see :func:`_chainsolver_worker_ceiling`).
    """
    ceiling = _chainsolver_worker_ceiling(budget, worker_memory_gb, driver_rss_gb)
    return _resolve_ceiling(
        "braunschweig.chainsolvers.processes", configured, ceiling,
        unit=(f"workers ({budget.memory_gb:.1f} GB memory budget - "
              f"{driver_rss_gb:.2f} GB driver RSS, {worker_memory_gb:g} GB/worker, "
              f"{budget.cores} cores)"),
    )


def effective_chainsolver_workers(configured, worker_memory_gb: float,
                                  machine: Optional[MachineResources] = None,
                                  env: Optional[dict] = None,
                                  driver_rss_gb: Optional[float] = None) -> int:
    """Effective chainsolver worker-pool size for this machine and this run, logged.

    Called at the point of use (``_solve_problem_set``), not resolved into the
    config: ``braunschweig.chainsolvers.processes`` stays ``volatile=True``
    (ADR-0126), so clamping here changes nothing any stage hashes. Unlike the
    other ``effective_*`` wrappers, the ceiling also depends on THIS process's
    live RSS (``driver_rss_gb``), because the worker pool is forked from it and
    both compete for the same memory budget. When ``driver_rss_gb`` is not
    injected it is measured via :func:`current_process_rss_gb`; if that also
    fails, a WARNING is logged and the memory bound is NOT applied for this run
    (``driver_rss_gb`` treated as 0.0, so only the core budget binds) -- no
    silent fallback (CLAUDE.md). One INFO line is always logged, naming the
    budget, the driver RSS actually used, ``worker_memory_gb`` and the
    resulting ceiling, so the effective parallelism is traceable even when
    nothing was clamped.
    """
    budget = resolve_budget(machine=machine, env=env)
    if driver_rss_gb is None:
        driver_rss_gb = current_process_rss_gb()
        if driver_rss_gb is None:
            logger.warning(
                "[resources] braunschweig.chainsolvers.processes: this process's RSS "
                "could not be measured (psutil not installed and /proc/self/status "
                "unavailable) -- the chainsolver memory bound is NOT applied for this "
                "run; falling back to the core bound only."
            )
            driver_rss_gb = 0.0
    resolution = resolve_chainsolver_workers(configured, budget, worker_memory_gb, driver_rss_gb)
    ceiling = _chainsolver_worker_ceiling(budget, worker_memory_gb, driver_rss_gb)
    logger.info(
        "[resources] braunschweig.chainsolvers.processes: budget %d cores / %.1f GB "
        "memory, driver RSS %.2f GB, worker_memory_gb %.2f GB -> ceiling %d workers",
        budget.cores, budget.memory_gb, driver_rss_gb, worker_memory_gb, ceiling,
    )
    _log_resolution_deviation(resolution)
    return int(resolution.effective)


def enforce_report(report: ResourceReport) -> None:
    """Log the resource report and abort the run on any error-level violation.

    Each violation is logged exactly ONCE, at its own severity: the machine /
    budget / per-key resolution lines are logged at INFO via
    :meth:`ResourceReport._header_lines`, and every violation is logged
    separately below -- never both inside the INFO-level dump (as
    :meth:`ResourceReport.format_log` would render it for a human reader) AND
    again through this loop.

    Failing here costs seconds; the same mismatch discovered by the kernel OOM
    killer costs hours of completed work (2026-08-20 incident, ADR-0097).
    """
    for line in report._header_lines():
        logger.info(line)
    for violation in report.violations:
        if violation.severity == "warning":
            logger.warning("[resources] %s", violation.message)
    errors = [v for v in report.violations if v.severity == "error"]
    if errors:
        raise ResourceValidationError(
            "The configuration does not fit this machine:\n"
            + "\n".join(f"  - {v.key}: {v.message}" for v in errors)
        )
