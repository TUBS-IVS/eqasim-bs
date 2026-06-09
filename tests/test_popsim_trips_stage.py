"""Tests for the popsim_mid trips_stage (Phase 5g.6 / Task 6).

Verifies that trips_stage.run() returns the canonical synthesis.population.trips
11-column contract plus euclidean_distance, and that the per-person departure-time
jitter preserves within-person trip ordering.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.popsim import trips_stage


def test_trips_stage_run_returns_contract_columns_and_euclidean():
    persons = pd.DataFrame({"person_id": ["A_1_0_1"], "H_ID": [1], "P_ID": [1]})
    wege = pd.DataFrame({"H_ID": [1, 1], "P_ID": [1, 1], "W_ID": [1, 2],
                         "W_ZWECK": [1, 8], "hvm": [4, 4], "W_SZS": [8, 17], "W_SZM": [0, 0],
                         "W_AZS": [8, 17], "W_AZM": [30, 20], "wegkm_imp": [12.0, 12.0]})
    out = trips_stage.run(persons, wege, random_seed=0)
    for col in ["person_id", "trip_index", "departure_time", "arrival_time",
                "preceding_purpose", "following_purpose", "is_first_trip",
                "is_last_trip", "trip_duration", "activity_duration", "mode",
                "euclidean_distance"]:
        assert col in out.columns
    assert np.allclose(out["euclidean_distance"], 12.0 * 1000 / 1.3)


def test_trips_stage_jitter_is_per_person_keeps_chain_ordered():
    # Two trips for one person; after jitter the within-person ordering must hold.
    persons = pd.DataFrame({"person_id": ["A_1_0_1"], "H_ID": [1], "P_ID": [1]})
    wege = pd.DataFrame({"H_ID": [1, 1], "P_ID": [1, 1], "W_ID": [1, 2],
                         "W_ZWECK": [1, 8], "hvm": [4, 4], "W_SZS": [8, 17], "W_SZM": [0, 0],
                         "W_AZS": [8, 17], "W_AZM": [30, 20], "wegkm_imp": [12.0, 12.0]})
    out = trips_stage.run(persons, wege, random_seed=0).sort_values("trip_index")
    deps = out["departure_time"].tolist()
    assert deps[0] <= deps[1]   # chain ordering preserved (per-person jitter, same offset)
