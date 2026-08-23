"""Parallel chain solving: person shards across worker processes.

Person chains are independent, so they are sharded across workers, each
with its own chainsolvers context seeded deterministically from
``random_seed`` and the shard index (``_derive_shard_seed``). The result is
fully reproducible for a fixed worker count but is a DIFFERENT (equally
valid) Monte-Carlo realisation than the single-RNG serial path. Worker
state lives in the module-level ``_WORKER_*`` globals set by
``_init_chain_worker`` (per-process, set once by the pool initializer).

Extracted verbatim from the stage module (``__init__``); see the package
docstring for the stage-level context.
"""

from __future__ import annotations

import copy
import multiprocessing as mp
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from concurrent.futures.process import BrokenProcessPool
from typing import Any, Dict, List, Tuple

import numpy as np
import pandas as pd

from braunschweig import parallelism

from .candidates import build_scorer
from .solver_defaults import DEFAULT_CHAIN_SOLVER


# ---------------------------------------------------------------------------
# Parallel chain solving
#
# Person chains are independent, so the population is sharded across worker
# processes. Each worker builds its own chainsolvers context (one cs.setup per
# shard) seeded deterministically from (base_seed, shard_index), so the result
# is fully reproducible given the seed and the worker count. Shard results are
# recombined in shard-index order regardless of completion order, so the output
# does not depend on scheduling.
# ---------------------------------------------------------------------------

# Per-leg result columns chainsolvers' solve() returns (used for the empty
# frame when no bounded legs are placed).
_CHAIN_RESULT_COLUMNS = [
    "unique_person_id", "unique_leg_id", "to_act_type",
    "distance_meters", "from_x", "from_y", "to_x", "to_y",
    "to_act_identifier",
]

# Number of persons per cs.solve() call within a shard. Solving in chunks
# amortises chainsolvers' per-call validation overhead; on a chunk failure the
# shard retries that chunk's persons individually so one bad person does not
# drop the rest.
_CHAIN_CHUNK_SIZE = 500

# Worker-process globals: the (read-only) locations table, solver name, and
# scorer spec are sent once per worker via the Pool initializer instead of being
# pickled with every task.
_WORKER_LOCATIONS_DF = None
_WORKER_SOLVER = None
_WORKER_SCORER_SPEC = None


def _empty_chain_result_df() -> pd.DataFrame:
    return pd.DataFrame(columns=_CHAIN_RESULT_COLUMNS)


def _person_row_ranges(plans_df: pd.DataFrame, uid_col: str = "unique_person_id"):
    """Per-person contiguous row ranges of ``plans_df``, in appearance order.

    ``plans_df`` is built problem-by-problem, so all rows of one
    ``unique_person_id`` are adjacent. This computes ``(uid_order, starts,
    ends)`` so person/chunk/shard sub-frames can be taken as contiguous
    ``iloc`` slices instead of materialising every person's sub-frame at once
    via ``dict(tuple(groupby))`` (a memory spike at 100%: every sub-frame plus
    the dict alive simultaneously).

    Returns ``None`` when some person's rows are NOT contiguous (run count !=
    unique count) -- callers then fall back to the groupby dict, so slicing can
    never silently mix persons.
    """
    values = plans_df[uid_col].to_numpy()
    if len(values) == 0:
        empty = np.array([], dtype=np.int64)
        return values, empty, empty
    change = np.flatnonzero(values[1:] != values[:-1]) + 1
    starts = np.concatenate(([0], change))
    ends = np.concatenate((change, [len(values)]))
    uid_order = values[starts]
    if len(uid_order) != pd.unique(values).size:
        return None
    return uid_order, starts, ends


def _make_person_shards(unique_persons: List[Any], n_workers: int) -> List[Tuple[int, List[Any]]]:
    """Split the person list into ``n_workers`` contiguous, balanced shards.

    Contiguous index-based slicing keeps the assignment deterministic and
    independent of the worker count's scheduling, so a run is reproducible.
    """
    n_workers = max(1, min(n_workers, len(unique_persons))) if unique_persons else 1
    shards: List[Tuple[int, List[Any]]] = []
    for shard_index, shard in enumerate(np.array_split(np.asarray(unique_persons, dtype=object), n_workers)):
        shard_list = list(shard)
        if shard_list:
            shards.append((shard_index, shard_list))
    return shards


def _derive_shard_seed(base_seed: int, shard_index: int) -> int:
    """Deterministic per-shard rng seed derived from the run seed and shard.

    Uses numpy ``SeedSequence`` so distinct shards get well-separated streams;
    the same (base_seed, shard_index) always yields the same seed.
    """
    return int(np.random.SeedSequence([int(base_seed), int(shard_index)]).generate_state(1)[0])


def _init_chain_worker(locations_df, solver, scorer_spec=None) -> None:
    # Pin BLAS/OpenMP to one thread FIRST (issue #122): with up to ~62 workers
    # each opening an ncores-sized BLAS pool, the box oversubscribes to
    # n_workers x ncores threads -- the exact failure class that segfaulted the
    # PopulationSim batches (see braunschweig.parallelism / popsim.batch).
    # Under fork the parent's BLAS is already initialised, so the helper also
    # applies a threadpoolctl runtime limit, not just the env variables.
    parallelism.limit_worker_blas_threads()
    global _WORKER_LOCATIONS_DF, _WORKER_SOLVER, _WORKER_SCORER_SPEC
    _WORKER_LOCATIONS_DF = locations_df
    _WORKER_SOLVER = solver
    _WORKER_SCORER_SPEC = scorer_spec


def _solve_person_shard(task):
    """Solve one shard of persons. Runs in a worker process (or in-process for
    the serial path). Returns ``(shard_index, result_df_or_None, failed_idx)``.

    Mirrors the legacy chunked solve loop exactly so the single-shard, seed=
    base_seed case is byte-identical to the pre-parallel serial behaviour.
    """
    import logging as _logging

    import chainsolvers as cs

    for _name in ("chainsolvers", "chainsolvers.io", "chainsolvers.locations"):
        _logging.getLogger(_name).setLevel(_logging.WARNING)

    shard_index, shard_uids, shard_df, shard_seed = task
    if _WORKER_SCORER_SPEC:
        # "_cs_parameters" is a non-Scorer key carrying the optional carla
        # selection parameters dict; pop it before forwarding to build_scorer.
        scorer_spec_copy = dict(_WORKER_SCORER_SPEC)
        cs_parameters = scorer_spec_copy.pop("_cs_parameters", None)
        scorer = build_scorer(**scorer_spec_copy)
    else:
        scorer = None
        cs_parameters = None
    ctx = cs.setup(
        locations_df=_WORKER_LOCATIONS_DF,
        solver=_WORKER_SOLVER or DEFAULT_CHAIN_SOLVER,
        rng_seed=int(shard_seed),
        scorer=scorer,
        **({"parameters": cs_parameters} if cs_parameters is not None else {}),
    )

    # Person sub-frames are contiguous iloc slices of shard_df (rows are built
    # problem-by-problem), verified at runtime; the groupby dict is only the
    # fallback for a non-contiguous frame. A chunk of consecutive persons is
    # one contiguous block whose reset_index(drop=True) is identical (rows,
    # order, RangeIndex) to the legacy per-person concat.
    ranges = _person_row_ranges(shard_df)
    use_slices = (
        ranges is not None
        and np.array_equal(ranges[0], np.asarray(shard_uids, dtype=object))
    )
    if use_slices:
        _, row_starts, row_ends = ranges
    else:
        by_person = dict(tuple(shard_df.groupby("unique_person_id", sort=False)))

    result_chunks: List[pd.DataFrame] = []
    failed_problem_idx: List[int] = []

    for start in range(0, len(shard_uids), _CHAIN_CHUNK_SIZE):
        chunk_uids = shard_uids[start:start + _CHAIN_CHUNK_SIZE]
        if use_slices:
            chunk_df = shard_df.iloc[
                row_starts[start]:row_ends[start + len(chunk_uids) - 1]
            ].reset_index(drop=True)
        else:
            chunk_df = pd.concat([by_person[u] for u in chunk_uids], ignore_index=True)
        try:
            res_df, _seg, _v = cs.solve(ctx=ctx, plans_df=chunk_df)
            result_chunks.append(res_df)
        except Exception:
            # Retry per-person to isolate the failures.
            for offset, uid in enumerate(chunk_uids):
                person_chunk = (
                    shard_df.iloc[row_starts[start + offset]:row_ends[start + offset]]
                    if use_slices else by_person[uid]
                )
                try:
                    res_df, _seg, _v = cs.solve(ctx=ctx, plans_df=person_chunk)
                    result_chunks.append(res_df)
                except Exception:
                    try:
                        _, prob_idx_str = str(uid).rsplit("#", 1)
                        failed_problem_idx.append(int(prob_idx_str))
                    except ValueError:
                        pass

    result_df = pd.concat(result_chunks, ignore_index=True) if result_chunks else None
    return shard_index, result_df, failed_problem_idx


#: How many executor generations a shard set may be run through before the stage
#: gives up. 1 = no recovery (fail on the first lost worker). The default allows
#: two retries, because the observed cause of a lost worker is transient memory
#: pressure from a CONCURRENT workload (issue #344): the same shard usually
#: succeeds once the competitor is gone.
DEFAULT_SHARD_ATTEMPTS = 3


def _run_shards_with_recovery(tasks, executor_kwargs, progress,
                              executor_factory=ProcessPoolExecutor,
                              max_attempts=DEFAULT_SHARD_ATTEMPTS,
                              worker_function=None):
    """Solve every shard, surviving worker processes that are killed outright.

    Returns ``(results_by_shard_index, failed_problem_idx)``.

    Why not ``multiprocessing.Pool.imap_unordered`` (what this replaces): Pool
    never notices a worker killed mid-task. It respawns the worker to keep the
    pool size, but the task that died with it is never re-queued and its result
    never arrives, so the consuming loop waits for it forever. That is exactly
    what happened on 2026-08-20: the kernel OOM killer removed one of 62 shard
    workers (a second heavy run was competing for memory), 61 results were
    delivered, and the stage then sat at zero CPU for four hours until it was
    killed by hand -- losing 7.6 h of completed shard work with it.
    ``ProcessPoolExecutor`` raises ``BrokenProcessPool`` on the same event
    (verified by reproduction on the run server's interpreter), which turns the
    silent stall into a recoverable failure.

    Recovery keeps the shard definitions untouched, so it cannot change results:
    shards, their person slices and their per-shard seeds are identical on a
    retry, and results are still recombined in shard-index order. A retried
    shard therefore reproduces bit-identical output.

    Note that a broken pool fails EVERY future of that generation, including
    shards that had not started; all of them are simply resubmitted. Only
    ``BrokenProcessPool`` is recovered from -- an exception raised INSIDE a shard
    is a real defect and propagates unchanged, so a retry loop can never mask it.
    """
    # Injectable only so a test can drive the real executor with a trivial,
    # picklable task; production always solves shards.
    worker_function = worker_function or _solve_person_shard

    results_by_index: Dict[int, pd.DataFrame] = {}
    failed_problem_idx: List[int] = []
    pending = list(tasks)

    for attempt in range(1, max_attempts + 1):
        lost = []

        with executor_factory(**executor_kwargs) as executor:
            futures = {executor.submit(worker_function, task): task
                       for task in pending}

            for future in as_completed(futures):
                try:
                    shard_index, res_df, shard_failed = future.result()
                except BrokenProcessPool:
                    lost.append(futures[future])
                    continue

                results_by_index[shard_index] = res_df
                failed_problem_idx.extend(shard_failed)
                progress(len(results_by_index), len(tasks))

        if not lost:
            return results_by_index, failed_problem_idx

        lost_indices = sorted(task[0] for task in lost)
        # Make the recovery observable: a silently retried shard would hide the
        # memory pressure that caused it (project rule on fallback transparency).
        print(
            f"[braunschweig.secondary_chainsolvers] WARNING! attempt {attempt}/"
            f"{max_attempts}: {len(lost)} shard worker(s) died (shards "
            f"{lost_indices}); {len(results_by_index)}/{len(tasks)} shards are "
            "already done and are NOT recomputed. The usual cause is memory "
            "pressure from a concurrent run (issue #344); retrying the lost "
            "shards in a fresh executor.",
            flush=True,
        )
        pending = lost

    raise RuntimeError(
        "secondary_chainsolvers: shard worker(s) died in every one of "
        f"{max_attempts} attempts; shards {sorted(task[0] for task in pending)} "
        "were never solved. The usual cause is memory exhaustion (check the "
        "kernel log for oom-kill entries and whether a second heavy run was "
        "active); see issue #344."
    )


def _solve_chains_parallel(plans_for_cs, unique_persons, locations_df, solver,
                           base_seed, n_workers, t0, scorer_spec=None,
                           shard_attempts=DEFAULT_SHARD_ATTEMPTS):
    """Solve all person chains across ``n_workers`` processes and recombine
    deterministically (results concatenated in shard-index order)."""
    shards = _make_person_shards(unique_persons, n_workers)

    # Shards are consecutive slices of unique_persons (appearance order), and
    # person rows are contiguous in plans_for_cs, so each shard frame is one
    # contiguous iloc block -- identical (rows, order, fresh RangeIndex) to the
    # legacy per-person concat, without holding every person's sub-frame alive
    # at once. Verified at runtime; groupby dict is the fallback.
    ranges = _person_row_ranges(plans_for_cs)
    use_slices = (
        ranges is not None
        and np.array_equal(ranges[0], np.asarray(unique_persons, dtype=object))
    )
    if not use_slices:
        by_person = dict(tuple(plans_for_cs.groupby("unique_person_id", sort=False)))

    tasks = []
    uid_pos = 0
    for shard_index, shard_uids in shards:
        if use_slices:
            _, row_starts, row_ends = ranges
            shard_frame = plans_for_cs.iloc[
                row_starts[uid_pos]:row_ends[uid_pos + len(shard_uids) - 1]
            ].reset_index(drop=True)
        else:
            shard_frame = pd.concat(
                [by_person[u] for u in shard_uids], ignore_index=True
            )
        tasks.append((
            shard_index, shard_uids, shard_frame,
            _derive_shard_seed(base_seed, shard_index),
        ))
        uid_pos += len(shard_uids)

    def report_progress(n_done, n_total):
        print(
            f"[braunschweig.secondary_chainsolvers] shard {n_done}/{n_total} "
            f"done (elapsed={time.time() - t0:.0f}s)",
            flush=True,
        )

    results_by_index, failed_problem_idx = _run_shards_with_recovery(
        tasks,
        executor_kwargs=dict(
            max_workers=len(tasks),
            # Platform default (fork on Linux), i.e. the same start method the
            # previous multiprocessing.Pool used -- workers inherit the parent's
            # pages copy-on-write instead of re-importing the world.
            mp_context=mp.get_context(),
            initializer=_init_chain_worker,
            initargs=(locations_df, solver, scorer_spec),
        ),
        progress=report_progress,
        max_attempts=shard_attempts,
    )

    ordered = [
        results_by_index[i] for i in sorted(results_by_index)
        if results_by_index[i] is not None
    ]
    result_df = pd.concat(ordered, ignore_index=True) if ordered else _empty_chain_result_df()
    # Deterministic order for the downstream fallback (which consumes the RNG).
    failed_problem_idx.sort()
    return result_df, failed_problem_idx
