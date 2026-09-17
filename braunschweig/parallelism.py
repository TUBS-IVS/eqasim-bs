"""Single-threaded-BLAS pinning helper for the pipeline's parallel side-processes.

Both parallel process pools of the pipeline (the PopulationSim batch runner,
``braunschweig/popsim/batch.py``, and the secondary-location chain solvers,
``braunschweig/synthesis/locations/secondary_chainsolvers/``) fork worker
processes whose numeric libraries must be pinned to a single BLAS/OpenMP
thread each -- otherwise N workers x an ncores-sized BLAS pool oversubscribes
the box (observed on the 64-core server: ~4000 threads, libc segfaults, 12
lost PopulationSim batches; see issue #122). :func:`limit_worker_blas_threads`
is that one shared pin, called from each pool's worker initializer, so the two
parallel paths cannot drift apart.

This module previously also resolved worker-COUNT sentinels
(``resolve_workers`` / ``available_cores``: a positive integer used verbatim,
``0``/``null``/``"auto"`` auto-scaled to ``cpu_count - reserve``). That
mechanism has moved to ``braunschweig/resources.py``, which derives one
machine-wide resource BUDGET (honouring ``sched_getaffinity`` and the
``EQASIM_CPU_BUDGET`` / ``EQASIM_MEM_BUDGET`` overrides, unlike the old
``os.cpu_count()``-only sentinel here) and resolves every worker-count key
against it -- ``resolve_popsim_workers`` / ``effective_popsim_workers`` for
the PopulationSim pool, ``resolve_chainsolver_workers`` /
``effective_chainsolver_workers`` for the chainsolver pool (ADR-0126). Both
resolvers are called at the point of use and log the effective count, so the
resolved parallelism stays traceable exactly as before, just from one place
instead of two that could disagree.
"""
from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

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
