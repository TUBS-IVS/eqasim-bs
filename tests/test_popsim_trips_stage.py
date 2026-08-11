"""Tests for the popsim_mid trips_stage (Phase 5g.6 / Task 6).

Verifies that trips_stage.run() returns the canonical synthesis.population.trips
11-column contract plus euclidean_distance, and that the per-person departure-time
jitter preserves within-person trip ordering.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import trips_stage


def test_trips_stage_run_returns_contract_columns_and_euclidean():
    persons = pd.DataFrame({"person_id": ["A_1_0_1"], "H_ID": [1], "P_ID": [1]})
    wege = pd.DataFrame({"H_ID": [1, 1], "P_ID": [1, 1], "W_ID": [1, 2],
                         "W_ZWECK": [1, 8], "hvm_imp": [4, 4], "W_SZS": [8, 17], "W_SZM": [0, 0],
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
                         "W_ZWECK": [1, 8], "hvm_imp": [4, 4], "W_SZS": [8, 17], "W_SZM": [0, 0],
                         "W_AZS": [8, 17], "W_AZM": [30, 20], "wegkm_imp": [12.0, 12.0]})
    out = trips_stage.run(persons, wege, random_seed=0).sort_values("trip_index")
    deps = out["departure_time"].tolist()
    assert deps[0] <= deps[1]   # chain ordering preserved (per-person jitter, same offset)


# ---------------------------------------------------------------------------
# Task 2.3 C: absolute plan-time bound + NaN-free guarantee in trips_stage.run.
# ---------------------------------------------------------------------------

def test_run_asserts_times_within_bound():
    import pandas as pd, pytest
    from braunschweig.popsim import trips_stage
    df = pd.DataFrame({"person_id": [1, 1], "departure_time": [3600.0, 130000.0],
                       "arrival_time": [4000.0, 131000.0]})
    with pytest.raises(AssertionError, match="exceed"):
        trips_stage._assert_time_bound(df, max_seconds=36 * 3600)


def test_run_resamples_coded_time_persons_no_nan_in_output():
    """End-to-end: a coded-time (701) person must come out of trips_stage.run
    with a valid resampled chain (same ZENSUS100m cell donor), never NaN times."""
    persons = pd.DataFrame({
        "person_id": ["pA", "pB"], "H_ID": [1, 2], "P_ID": [1, 1],
        "ZENSUS100m": ["c1", "c1"],
    })
    wege = pd.DataFrame({
        "H_ID": [1, 2], "P_ID": [1, 1], "W_ID": [1, 1],
        "W_ZWECK": [1, 1], "hvm_imp": [4, 4],
        "W_SZS": [701, 8], "W_SZM": [701, 0],
        "W_AZS": [701, 9], "W_AZM": [701, 0],
        "wegkm_imp": [5.0, 5.0],
    })
    out = trips_stage.run(persons, wege, random_seed=0)
    assert set(out["person_id"].unique()) == {"pA", "pB"}
    assert out["departure_time"].notna().all()
    assert out["arrival_time"].notna().all()
    assert (out["departure_time"] <= trips_stage.MAX_PLAN_TIME_SECONDS).all()


# ---------------------------------------------------------------------------
# Task 3: thread escort_purpose through trips_stage.run and the donor sources.
# ---------------------------------------------------------------------------

def test_run_escort_purpose_flag_produces_escort_trips():
    persons = pd.DataFrame({"person_id": [1], "H_ID": [10], "P_ID": [1]})
    wege = pd.DataFrame({
        "H_ID": [10, 10], "P_ID": [1, 1], "W_ID": [1, 2],
        "W_ZWECK": [6, 8], "hvm_imp": [4, 4],
        "W_SZS": [8, 12], "W_SZM": [0, 0], "W_AZS": [8, 12], "W_AZM": [30, 30],
        "wegkm_imp": [5.0, 5.0], "wegmin_imp1": [30.0, 30.0], "W_GEW": [1.0, 1.0],
    })
    table_on = trips_stage.run(persons, wege, random_seed=1, escort_purpose=True)
    assert "escort" in set(table_on["following_purpose"])
    table_off = trips_stage.run(persons, wege, random_seed=1, escort_purpose=False)
    assert "escort" not in set(table_off["following_purpose"])


def test_entd_source_rejects_escort_purpose():
    from braunschweig.popsim.sources.entd import EntdSource
    with pytest.raises(NotImplementedError, match="escort_purpose"):
        EntdSource().build_trips(
            pd.DataFrame({"person_id": []}), pd.DataFrame(), random_seed=1,
            escort_purpose=True,
        )


# ---------------------------------------------------------------------------
# Issue #256: thread escort_passive_education through trips_stage.run and the
# ENTD source guard (mirrors the #201 escort_purpose tests directly above).
# ---------------------------------------------------------------------------

def test_trips_stage_threads_escort_passive_education():
    """A W_ZWECK-13 (passive escort) leg must become 'education', not 'escort',
    when escort_passive_education is threaded through trips_stage.run alongside
    escort_purpose."""
    persons = pd.DataFrame({"person_id": [1], "H_ID": [10], "P_ID": [1]})
    wege = pd.DataFrame({
        "H_ID": [10, 10], "P_ID": [1, 1], "W_ID": [1, 2],
        "W_ZWECK": [13, 8], "hvm_imp": [4, 4],
        "W_SZS": [8, 12], "W_SZM": [0, 0], "W_AZS": [8, 12], "W_AZM": [30, 30],
        "wegkm_imp": [5.0, 5.0], "wegmin_imp1": [30.0, 30.0], "W_GEW": [1.0, 1.0],
    })
    table = trips_stage.run(
        persons, wege, random_seed=1,
        escort_purpose=True, escort_passive_education=True,
    )
    mask_13 = table["W_ZWECK"] == 13
    assert mask_13.any(), "the W_ZWECK-13 donor leg must survive into the output table"
    # following_purpose is the canonical contract column (trips_stage.CONTRACT),
    # not the leftover "purpose" extra column; mirrors
    # test_run_escort_purpose_flag_produces_escort_trips above.
    assert "education" in set(table.loc[mask_13, "following_purpose"])
    assert not (table.loc[mask_13, "following_purpose"] == "escort").any()


def test_entd_source_rejects_escort_passive_education():
    from braunschweig.popsim.sources.entd import EntdSource
    # escort_purpose is left at its default False so this isolates the
    # escort_passive_education guard specifically (both guards are unconditional
    # and independent; escort_purpose=True would raise on that check first, as
    # test_entd_source_rejects_escort_purpose above already covers).
    with pytest.raises(NotImplementedError, match="escort_passive_education"):
        EntdSource().build_trips(
            pd.DataFrame({"person_id": []}), pd.DataFrame(), random_seed=1,
            escort_passive_education=True,
        )
