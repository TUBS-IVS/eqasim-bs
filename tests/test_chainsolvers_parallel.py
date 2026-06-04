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


def test_make_person_shards_caps_workers_at_person_count():
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

    df1, failed1 = scs._solve_chains_parallel(plans, persons, None, "carla", 99, 4, 0.0)
    df2, failed2 = scs._solve_chains_parallel(plans, persons, None, "carla", 99, 4, 0.0)

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
    df, failed = scs._solve_chains_parallel(plans, persons, None, "carla", 7, 4, 0.0)
    assert 999 in failed
    assert failed == sorted(failed)
    assert "pBAD#999" not in set(df["unique_person_id"])
