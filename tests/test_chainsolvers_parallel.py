"""Tests for the parallel secondary-location chain solving.

``braunschweig.synthesis.locations.secondary_chainsolvers`` shards the
population across worker processes (flag ``braunschweig.chainsolvers.parallel``)
because person chains are independent. The chainsolvers package itself has no
internal parallelism, so the wrapper does the sharding. The contract under test:

* sharding is contiguous, balanced and covers every person exactly once;
* each shard gets a deterministic rng seed derived from (base_seed, shard_index)
  so a parallel run is fully reproducible;
* result recombination is by shard index, independent of completion order;
* the per-person fallback (a chunk that fails to solve is retried person by
  person, and genuinely failing persons are collected for the RDA fallback)
  still works.

chainsolvers is an optional dependency and is not installed in the test
environment, so a tiny in-memory fake module is injected into ``sys.modules``;
the worker code does ``import chainsolvers`` and picks it up. The actual Pool
path is exercised only where the 'fork' start method is available (Linux/CI),
because a spawned worker would not inherit the injected fake module.
"""
from __future__ import annotations

import multiprocessing
import sys
import types
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.synthesis.locations import secondary_chainsolvers as scs  # noqa: E402

FORK_AVAILABLE = "fork" in multiprocessing.get_all_start_methods()


# ---------------------------------------------------------------------------
# Fake chainsolvers module (module-level so it survives a fork).
# ---------------------------------------------------------------------------

def _fake_setup(locations_df=None, solver=None, rng_seed=None, **_kwargs):
    return {"seed": int(rng_seed)}


def _fake_solve(ctx=None, plans_df=None):
    """Return one result row per unique person, tagging the ctx seed used so a
    test can assert which seed solved which person. A person id containing
    'BAD' raises, to exercise the per-person fallback path."""
    uids = plans_df["unique_person_id"].drop_duplicates().tolist()
    rows = []
    for uid in uids:
        if "BAD" in str(uid):
            raise ValueError(f"unsolvable person {uid}")
        rows.append({
            "unique_person_id": uid,
            "unique_leg_id": f"{uid}_leg",
            "to_act_type": "leisure",
            "distance_meters": 1000.0,
            "from_x": 0.0, "from_y": 0.0, "to_x": 1.0, "to_y": 1.0,
            "to_act_identifier": "loc",
            "seed_used": ctx["seed"],
        })
    return pd.DataFrame(rows), None, None


def _install_fake_chainsolvers():
    module = types.ModuleType("chainsolvers")
    module.setup = _fake_setup
    module.solve = _fake_solve
    sys.modules["chainsolvers"] = module


@pytest.fixture
def fake_chainsolvers():
    previous = sys.modules.get("chainsolvers")
    _install_fake_chainsolvers()
    try:
        yield
    finally:
        if previous is not None:
            sys.modules["chainsolvers"] = previous
        else:
            sys.modules.pop("chainsolvers", None)


def _plans(person_ids):
    """Two legs per person, matching the columns the shard solver groups on."""
    rows = []
    for uid in person_ids:
        for leg in range(2):
            rows.append({"unique_person_id": uid, "leg": leg})
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# Pure helpers: sharding + seed derivation.
# ---------------------------------------------------------------------------

def test_make_person_shards_covers_every_person_once_in_order():
    persons = [f"p{i}" for i in range(10)]
    shards = scs._make_person_shards(persons, 3)
    assert len(shards) == 3
    flat = [uid for _idx, uids in shards for uid in uids]
    assert flat == persons  # contiguous, order-preserving, complete
    # balanced: largest and smallest shard differ by at most one
    sizes = [len(uids) for _idx, uids in shards]
    assert max(sizes) - min(sizes) <= 1


def test_make_person_shards_caps_shards_at_person_count():
    shards = scs._make_person_shards(["a", "b"], 8)
    assert len(shards) == 2
    assert [uid for _i, uids in shards for uid in uids] == ["a", "b"]


def test_make_person_shards_empty():
    assert scs._make_person_shards([], 4) == []


def test_derive_shard_seed_is_deterministic_and_distinct():
    base = 12345
    seeds = [scs._derive_shard_seed(base, i) for i in range(8)]
    # deterministic
    assert seeds == [scs._derive_shard_seed(base, i) for i in range(8)]
    # distinct streams per shard
    assert len(set(seeds)) == len(seeds)
    # a different base seed gives different values
    assert scs._derive_shard_seed(base, 0) != scs._derive_shard_seed(base + 1, 0)
    assert all(isinstance(s, int) for s in seeds)


# ---------------------------------------------------------------------------
# Single-shard solve (the in-process / serial path).
# ---------------------------------------------------------------------------

def test_solve_person_shard_solves_all_and_propagates_seed(fake_chainsolvers):
    persons = [f"p{i}#{i}" for i in range(5)]
    scs._init_chain_worker(locations_df=None, solver="carla")
    shard_index, result_df, failed = scs._solve_person_shard(
        (0, persons, _plans(persons), 777)
    )
    assert shard_index == 0
    assert failed == []
    assert sorted(result_df["unique_person_id"]) == sorted(persons)
    # every row solved with the shard seed we passed
    assert set(result_df["seed_used"]) == {777}


def test_solve_person_shard_isolates_failing_person_via_fallback(fake_chainsolvers):
    persons = ["p0#0", "pBAD#1", "p2#2"]
    scs._init_chain_worker(locations_df=None, solver="carla")
    _idx, result_df, failed = scs._solve_person_shard(
        (0, persons, _plans(persons), 5)
    )
    # the bad person's problem index is collected; the good ones are solved
    assert failed == [1]
    assert sorted(result_df["unique_person_id"]) == ["p0#0", "p2#2"]


# ---------------------------------------------------------------------------
# Parallel orchestration (fork only).
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not FORK_AVAILABLE, reason="requires the 'fork' start method so workers inherit the injected fake module")
def test_solve_chains_parallel_is_reproducible_and_complete(fake_chainsolvers):
    persons = [f"p{i}#{i}" for i in range(50)]
    plans = _plans(persons)

    df1, failed1 = scs._solve_chains_parallel(plans, persons, None, "carla", 99, 4, 0.0, n_shards=4)
    df2, failed2 = scs._solve_chains_parallel(plans, persons, None, "carla", 99, 4, 0.0, n_shards=4)

    # all persons placed, no failures
    assert sorted(df1["unique_person_id"]) == sorted(persons)
    assert failed1 == []
    # reproducible: identical output across runs (deterministic recombination)
    pd.testing.assert_frame_equal(df1, df2)
    assert failed1 == failed2


@pytest.mark.skipif(not FORK_AVAILABLE, reason="requires the 'fork' start method so workers inherit the injected fake module")
def test_solve_chains_parallel_collects_failures_sorted(fake_chainsolvers):
    persons = [f"p{i}#{i}" for i in range(20)] + ["pBAD#999"]
    plans = _plans(persons)
    df, failed = scs._solve_chains_parallel(plans, persons, None, "carla", 7, 4, 0.0, n_shards=4)
    assert 999 in failed
    assert failed == sorted(failed)
    assert "pBAD#999" not in set(df["unique_person_id"])


# ---------------------------------------------------------------------------
# Shard count vs worker count decoupling (issue-6 of the resource-adaptive
# config plan): the shard count is the SCIENTIFIC parameter (it fixes the
# partition and every shard's rng seed) while the worker count is purely
# OPERATIONAL (how many processes chew through that fixed shard set). Before
# this split the shard count WAS the worker count, so the machine's core
# count silently changed every person's random stream.
# ---------------------------------------------------------------------------

def test_shard_partition_depends_only_on_the_shard_count():
    persons = [f"p{i}" for i in range(97)]
    # Same shard count -> same partition, no matter how many processes will
    # execute it. A different shard count is a different (equally valid)
    # realisation, which is exactly why the shard count is hashed.
    assert scs._make_person_shards(persons, 8) == scs._make_person_shards(persons, 8)
    assert scs._make_person_shards(persons, 8) != scs._make_person_shards(persons, 62)


def test_shard_tasks_are_invariant_to_the_worker_count_and_pool_follows_workers(monkeypatch):
    """Platform-independent counterpart to the end-to-end invariance test.

    Captures what _solve_chains_parallel hands the executor instead of running a
    process pool, so it also runs on Windows (no fork). Asserts both halves of the
    split: the shard tasks depend only on n_shards, and the pool size depends only
    on n_workers.
    """
    from braunschweig.synthesis.locations.secondary_chainsolvers import parallel_solving

    captured = []

    def fake_run_shards(tasks, executor_kwargs, progress, **kwargs):
        captured.append((tasks, executor_kwargs))
        return {index: None for index, *_ in tasks}, []

    monkeypatch.setattr(parallel_solving, "_run_shards_with_recovery", fake_run_shards)

    persons = [f"p{i}#{i}" for i in range(50)]
    plans = _plans(persons)
    for n_workers in (4, 8):
        parallel_solving._solve_chains_parallel(
            plans, persons, None, "carla", 99, n_workers, 0.0, n_shards=8)

    (tasks_four, kwargs_four), (tasks_eight, kwargs_eight) = captured
    # Identical partition and identical per-shard seeds despite different pools.
    assert [(i, uids, seed) for i, uids, _frame, seed in tasks_four] \
        == [(i, uids, seed) for i, uids, _frame, seed in tasks_eight]
    assert len(tasks_four) == 8
    # The pool size follows the WORKER count, never the shard count.
    assert kwargs_four["max_workers"] == 4
    assert kwargs_eight["max_workers"] == 8


@pytest.mark.skipif(not FORK_AVAILABLE, reason="requires the 'fork' start method so workers inherit the injected fake module")
def test_solve_chains_parallel_is_invariant_to_the_worker_count(fake_chainsolvers):
    """The scientifically decisive test: the machine's core count must not change
    secondary locations. Same input, same shard count, different worker counts ->
    byte-identical result. This fails on the pre-split code, where the shard count
    WAS the worker count."""
    persons = [f"p{i}#{i}" for i in range(50)]
    plans = _plans(persons)

    with_four, failed_four = scs._solve_chains_parallel(
        plans, persons, None, "carla", 99, 4, 0.0, n_shards=8)
    with_eight, failed_eight = scs._solve_chains_parallel(
        plans, persons, None, "carla", 99, 8, 0.0, n_shards=8)

    pd.testing.assert_frame_equal(with_four, with_eight)
    assert failed_four == failed_eight


@pytest.mark.skipif(not FORK_AVAILABLE, reason="requires the 'fork' start method so workers inherit the injected fake module")
def test_a_single_worker_still_produces_the_sharded_realisation(fake_chainsolvers):
    """One worker must not silently fall back to the serial single-shard result."""
    persons = [f"p{i}#{i}" for i in range(50)]
    plans = _plans(persons)

    with_one, _ = scs._solve_chains_parallel(
        plans, persons, None, "carla", 99, 1, 0.0, n_shards=8)
    with_eight, _ = scs._solve_chains_parallel(
        plans, persons, None, "carla", 99, 8, 0.0, n_shards=8)

    pd.testing.assert_frame_equal(with_one, with_eight)


# ---------------------------------------------------------------------------
# configure() volatility contract: braunschweig.chainsolvers.shards must stay
# HASHED (a changed partition invalidates the cache, as it must) while
# braunschweig.chainsolvers.processes must be VOLATILE (an operational change
# must never invalidate the cache). Nothing else in the suite asserts this --
# a later edit that flips either flag would stay green everywhere else while
# silently reusing a cached artifact under a partition that never produced it,
# or forcing an unnecessary re-run on every worker-count tweak.
# ---------------------------------------------------------------------------

class _RecordingContext:
    """configure()-time context: records declared options and volatile flags.

    Same shape as tests/test_java_hang_watchdog.py::_RecordingContext, so a
    stage's ``configure()`` can be run against it directly and the resulting
    ``declared`` values / ``volatile`` set inspected.
    """

    def __init__(self):
        self.declared = {}
        self.volatile = set()

    def stage(self, name, *args, **kwargs):
        return None

    def config(self, name, *args, **kwargs):
        self.declared[name] = args[0] if args else None
        if kwargs.get("volatile"):
            self.volatile.add(name)
        return self.declared[name]


def test_shard_count_is_hashed_but_worker_count_is_volatile():
    """The shard count is SCIENTIFIC (it fixes the partition and every shard's
    rng seed), so it must stay OUT of the volatile set -- changing it has to
    invalidate the stage cache. The worker count is purely OPERATIONAL since the
    split, so it must be volatile -- changing it must never force a re-run."""
    ctx = _RecordingContext()
    scs.configure(ctx)
    assert ctx.declared["braunschweig.chainsolvers.shards"] == scs.DEFAULT_CHAIN_SHARDS
    assert "braunschweig.chainsolvers.shards" not in ctx.volatile
    assert "braunschweig.chainsolvers.processes" in ctx.volatile


# ---------------------------------------------------------------------------
# braunschweig.chainsolvers.shards has no auto sentinel, unlike the adjacent
# braunschweig.chainsolvers.processes: 0 would silently route every run to the
# serial single-shard realisation (the n_shards > 1 gate) with nothing in the
# log naming the cause.
# ---------------------------------------------------------------------------

def test_chain_shards_must_be_a_positive_integer():
    with pytest.raises(ValueError, match="shards must be a positive integer"):
        scs._resolve_chain_shards(0)
    with pytest.raises(ValueError, match="got 0"):
        scs._resolve_chain_shards(0)
    with pytest.raises(ValueError, match="got -1"):
        scs._resolve_chain_shards(-1)
    assert scs._resolve_chain_shards(62) == 62


def test_chain_shards_rejects_a_non_integral_value():
    # int(3.7) truncates to 3, silently using a different shard count -- and
    # therefore a different partition and per-shard seed -- than the config
    # states. An integral float is still a legitimate spelling of an int.
    with pytest.raises(ValueError, match="must be a positive integer"):
        scs._resolve_chain_shards(3.7)
    assert scs._resolve_chain_shards(62.0) == 62
