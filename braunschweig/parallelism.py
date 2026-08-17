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

# Single-threaded BLAS/OpenMP pins for worker processes. Every parallel
# side-process of the pipeline must run its numerics single-threaded: with N
# workers each opening an ncores-sized BLAS pool the box oversubscribes to
# N x ncores threads (observed on the 64-core server: ~4000 threads, libc
# segfaults, 12 lost PopulationSim batches -- see braunschweig/popsim/batch.py
# and issue #122 for the chainsolvers pool). Shared here so the PopulationSim
# batch runner and the chainsolvers pool cannot drift apart.
SINGLE_THREAD_BLAS_ENV = {
    "OPENBLAS_NUM_THREADS": "1",
    "OMP_NUM_THREADS": "1",
    "MKL_NUM_THREADS": "1",
    "NUMEXPR_NUM_THREADS": "1",
}


def limit_worker_blas_threads() -> None:
    """Pin the CURRENT process's BLAS/OpenMP pools to a single thread.

    For use inside pool-worker initializers (issue #122). Two layers, both
    needed:

    1. The :data:`SINGLE_THREAD_BLAS_ENV` variables -- effective for libraries
       loaded AFTER this call (and for ``spawn``-started children).
    2. A ``threadpoolctl`` runtime limit -- required under the ``fork`` start
       method (Linux server), where the parent's BLAS is already initialised
       with its full thread count and the inherited env variables are read too
       late to matter.

    When ``threadpoolctl`` is unavailable the env layer still applies and a
    warning is logged (no silent fallback).
    """
    os.environ.update(SINGLE_THREAD_BLAS_ENV)
    try:
        import threadpoolctl
    except ImportError:
        logger.warning(
            "[parallelism] threadpoolctl is not installed; BLAS thread pin "
            "falls back to environment variables only, which do NOT limit a "
            "fork-inherited, already-initialised BLAS. Install threadpoolctl "
            "for a reliable pin."
        )
        return
    threadpoolctl.ThreadpoolController().limit(limits=1)


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
