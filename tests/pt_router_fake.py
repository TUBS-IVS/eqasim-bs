"""A deterministic fake of the batch pt router for the prefetch tests (ADR-0196).

Like the Java worker it answers every task from its five values only (origin, destination, departure time): the
answer does not depend on the batch. It counts its calls, records every task key it was asked, and its rows carry a
fingerprint of the task so a test can see which answer the code read.
"""
from __future__ import annotations

import hashlib

import pandas as pd

from braunschweig.data.transit.router_cache import task_keys

TRIPS_COLUMNS = ["identifier", "is_only_walk", "access_distance_km", "egress_distance_km", "transfer_distance_km",
                 "total_walk_distance_km", "egress_travel_time_min", "fingerprint"]
LEGS_COLUMNS = ["identifier", "leg_index", "access_stop_id", "egress_stop_id", "egress_time"]


def _hash(key) -> int:
    return int(hashlib.sha256(repr(key).encode()).hexdigest()[:8], 16)


class FakeRouter:
    """``answer(key) -> None | "walk" | ("pt", n_legs, stop_ids)``; the default draws from the key's hash:
    about a third no route, a third short walk, a third pt trips with 1-2 legs at the given stops."""

    def __init__(self, stops=("s1", "s2"), answer=None, connect_share: float = 0.35):
        self.stops = list(stops)
        self.answer = answer or self._default
        self.connect_share = connect_share
        self.calls = 0
        self.asked: set = set()
        self.batches: list[int] = []
        self.kwargs: list[dict] = []

    def _default(self, key):
        h = _hash(key)
        u = (h % 1000) / 1000.0
        if u < 1.0 - 2 * self.connect_share:
            return None
        if u < 1.0 - self.connect_share:
            return "far_walk"
        return ("pt", 1 + h % 2, [self.stops[(h >> 4) % len(self.stops)], self.stops[(h >> 8) % len(self.stops)]])

    def __call__(self, tasks: pd.DataFrame, **kwargs):
        self.calls += 1
        self.batches.append(len(tasks))
        self.kwargs.append(kwargs)
        trips, legs = [], []
        for identifier, key in zip(tasks["identifier"].astype(str), task_keys(tasks)):
            self.asked.add(key)
            result = self.answer(key)
            fingerprint = _hash(key)
            if result is None:
                continue
            if result == "far_walk":
                trips.append((identifier, 1, 9.0, 0.0, 0.0, 9.0, 0.0, fingerprint))
                continue
            if result == "walk":
                trips.append((identifier, 1, 0.2, 0.0, 0.0, 0.2, 0.0, fingerprint))
                continue
            _, n_legs, stops = result
            trips.append((identifier, 0, 0.3, 0.3, 0.0, 0.6, 2.0, fingerprint))
            for leg in range(n_legs):
                legs.append((identifier, leg, stops[0], stops[1], key[4] + 600.0 * (leg + 1)))
        trips = pd.DataFrame(trips, columns=TRIPS_COLUMNS)
        legs = pd.DataFrame(legs, columns=LEGS_COLUMNS)
        if len(tasks) and trips.empty and not kwargs.get("allow_no_route_at_all", False):
            raise ValueError("fake route_batch: no trips row at all; the run is broken")
        return trips, legs
