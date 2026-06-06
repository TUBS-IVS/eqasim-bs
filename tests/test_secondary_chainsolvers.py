"""Unit tests for the chainsolvers (carla) secondary-location stage helpers.

These tests exercise the pure result-extraction helper ``_extract_locations``
without importing the optional ``chainsolvers`` package (which is imported
lazily inside ``execute``). They lock the behaviour of the carla -> eqasim
output conversion: every secondary leg is placed exactly once, the canonical
eqasim ``location_id`` is recovered from the solver's ``to_act_identifier``
column, and the trailing fixed-anchor leg is skipped.
"""
from __future__ import annotations

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely import geometry as geo

from braunschweig.synthesis.locations import secondary_chainsolvers as sc


def _df_secondary():
    return gpd.GeoDataFrame(
        {"location_id": ["L1", "L2"]},
        geometry=[geo.Point(0.0, 0.0), geo.Point(10.0, 10.0)],
        crs="EPSG:25832",
    )


def _result_df():
    # One person (person_id 7), one bounded problem (problem_idx 0) with two
    # secondary legs (shop, leisure) and a trailing leg that lands on the
    # fixed home anchor (must be skipped by _extract_locations).
    return pd.DataFrame({
        "unique_person_id": ["7#0", "7#0", "7#0"],
        "unique_leg_id": ["7#0#0", "7#0#1", "7#0#2"],
        "to_act_type": ["shop", "leisure", "home"],
        "to_x": [0.0, 10.0, 99.0],
        "to_y": [0.0, 10.0, 99.0],
        "to_act_identifier": ["L1", "L2", "Lhome"],
    })


def test_extract_locations_recovers_canonical_ids_and_skips_anchor():
    meta = [{"problem_idx": 0, "person_id": 7, "activity_index": 3,
             "n_secondary": 2}]

    df_loc, df_conv = sc._extract_locations(
        _result_df(), meta, _df_secondary(), crs="EPSG:25832"
    )

    # The trailing home leg is skipped: exactly the two secondary legs remain.
    assert list(df_loc["person_id"]) == [7, 7]
    assert list(df_loc["activity_index"]) == [3, 4]
    # Canonical eqasim location_id recovered from to_act_identifier (not a
    # synthesised "cs_*" placeholder).
    assert list(df_loc["location_id"]) == ["L1", "L2"]
    assert df_loc.iloc[0].geometry.equals(geo.Point(0.0, 0.0))
    assert df_loc.iloc[1].geometry.equals(geo.Point(10.0, 10.0))
    # Both secondary legs placed -> problem converged.
    assert list(df_conv["valid"]) == [True]
    assert list(df_conv["size"]) == [2]


def test_extract_locations_convergence_per_problem_partial_placement():
    # Two bounded problems. Problem 0 (person 7): both secondary legs placed ->
    # converged. Problem 1 (person 9): one secondary leg has NaN coords (solver
    # failed to place it) -> only 1 of 2 placed -> NOT converged. This locks the
    # per-problem convergence accounting so the O(n) computation matches the old
    # O(problems x placements) scan, including the problem_meta output order.
    rdf = pd.DataFrame({
        "unique_person_id": ["7#0", "7#0", "7#0", "9#1", "9#1", "9#1"],
        "unique_leg_id": ["7#0#0", "7#0#1", "7#0#2", "9#1#0", "9#1#1", "9#1#2"],
        "to_act_type": ["shop", "leisure", "home", "shop", "leisure", "home"],
        "to_x": [0.0, 10.0, 99.0, 0.0, float("nan"), 99.0],
        "to_y": [0.0, 10.0, 99.0, 0.0, float("nan"), 99.0],
        "to_act_identifier": ["L1", "L2", "Lhome", "L1", "L2", "Lhome"],
    })
    meta = [
        {"problem_idx": 0, "person_id": 7, "activity_index": 3, "n_secondary": 2},
        {"problem_idx": 1, "person_id": 9, "activity_index": 5, "n_secondary": 2},
    ]

    df_loc, df_conv = sc._extract_locations(
        rdf, meta, _df_secondary(), crs="EPSG:25832"
    )

    # Person 7: two placed (activity_index 3, 4). Person 9: only the first leg
    # placed (activity_index 5); the NaN-coord leg is skipped.
    assert list(df_loc["person_id"]) == [7, 7, 9]
    assert list(df_loc["activity_index"]) == [3, 4, 5]
    # Convergence in problem_meta order: problem 0 fully placed, problem 1 not.
    assert list(df_conv["valid"]) == [True, False]
    assert list(df_conv["size"]) == [2, 2]


def test_extract_locations_synthesises_id_when_identifier_unknown():
    # An identifier that is not in the facility coord lookup must fall back to
    # a synthesised id while still emitting the placed geometry.
    rdf = _result_df()
    rdf.loc[0, "to_act_identifier"] = "UNKNOWN"

    df_loc, _ = sc._extract_locations(
        rdf, [{"problem_idx": 0, "person_id": 7, "activity_index": 3,
               "n_secondary": 2}],
        _df_secondary(), crs="EPSG:25832",
    )

    assert df_loc.iloc[0]["location_id"] == "cs_0_0"
    assert df_loc.iloc[1]["location_id"] == "L2"


# ---------------------------------------------------------------------------
# RDA fallback equivalence (shared prebuilt CandidateIndex).
#
# These tests cover the performance refactor that builds the eqasim
# CandidateIndex (3 KDTrees over the secondary candidate set) ONCE and reuses
# it across both fallback calls (unbounded chains + failed-bounded problems).
# They lock the two output-identity guarantees:
#   1. the fallback is deterministic for a given seed, and
#   2. reusing one shared index produces byte-identical placements to building
#      a fresh index for every call (the pre-refactor behaviour) -- proving the
#      shared index changes no drawn result and no candidate ordering.
# The RDA solver pipeline lives in the repo (synthesis.population.spatial.
# secondary.{rda,components}) and needs neither the optional chainsolvers
# package nor any external data.
# ---------------------------------------------------------------------------

def _df_secondary_fallback():
    """A small candidate set where every facility offers all three purposes,
    spread out so KDTree queries are well-defined."""
    points = [
        geo.Point(0.0, 0.0),
        geo.Point(1000.0, 0.0),
        geo.Point(0.0, 1000.0),
        geo.Point(1000.0, 1000.0),
        geo.Point(500.0, 500.0),
        geo.Point(2000.0, 2000.0),
    ]
    return gpd.GeoDataFrame(
        {
            "location_id": [f"F{i}" for i in range(len(points))],
            "offers_shop": [True] * len(points),
            "offers_leisure": [True] * len(points),
            "offers_other": [True] * len(points),
        },
        geometry=points,
        crs="EPSG:25832",
    )


def _flat_distribution():
    """Minimal mode-conditional distribution structure consumed by the
    CustomDistanceSampler / _sample_leg_distance: one bound bucket whose CDF is
    a uniform step over a few candidate distance values (metres)."""
    values = np.array([800.0, 1000.0, 1200.0, 1500.0])
    cdf = np.array([0.25, 0.5, 0.75, 1.0])
    return {
        mode: {
            "bounds": np.array([], dtype=float),
            "distributions": [{"values": values.copy(), "cdf": cdf.copy()}],
        }
        for mode in ("car", "car_passenger", "pt", "bicycle", "walk")
    }


def _fallback_problems():
    """A mixed problem set exercising every relaxation branch:

    * problem 0: bounded chain (origin + destination) with two secondary legs;
    * problem 1: right tail (origin fixed, destination None) with one leg;
    * problem 2: free chain (both anchors None) with one leg.
    """
    return [
        {
            # Bounded chain with two secondary stops -> three legs
            # (origin -> shop -> leisure -> destination): modes / travel_times
            # have length size + 1.
            "person_id": 100,
            "activity_index": 2,
            "size": 2,
            "purposes": ["shop", "leisure"],
            "modes": ["car", "bicycle", "car"],
            "travel_times": np.array([600.0, 300.0, 600.0]),
            "origin": np.array([[0.0, 0.0]]),
            "destination": np.array([[1000.0, 1000.0]]),
        },
        {
            "person_id": 200,
            "activity_index": 1,
            "size": 1,
            "purposes": ["other"],
            "modes": ["walk"],
            "travel_times": np.array([400.0]),
            "origin": np.array([[500.0, 500.0]]),
            "destination": None,
        },
        {
            "person_id": 300,
            "activity_index": 0,
            "size": 1,
            "purposes": ["leisure"],
            "modes": ["pt"],
            "travel_times": np.array([900.0]),
            "origin": None,
            "destination": None,
        },
    ]


def _run_rda_fallback_shared(seed):
    """Run BOTH fallback calls (unbounded then bounded) through a single shared,
    prebuilt CandidateIndex -- mirrors the refactored execute() flow."""
    problems = _fallback_problems()
    distributions = _flat_distribution()
    df_secondary = _df_secondary_fallback()
    random = np.random.RandomState(seed)

    index = sc._build_rda_candidate_index(df_secondary)

    unbounded = [1, 2]  # right tail + free chain
    bounded_failed = [0]  # the bounded chain, routed through the same index

    rows_a, conv_a = sc._rda_fallback_place(
        problems, unbounded, index, distributions, 2.0, random, "EPSG:25832",
    )
    rows_b, conv_b = sc._rda_fallback_place(
        problems, bounded_failed, index, distributions, 2.0, random, "EPSG:25832",
    )
    return rows_a + rows_b, conv_a + conv_b


def _run_rda_fallback_fresh_index(seed):
    """Pre-refactor behaviour: build a FRESH CandidateIndex for every fallback
    call. Used to prove the shared index produces identical results."""
    problems = _fallback_problems()
    distributions = _flat_distribution()
    df_secondary = _df_secondary_fallback()
    random = np.random.RandomState(seed)

    unbounded = [1, 2]
    bounded_failed = [0]

    rows_a, conv_a = sc._rda_fallback_place(
        problems, unbounded, sc._build_rda_candidate_index(df_secondary),
        distributions, 2.0, random, "EPSG:25832",
    )
    rows_b, conv_b = sc._rda_fallback_place(
        problems, bounded_failed, sc._build_rda_candidate_index(df_secondary),
        distributions, 2.0, random, "EPSG:25832",
    )
    return rows_a + rows_b, conv_a + conv_b


def _rows_to_comparable(rows):
    """Reduce placement tuples (person_id, activity_index, location_id, Point)
    to a hashable form with exact coordinates for equality assertions."""
    return [
        (pid, aidx, lid, (round(pt.x, 9), round(pt.y, 9)))
        for (pid, aidx, lid, pt) in rows
    ]


def test_rda_fallback_is_deterministic_across_runs_with_same_seed():
    rows1, conv1 = _run_rda_fallback_shared(seed=4242)
    rows2, conv2 = _run_rda_fallback_shared(seed=4242)

    assert _rows_to_comparable(rows1) == _rows_to_comparable(rows2)
    assert conv1 == conv2
    # Sanity: the fallback actually placed something (3 problems, 4 legs total).
    assert len(rows1) == 4


def test_rda_fallback_shared_index_matches_fresh_index_per_call():
    # The performance refactor reuses one prebuilt CandidateIndex across both
    # fallback calls. With the SAME seed this must be byte-identical to the
    # pre-refactor path that builds a fresh index for every call -- the index
    # consumes no randomness, so sharing it changes neither the drawn results
    # nor the candidate ordering.
    rows_shared, conv_shared = _run_rda_fallback_shared(seed=99)
    rows_fresh, conv_fresh = _run_rda_fallback_fresh_index(seed=99)

    assert _rows_to_comparable(rows_shared) == _rows_to_comparable(rows_fresh)
    assert conv_shared == conv_fresh


def test_build_rda_candidate_index_preserves_candidate_order():
    # The vectorised coordinate access (column_stack of GeoSeries.x/.y) must
    # preserve the exact df_secondary row order, so KDTree queries / samples
    # map to the same candidates as the previous per-geometry apply().
    df_secondary = _df_secondary_fallback()
    index = sc._build_rda_candidate_index(df_secondary)

    for purpose in ("shop", "leisure", "other"):
        ids = list(index.data[purpose]["identifiers"])
        coords = index.data[purpose]["locations"]
        # All candidates offer every purpose, so each per-purpose set equals the
        # full candidate set in original order.
        assert ids == list(df_secondary["location_id"].values)
        expected = np.column_stack((
            df_secondary.geometry.x.values, df_secondary.geometry.y.values,
        ))
        assert np.array_equal(coords, expected)


# ---------------------------------------------------------------------------
# FIX 2.3: _resample_distributions must not mutate the (synpp-cached) input.
# ---------------------------------------------------------------------------

# Non-zero resample factors for every mode so the operation actually changes
# the CDFs (a zero factor leaves _resample_cdf a no-op).
_RESAMPLE_FACTORS = dict(car=0.0, car_passenger=0.1, pt=0.5, bicycle=0.0, walk=-0.5)


def test_resample_distributions_does_not_mutate_input():
    """The cached ``distance_distributions`` object is shared with the legacy
    locations stage, so ``_resample_distributions`` must return a resampled deep
    copy and leave the original CDF arrays untouched."""
    original = _flat_distribution()
    # Snapshot the original CDF arrays (deep) before resampling.
    original_cdfs = {
        mode: [d["cdf"].copy() for d in original[mode]["distributions"]]
        for mode in original
    }

    resampled = sc._resample_distributions(original, _RESAMPLE_FACTORS)

    # The original object's CDF arrays are byte-identical to the snapshot.
    for mode in original:
        for d, snapshot in zip(original[mode]["distributions"], original_cdfs[mode]):
            assert np.array_equal(d["cdf"], snapshot), (
                f"input CDF for mode {mode} was mutated in place"
            )

    # The returned copy is a distinct object whose CDFs differ for the modes
    # with a non-zero factor (pt, walk), proving the resample was applied to the
    # copy rather than the input.
    assert resampled is not original
    for mode in ("pt", "walk"):
        for d_in, d_out in zip(original[mode]["distributions"],
                               resampled[mode]["distributions"]):
            assert d_out["cdf"] is not d_in["cdf"]
            assert not np.array_equal(d_out["cdf"], d_in["cdf"])
    # Zero-factor modes resample to the (normalised) same values but on a copy.
    for mode in ("car", "bicycle"):
        for d_in, d_out in zip(original[mode]["distributions"],
                               resampled[mode]["distributions"]):
            assert d_out["cdf"] is not d_in["cdf"]
            assert np.allclose(d_out["cdf"], d_in["cdf"])


def test_resample_distributions_is_not_compounded_via_cached_object():
    """Calling ``_resample_distributions`` twice on the SAME cached object must
    yield the SAME result both times (no compounding of the resample factors),
    because each call resamples a fresh copy of the untouched input."""
    cached = _flat_distribution()

    first = sc._resample_distributions(cached, _RESAMPLE_FACTORS)
    second = sc._resample_distributions(cached, _RESAMPLE_FACTORS)

    for mode in cached:
        for d_first, d_second in zip(first[mode]["distributions"],
                                     second[mode]["distributions"]):
            assert np.array_equal(d_first["cdf"], d_second["cdf"]), (
                f"resampling the cached object twice compounded mode {mode}"
            )
