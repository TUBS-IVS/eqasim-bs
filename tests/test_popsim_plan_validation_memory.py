"""repair_trips must not hold many copies of the trip table at once (trips_stage memory, ADR-0135).

At 100 % the trips_stage driver reached 86.3 GiB of RSS on the 94.3 GiB run server, 10.1 GiB short
of the machine's limit, inside ``PlanValidator.repair_trips``
(docs/runs/chainsolver-pool-memory-2026-09-28.yml). A synthetic table as wide as the MiD trip table
shows why: the repair held about seven copies of it at its peak -- a redundant copy before every
sort, a ``reset_index`` copy after it, the time-repaired and the sorted frames kept alive through the
closure append, and ``hts.fix_trip_times`` / ``hts.compute_activity_duration`` shifting every donor
column of the table although they read four columns.
"""
from __future__ import annotations

import tracemalloc

import numpy as np
import pandas as pd

from braunschweig.popsim import plan_validation
from data.hts import hts

PURPOSES = ["work", "education", "shop", "leisure", "other", "escort"]


def _wide_trips(n_persons=2000, donor_columns=100, seed=4):
    """Deterministic trip chains (a fifth not ending at home, 1 % NaN departures) plus as many
    numeric donor columns as the MiD Wege frame carries along."""
    rng = np.random.RandomState(seed)
    rows = []
    for person in range(n_persons):
        n_trips = int(rng.randint(1, 7))
        purposes = ["home"] + [PURPOSES[i] for i in rng.randint(0, len(PURPOSES), n_trips)]
        if rng.rand() < 0.8:
            purposes[-1] = "home"
        clock = float(rng.randint(5 * 3600, 10 * 3600))
        for index in range(n_trips):
            travel = float(rng.randint(300, 3600))
            departure = np.nan if rng.rand() < 0.01 else clock
            rows.append((f"p{person}", index, departure, clock + travel,
                         purposes[index], purposes[index + 1], "car"))
            clock += travel + float(rng.randint(600, 4 * 3600))
    trips = pd.DataFrame(rows, columns=["person_id", "trip_id", "departure_time", "arrival_time",
                                        "preceding_purpose", "following_purpose", "mode"])
    trips["is_first_trip"] = trips.groupby("person_id").cumcount() == 0
    trips["is_last_trip"] = trips.groupby("person_id").cumcount(ascending=False) == 0
    trips["trip_duration"] = trips["arrival_time"] - trips["departure_time"]
    donors = pd.DataFrame(rng.rand(len(trips), donor_columns),
                          columns=[f"donor_column_{i}" for i in range(donor_columns)])
    return pd.concat([trips, donors], axis=1)


def _peak_table_copies(trips):
    """Peak memory ``repair_trips`` allocates above its entry, in copies of ``trips``."""
    size_bytes = trips.memory_usage(index=True, deep=True).sum()
    validator = plan_validation.PlanValidator(require_home_closure=True)
    tracemalloc.start()
    try:
        entry_bytes, _ = tracemalloc.get_traced_memory()
        tracemalloc.reset_peak()
        validator.repair_trips(trips)
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return (peak_bytes - entry_bytes) / size_bytes


def test_repair_trips_holds_at_most_four_copies_of_a_wide_trip_table():
    # Measured on this fixture: about 7.0 copies before ADR-0135. Four leaves room for the
    # copies a sort-and-append repair cannot avoid (input, sorted chains, their concatenation,
    # its sort) and fails well before the old behaviour returns.
    copies = _peak_table_copies(_wide_trips())
    assert copies < 4.0, f"repair_trips peaked at {copies:.2f} copies of the trip table"


def _chains_exercising_every_fix_trip_times_branch():
    """Per person one fix_trip_times branch: swapped times, a midnight crossing, a trip after
    its successor, an intersecting and an included trip, plus a clean chain."""
    rows = [
        ("swap", 0, 1000.0, 1500.0), ("swap", 1, 3000.0, 2500.0), ("swap", 2, 4000.0, 4500.0),
        ("midnight", 0, 80000.0, 85000.0), ("midnight", 1, 86000.0, 1000.0),
        ("after", 0, 80000.0, 81000.0), ("after", 1, 2000.0, 3000.0),
        ("intersect", 0, 1000.0, 2500.0), ("intersect", 1, 2000.0, 3000.0),
        ("included", 0, 2100.0, 2200.0), ("included", 1, 2000.0, 3000.0),
        ("clean", 0, 1000.0, 1100.0), ("clean", 1, 2000.0, 2100.0),
    ]
    trips = pd.DataFrame(rows, columns=["person_id", "trip_id", "departure_time", "arrival_time"])
    trips["preceding_purpose"] = "home"
    trips["following_purpose"] = "work"
    trips["donor_value"] = np.arange(len(trips), dtype=float)
    return trips


def test_the_narrow_time_repair_equals_the_eqasim_helper_on_the_full_table():
    """_eqasim_fix_trip_times hands hts.fix_trip_times only the four columns it reads. That is
    only sound while the helper reads nothing else: this pins it against the full-table call on
    chains that reach every branch of the helper."""
    trips = _chains_exercising_every_fix_trip_times_branch()
    reference = hts.fix_trip_times(hts.compute_first_last(trips.sort_values(["person_id", "trip_id"])))

    repaired = plan_validation._eqasim_fix_trip_times(trips)

    pd.testing.assert_frame_equal(repaired, reference)
    assert not repaired[["departure_time", "arrival_time"]].equals(
        trips.sort_values(["person_id", "trip_id"])[["departure_time", "arrival_time"]]), (
        "the fixture must actually be repaired, or this comparison proves nothing")


def test_the_narrow_time_repair_leaves_the_callers_table_untouched():
    trips = _chains_exercising_every_fix_trip_times_branch()
    untouched = trips.copy()
    plan_validation._eqasim_fix_trip_times(trips)
    pd.testing.assert_frame_equal(trips, untouched)
