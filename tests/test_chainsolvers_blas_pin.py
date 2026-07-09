"""Issue #122: pin BLAS/OpenMP threads in the chainsolvers worker pool.

``braunschweig/popsim/batch.py`` pins every PopulationSim subprocess to
single-threaded BLAS because unpinned workers oversubscribed the server
(~4000 threads / 64 cores, libc segfaults, 12 lost batches). The secondary
chainsolvers ``mp.Pool`` forks up to ~62 workers WITHOUT that pin -- the same
failure class, with the added twist that under the fork start method a
child-side env var alone is too late (the parent's BLAS is already
initialised), so the runtime limit is applied via ``threadpoolctl`` as well.
These tests pin the shared helper and its wiring into ``_init_chain_worker``.
"""
from __future__ import annotations

import os

import pandas as pd
import pytest

from braunschweig import parallelism
from braunschweig.synthesis.locations import secondary_chainsolvers as sc


_ENV_KEYS = (
    "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS",
    "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS",
)


@pytest.fixture()
def _restore_thread_state():
    """Save/restore env vars and threadpoolctl limits around a test."""
    saved_env = {k: os.environ.get(k) for k in _ENV_KEYS}
    import threadpoolctl
    # Highest original thread count per user_api, restored after the test so
    # the rest of the suite does not run on a single-threaded BLAS.
    saved_limits: dict = {}
    for pool_info in threadpoolctl.ThreadpoolController().info():
        api = pool_info["user_api"]
        saved_limits[api] = max(saved_limits.get(api, 1), pool_info["num_threads"])
    yield
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    for api, n_threads in saved_limits.items():
        threadpoolctl.threadpool_limits(limits=n_threads, user_api=api)


def test_single_thread_blas_env_is_shared_with_batch_runner() -> None:
    # Single source of truth: the batch runner's pin and the chainsolvers pin
    # must be the same mapping (no drift between the two parallel paths).
    from braunschweig.popsim import batch
    assert batch._SINGLE_THREAD_BLAS_ENV == parallelism.SINGLE_THREAD_BLAS_ENV
    assert set(parallelism.SINGLE_THREAD_BLAS_ENV) == set(_ENV_KEYS)
    assert all(v == "1" for v in parallelism.SINGLE_THREAD_BLAS_ENV.values())


def test_limit_worker_blas_threads_sets_env_and_runtime_limit(_restore_thread_state) -> None:
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    parallelism.limit_worker_blas_threads()
    for k in _ENV_KEYS:
        assert os.environ[k] == "1"
    # Runtime limit through threadpoolctl: every loaded BLAS/OpenMP pool must
    # now report a single thread (env alone is too late under fork).
    import threadpoolctl
    for pool_info in threadpoolctl.ThreadpoolController().info():
        assert pool_info["num_threads"] == 1


def test_init_chain_worker_applies_the_pin(_restore_thread_state) -> None:
    for k in _ENV_KEYS:
        os.environ.pop(k, None)
    locations = pd.DataFrame({"x": [1.0]})
    sc._init_chain_worker(locations, "carla", None)
    for k in _ENV_KEYS:
        assert os.environ[k] == "1"
    # Worker globals still set exactly as before.
    assert sc._WORKER_LOCATIONS_DF is locations
    assert sc._WORKER_SOLVER == "carla"
