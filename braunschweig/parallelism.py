"""Core-count aware parallelism helpers.

Centralises how the pipeline's parallel side-processes (PopulationSim batch
runner, secondary-location chain solvers, ...) pick a worker count, so a run
scales with the machine it lands on instead of a hard-coded number tuned for one
box. Worker counts are therefore configured as a SENTINEL:

  - a positive integer N  -> use exactly N workers (explicit, reproducible),
  - 0 / null / "auto"     -> auto-scale to the available cores (this module).

Reproducibility note (CLAUDE.md): a parallel stage seeded per shard is only
byte-reproducible for a FIXED worker count. "auto" makes the count depend on the
machine, so a run that must be byte-identical across machines should pin an
explicit integer; "auto" is for "use this box well". The resolved count is always
logged by the caller so the effective parallelism is traceable.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Cores to leave free for the OS + the main Python driver when auto-scaling.
DEFAULT_CORE_RESERVE = 2


def available_cores(reserve: int = DEFAULT_CORE_RESERVE) -> int:
    """Number of usable cores for worker processes = cpu_count - ``reserve``.

    Never returns less than 1. ``reserve`` keeps a couple of cores free for the OS
    and the orchestrating process so the box stays responsive under load.
    """
    total = os.cpu_count() or 1
    return max(1, total - max(0, int(reserve)))


def resolve_workers(requested, reserve: int = DEFAULT_CORE_RESERVE) -> int:
    """Resolve a configured worker count, honouring the auto sentinel.

    Parameters
    ----------
    requested:
        The configured value. ``None``, ``0``, a negative number, or the string
        ``"auto"`` -> auto-scale to :func:`available_cores`. Any positive integer
        (or its string form) is used verbatim.
    reserve:
        Cores to leave free when auto-scaling.

    Returns
    -------
    int
        The effective worker count (>= 1).
    """
    if requested is None:
        return available_cores(reserve)
    if isinstance(requested, str):
        if requested.strip().lower() in ("auto", ""):
            return available_cores(reserve)
        requested = int(requested)
    requested = int(requested)
    if requested <= 0:
        return available_cores(reserve)
    return requested
