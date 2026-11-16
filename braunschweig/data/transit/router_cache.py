"""Prefetch cache in front of the batch pt router: one JVM start per search loop instead of one per round.

Every call of :func:`braunschweig.data.transit.connections.route_batch` starts ``RunBatchPublicTransportRouter``,
which spends most of its time loading network, schedule and injector before it routes. The timetable checks of the
in-commuters, the outside tours and the portal pt stays ask the router once per round of a sequential search
(one shift offset after the other). :class:`PrefetchRouter` lets such a loop route the SUPERSET of the tasks it can
ask in ONE call (:meth:`PrefetchRouter.prefetched`) and then answers each round from that batch.

Why the answers are the same. The Java worker routes each task on its own from (origin, destination, departure
time): no person, no random numbers, the identifier is only copied to the output. A task's trips row and legs rows
therefore do not depend on the batch it is routed in; checked on felix with real production tasks (run manifest
``docs/runs/pt-router-batch-invariance-2026-11-16.yml``). The rule and its consequences are recorded in ADR-0196.

Cache key: the five task values (origin x/y, destination x/y, departure time) as Python floats, i.e. exact equality
of the numbers the router reads. Two tasks with equal values share one routed answer; the requested identifier is
written into the returned rows.

Fallback transparency: inside a :meth:`PrefetchRouter.prefetched` block a task that is not in the cache (the loop
asked something the superset did not cover) is routed as before, in one call per round, and counted and logged
at WARNING as a fallback; the block's exit logs the hit and fallback counts. Outside a block every call routes
its tasks directly (cache answers are reused there too, and counted as hits).
"""
from __future__ import annotations

import contextlib
import logging

import numpy as np
import pandas as pd

from braunschweig.data.transit.connections import TASK_COLUMNS, Router

logger = logging.getLogger(__name__)

_KEY_COLUMNS = TASK_COLUMNS[1:]
_INTERNAL_ID = "__prefetch_id"


def task_keys(tasks: pd.DataFrame) -> list[tuple]:
    """The cache key of each task row: its five ``TASK_COLUMNS`` values (all but the identifier) as floats."""
    values = tasks[_KEY_COLUMNS].to_numpy(dtype=float)
    return [tuple(float(v) for v in row) for row in values]


class PrefetchRouter:
    """A ``Router`` (tasks -> ``(trips, legs)``) that answers from a content-keyed cache of routed tasks.

    ``router`` is the real router (in the stages ``functools.partial(route_batch, ...)``); keyword arguments of a
    call (``allow_no_route_at_all``) are passed to it unchanged whenever it is called. ``label`` names the stage in
    the log lines. ``calls`` counts the calls of the real router (= JVM starts in production).
    """

    def __init__(self, router: Router, *, label: str):
        self._router = router
        self.label = label
        self._ids: dict[tuple, str] = {}         # task key -> internal identifier of the routed answer
        self._trips: list[pd.DataFrame] = []     # routed trips rows, identifier = internal identifier
        self._legs: list[pd.DataFrame] = []
        self._trips_frame: pd.DataFrame | None = None
        self._legs_frame: pd.DataFrame | None = None
        self.calls = 0
        self.prefetch_calls = 0
        self.hits = 0
        self.routed = 0
        self.fallback_tasks = 0
        self.fallback_calls = 0
        self._in_block = False
        self._block = {"hits": 0, "fallback": 0, "fallback_calls": 0}

    # -- routing ---------------------------------------------------------------------------------------------------
    def _route_new(self, keys: list[tuple], frame: pd.DataFrame, **kwargs) -> None:
        """Route the rows of ``frame`` whose key is not cached yet (distinct keys, one real call) and cache them."""
        new_rows, seen = [], set()
        for row, key in enumerate(keys):
            if key not in self._ids and key not in seen:
                seen.add(key)
                new_rows.append(row)
        if not new_rows:
            return
        start = len(self._ids)
        internal = [f"c{start + k}" for k in range(len(new_rows))]
        batch = frame.iloc[new_rows][_KEY_COLUMNS].reset_index(drop=True)
        batch.insert(0, "identifier", internal)
        trips, legs = self._router(batch, **kwargs)
        self.calls += 1
        self.routed += len(batch)
        for k, row in enumerate(new_rows):
            self._ids[keys[row]] = internal[k]
        self._trips.append(trips)
        self._legs.append(legs)
        self._trips_frame = self._legs_frame = None

    def _frames(self) -> tuple[pd.DataFrame, pd.DataFrame]:
        if self._trips_frame is None:
            self._trips_frame = pd.concat(self._trips, ignore_index=True) if self._trips else pd.DataFrame(
                columns=["identifier"])
            self._legs_frame = pd.concat(self._legs, ignore_index=True) if self._legs else pd.DataFrame(
                columns=["identifier"])
        return self._trips_frame, self._legs_frame

    def prefetch(self, tasks: pd.DataFrame, **kwargs) -> int:
        """Route every task of ``tasks`` whose key is not cached yet in ONE real call; return the number routed.

        The identifiers of ``tasks`` are not used (duplicates allowed): the superset is keyed by content."""
        missing = [c for c in TASK_COLUMNS if c not in tasks.columns]
        if missing:
            raise ValueError(f"[pt-router cache] {self.label}: prefetch tasks lack columns {missing}")
        before_calls, before_routed = self.calls, self.routed
        self._route_new(task_keys(tasks), tasks, **kwargs)
        n_routed = self.routed - before_routed
        self.prefetch_calls += self.calls - before_calls
        logger.info("[pt-router cache] %s: prefetch of %d task(s), %d distinct new, routed in %d router call(s)",
                    self.label, len(tasks), n_routed, self.calls - before_calls)
        return n_routed

    @contextlib.contextmanager
    def prefetched(self, tasks: pd.DataFrame, **kwargs):
        """Prefetch ``tasks``, then run the block; a task the block asks that is not cached is a counted fallback.

        On exit one line logs the answers from the cache and the fallbacks (WARNING when there is any fallback:
        the superset of the loop missed a task, so a JVM start was spent that the prefetch should have saved)."""
        if self._in_block:
            raise RuntimeError(f"[pt-router cache] {self.label}: prefetched blocks do not nest")
        self.prefetch(tasks, **kwargs)
        self._in_block = True
        self._block = {"hits": 0, "fallback": 0, "fallback_calls": 0}
        try:
            yield self
        finally:
            self._in_block = False
            block = self._block
            asked = block["hits"] + block["fallback"]
            (logger.warning if block["fallback"] else logger.info)(
                "[pt-router cache] %s: loop asked %d task(s): from the prefetch %d (%.1f%%), fallback routed %d "
                "in %d router call(s); stage total so far %d router call(s) (%d prefetch), %d distinct task(s) "
                "routed", self.label, asked, block["hits"], 100.0 * block["hits"] / asked if asked else 100.0,
                block["fallback"], block["fallback_calls"], self.calls, self.prefetch_calls, self.routed)

    def __call__(self, tasks: pd.DataFrame, **kwargs) -> tuple[pd.DataFrame, pd.DataFrame]:
        """Answer ``tasks`` like the real router: trips rows for the routed tasks, legs rows of their pt legs, with
        the requested identifiers. Uncached tasks are routed in one real call first."""
        missing = [c for c in TASK_COLUMNS if c not in tasks.columns]
        if missing:
            raise ValueError(f"route_batch: task table lacks columns {missing}")
        task_ids = tasks["identifier"].astype(str)
        if task_ids.duplicated().any():
            raise ValueError(f"route_batch: duplicate task identifiers (first {task_ids[task_ids.duplicated()].head(5).tolist()}); "
                             "each task needs a unique identifier")
        for column in _KEY_COLUMNS:
            values = pd.to_numeric(tasks[column], errors="coerce").to_numpy(dtype=float)
            if (~np.isfinite(values)).any():
                # the real router raises for these before Java starts; let it do so with its own message
                return self._router(tasks, **kwargs)
        keys = task_keys(tasks)
        n_cached = sum(1 for key in keys if key in self._ids)
        n_new = len({key for key in keys if key not in self._ids})
        calls_before = self.calls
        if n_cached == 0:
            # Nothing cached: the real router gets the request's tasks with the caller's arguments, as before.
            self._route_new(keys, tasks, **kwargs)
        elif n_new:
            # Mixed request: the real router routes only the new tasks, so its "no route at all = broken run" check
            # would judge a narrower batch than the caller sent; it is judged over the whole request below instead.
            self._route_new(keys, tasks, **dict(kwargs, allow_no_route_at_all=True))
        if self._in_block:
            self._block["hits"] += n_cached
            self._block["fallback"] += len(keys) - n_cached
            self._block["fallback_calls"] += self.calls - calls_before
            if len(keys) - n_cached:
                self.fallback_tasks += len(keys) - n_cached
                self.fallback_calls += self.calls - calls_before
                logger.warning("[pt-router cache] %s: fallback: %d of %d task(s) (%d distinct) were not in the "
                               "prefetch and were routed directly", self.label, len(keys) - n_cached, len(keys), n_new)
        self.hits += n_cached
        request = pd.DataFrame({_INTERNAL_ID: [self._ids[key] for key in keys], "identifier": task_ids.to_numpy()})
        trips_all, legs_all = self._frames()
        trips, legs = _relabel(request, trips_all), _relabel(request, legs_all)
        if n_cached and len(tasks) and trips.empty and not kwargs.get("allow_no_route_at_all", False):
            # route_batch's strict check, over the whole request (ADR-0196 decision 2: inside a prefetched block the
            # loop's narrowed rounds are not checked; this applies to requests outside a block only)
            if not self._in_block:
                raise ValueError(f"route_batch: router output has missing {len(tasks)} of {len(tasks)} tasks, i.e. "
                                 f"no trips row at all; the run is broken ({self.label}, answered from the cache)")
        return trips, legs


def _relabel(request: pd.DataFrame, rows: pd.DataFrame) -> pd.DataFrame:
    """The rows of the routed answers that ``request`` asks for, with the requested identifier, in request order
    (rows of one answer keep their routed order); the column order of the router output is kept."""
    columns = list(rows.columns)
    if rows.empty:
        return rows.iloc[0:0][columns].reset_index(drop=True)
    renamed = rows.rename(columns={"identifier": _INTERNAL_ID})
    joined = request.merge(renamed, on=_INTERNAL_ID, how="inner", sort=False)
    return joined[columns].reset_index(drop=True)
