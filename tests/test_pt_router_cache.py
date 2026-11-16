"""Prefetch cache in front of the batch pt router (ADR-0196, audit package H)."""
import logging

import pandas as pd
import pytest

from braunschweig.data.transit.connections import TASK_COLUMNS
from braunschweig.data.transit.router_cache import PrefetchRouter, task_keys
from tests.pt_router_fake import FakeRouter


def _tasks(prefix, n, offset=0.0):
    return pd.DataFrame([(f"{prefix}{i}", 1000.0 * i, 0.5, 2000.0, 3000.0 + i, 3600.0 * (i % 5) + offset)
                         for i in range(n)], columns=TASK_COLUMNS)


def _sorted(frame):
    return frame.sort_values(list(frame.columns)).reset_index(drop=True)


def test_rounds_inside_a_prefetch_get_the_direct_answers_with_one_router_call(caplog):
    """Defects: an answer read for another task (wrong key or relabel), lost legs rows, extra JVM calls."""
    fake = FakeRouter()
    cached = PrefetchRouter(fake, label="test")
    superset = pd.concat([_tasks("a", 40, offset) for offset in (0.0, 1800.0, -1800.0)], ignore_index=True)
    with caplog.at_level(logging.INFO):
        with cached.prefetched(superset):
            # two narrowed rounds with their own identifiers, one with a task content asked twice
            for offset in (1800.0, -1800.0):
                round_tasks = _tasks("r", 40, offset).iloc[::3].reset_index(drop=True)
                round_tasks = pd.concat([round_tasks, round_tasks.iloc[:1].assign(identifier="dup")],
                                        ignore_index=True)
                trips, legs = cached(round_tasks)
                direct_trips, direct_legs = FakeRouter()(round_tasks, allow_no_route_at_all=True)
                pd.testing.assert_frame_equal(_sorted(trips), _sorted(direct_trips))
                pd.testing.assert_frame_equal(_sorted(legs), _sorted(direct_legs))
    assert fake.calls == 1 and fake.batches == [120]
    assert cached.fallback_tasks == 0
    assert {k for k in task_keys(superset)} == fake.asked


def test_a_task_outside_the_prefetch_is_routed_and_counted_as_fallback(caplog):
    fake = FakeRouter()
    cached = PrefetchRouter(fake, label="test")
    with caplog.at_level(logging.INFO):
        with cached.prefetched(_tasks("a", 10)):
            trips, _ = cached(pd.concat([_tasks("a", 10), _tasks("x", 3, offset=7.0)], ignore_index=True))
    assert fake.calls == 2 and fake.batches == [10, 3]
    assert cached.fallback_tasks == 3 and cached.fallback_calls == 1
    assert any(r.levelno == logging.WARNING and "fallback" in r.getMessage() for r in caplog.records)
    # the fallback call judges no-route over the whole request, so the narrow batch is routed leniently
    assert fake.kwargs[1] == {"allow_no_route_at_all": True}


def test_no_route_at_all_is_strict_over_the_whole_request_outside_a_block_only():
    """The 'no route at all = broken run' rule judges the superset and the caller's whole request (ADR-0196
    decision 2), never a narrowed round inside a prefetched block."""
    fake = FakeRouter(answer=lambda key: None if key[4] > 0 else "walk")
    cached = PrefetchRouter(fake, label="test")
    with pytest.raises(ValueError, match="no trips row at all"):
        cached.prefetch(_tasks("a", 3, offset=10.0))           # superset without any route: strict
    cached.prefetch(_tasks("a", 3, offset=20.0), allow_no_route_at_all=True)
    with cached.prefetched(_tasks("a", 3, offset=30.0), allow_no_route_at_all=True):
        trips, _ = cached(_tasks("b", 3, offset=30.0))          # narrowed round, all cached, no route: tolerated
        assert trips.empty
    with pytest.raises(ValueError, match="no trips row at all"):
        cached(_tasks("c", 3, offset=20.0))                     # outside a block, all cached, no route: strict
