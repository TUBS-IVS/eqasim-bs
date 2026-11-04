"""Driver-aware JVM heap: the ``-Xmx`` of a Java process leaves room for the synpp driver.

The defect (C2, production run ``prod100_20261031``; measurements in
``docs/runs/prod100-java-heap-driver-rss-2026-11-04.yml``):
``braunschweig.resources.effective_java_memory`` clamps ``java_memory`` against the MACHINE budget (total
memory minus ``DEFAULT_MEMORY_RESERVE_GB``) and has no term for the memory the already running driver
process holds. Synthesis and MATSim shared one driver there, so the driver plus a heap capped at the whole
budget exceeded the machine. The chainsolver pool already subtracts the driver's live RSS
(``resources.effective_chainsolver_workers``); the Java launch did not.

The rule, applied at the moment a Java process is started::

    room_gb = max(1, memory_budget_gb - driver_rss_gb)
    -Xmx    = java_memory                if it fits into room_gb
              floor(room_gb) G           otherwise (clamped), or when java_memory is ``auto``

``java_memory`` itself is never rewritten: it is part of the ``matsim.runtime.java`` stage hash, so the
resolved value exists only as the ``-Xmx`` argument and in the log. The heap size cannot change what a
Java stage computes (a heap that is too small makes the Java process fail with an OutOfMemoryError, it
does not alter its results), which is why this module is exempt from every stage token
(``_SYNPP_TOKEN_EXEMPTION``).

Where the figures come from, and what they do not cover:

- ``driver_rss_gb`` is THIS process's current RSS (``resources.current_process_rss_gb``: psutil, then
  ``/proc/self/status``). Live child processes are not added: at the MATSim launch of the run above the
  driver's only children were two small multiprocessing helper processes (the manifest records them), and
  summing the RSS of forked worker children would count the copy-on-write pages they share with the driver
  once per child.
- When the RSS cannot be measured, the log line says so at WARNING level and the heap is bounded by the
  machine budget alone, exactly as before this module existed. The fallback is never silent.
- Units follow ``braunschweig.resources``: one gigabyte is 1024**3 bytes, the unit of the JVM's ``G``.

This module lives outside ``braunschweig/resources.py`` on purpose: several stage tokens hash that file
(PopulationSim and the chainsolver among them), so a code change there recomputes the 100 % stages. The
reasoning, and what the rule means for operating a run, is in ``docs/codebase/notes/resource-budget.md``.
"""
from __future__ import annotations

import dataclasses
import logging
from typing import Optional

from braunschweig import resources

logger = logging.getLogger(__name__)

#: Read by scripts/audit_synpp_helper_hash.py (ADR-0136): no synpp stage token hashes this module,
#: because nothing here changes a value a stage returns. Remove the marker as soon as a function here
#: starts to shape a stage result; the audit then asks every stage that reaches this module to hash it.
_SYNPP_TOKEN_EXEMPTION = (
    "sizes the JVM heap (-Xmx) of a Java launch only; the heap size never changes what a Java stage "
    "computes, and the resolved value is neither stored in the config nor returned by any stage"
)

#: Smallest heap, in gigabytes, this module will hand out; the same floor ``resources.resolve_budget``
#: and ``resources.format_memory_gb`` apply to the machine budget.
MINIMUM_HEAP_GB = 1.0


def _describe(resolution: resources.Resolution, budget: resources.ResourceBudget,
              driver_rss_gb: float, driver_measured: bool, room_gb: float) -> str:
    """The note of one resolution: every figure the heap was derived from, then what was decided."""
    driver_text = (f"{driver_rss_gb:.2f} GB" if driver_measured else
                   "could not be measured (psutil and /proc/self/status unavailable), counted as "
                   "0.00 GB, so the machine budget alone bounds the heap")
    figures = (f"memory budget {budget.memory_gb:.2f} GB ({budget.machine.memory_source}) - driver RSS "
               f"{driver_text} = {room_gb:.2f} GB for the JVM")
    if room_gb < MINIMUM_HEAP_GB:
        figures += f" (under {MINIMUM_HEAP_GB:g} GB, heap floored at {MINIMUM_HEAP_GB:g} GB)"
    verdicts = {
        "pinned": f"configured {resolution.configured} fits",
        "clamped": f"configured {resolution.configured} exceeds it, clamped to {resolution.effective}",
        "derived": f"java_memory is auto, derived as {resolution.effective}",
    }
    return f"{figures}; {verdicts[resolution.origin]}"


def effective_java_heap(configured, machine: Optional[resources.MachineResources] = None,
                        env: Optional[dict] = None,
                        driver_rss_gb: Optional[float] = None) -> str:
    """Effective ``-Xmx`` value for a configured ``java_memory``, after the driver's memory; logged.

    Called at the point of use (``matsim.runtime.java.run``), never resolved into the config, for the
    reason ``resources.effective_java_memory`` names: ``java_memory`` is part of the stage hash.

    ``machine`` and ``env`` are injected like in the other ``effective_*`` wrappers. ``driver_rss_gb`` is
    injected by tests; left out, it is measured now. Exactly one line is logged per call, at WARNING when
    the value differs from the configuration, the RSS could not be measured or less than the minimum heap
    is left, and at INFO when the configured value fits. Raises ``ValueError`` for a negative
    ``driver_rss_gb`` (a fabricated footprint) and for a ``java_memory`` that
    ``resources.resolve_java_memory`` rejects.
    """
    if driver_rss_gb is not None and driver_rss_gb < 0:
        raise ValueError(f"driver_rss_gb must be >= 0, got {driver_rss_gb!r}.")
    budget = resources.resolve_budget(machine=machine, env=env)
    if driver_rss_gb is None:
        driver_rss_gb = resources.current_process_rss_gb()
    driver_measured = driver_rss_gb is not None
    if not driver_measured:
        driver_rss_gb = 0.0

    room_gb = budget.memory_gb - driver_rss_gb
    # Reuse the one resolver of java_memory (a string pin echoed verbatim, a numeric pin formatted as
    # gigabytes, ``auto`` derived, an unusable value rejected) against the memory that is actually left.
    heap_budget = resources.ResourceBudget(
        cores=budget.cores, memory_gb=max(MINIMUM_HEAP_GB, room_gb), machine=budget.machine)
    resolution = resources.resolve_java_memory(configured, heap_budget)
    resolution = dataclasses.replace(
        resolution, note=_describe(resolution, budget, driver_rss_gb, driver_measured, room_gb))

    degraded = (not driver_measured) or room_gb < MINIMUM_HEAP_GB
    level = logging.WARNING if (resolution.origin != "pinned" or degraded) else logging.INFO
    logger.log(level, "[resources] java_memory %s -> %s (%s)", resolution.configured,
               resolution.effective, resolution.note)
    return resolution.effective
