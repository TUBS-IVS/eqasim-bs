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
# _extract_locations vectorisation: byte-identity vs the previous per-row loop.
#
# _extract_locations was vectorised for performance (it consumes NO randomness,
# so it is a pure transform). ``_extract_locations_reference`` below is a verbatim
# copy of the previous per-row-loop implementation; the equivalence test asserts
# the new vectorised output is byte-identical to it across a representative result
# frame (several persons/problems, a skipped trailing fixed-anchor leg, an unknown
# candidate id -> "cs_" fallback, a NaN-coordinate skipped leg, a partially placed
# problem, an unknown problem_idx, and a malformed leg id).
# ---------------------------------------------------------------------------

def _extract_locations_reference(result_df, problem_meta, df_secondary, crs):
    """Verbatim previous per-row-loop implementation of ``_extract_locations``.

    Kept as the equivalence oracle for the vectorised version. Do NOT change the
    behaviour here -- it is intentionally the pre-refactor code.
    """
    coord_lookup = {
        str(lid): (float(g.x), float(g.y))
        for lid, g in zip(df_secondary["location_id"].astype(str),
                          df_secondary["geometry"])
    }
    meta_by_idx = {m["problem_idx"]: m for m in problem_meta}

    out_rows = []
    convergence_rows = []
    placed_per_prob = {}

    if "to_act_identifier" in result_df.columns:
        identifiers = result_df["to_act_identifier"]
    else:
        identifiers = [None] * len(result_df)
    for (uid, leg_id, to_act, to_x, to_y, cand) in zip(
        result_df["unique_person_id"],
        result_df["unique_leg_id"],
        result_df["to_act_type"],
        result_df["to_x"],
        result_df["to_y"],
        identifiers,
    ):
        try:
            person_str, prob_idx_str, leg_idx_str = leg_id.split("#")
        except ValueError:
            continue
        prob_idx = int(prob_idx_str)
        leg_idx = int(leg_idx_str)
        meta = meta_by_idx.get(prob_idx)
        if meta is None:
            continue
        if to_act not in sc.SECONDARY_PURPOSES:
            continue
        if pd.isna(to_x) or pd.isna(to_y):
            continue
        person_id = meta["person_id"]
        activity_index = meta["activity_index"] + leg_idx
        loc_id = cand if isinstance(cand, str) else None
        if loc_id is None or loc_id not in coord_lookup:
            loc_id = f"cs_{prob_idx}_{leg_idx}"
        out_rows.append((
            person_id, activity_index, loc_id, geo.Point(float(to_x), float(to_y))
        ))
        placed_per_prob[prob_idx] = placed_per_prob.get(prob_idx, 0) + 1

    for meta in problem_meta:
        n_expected = meta["n_secondary"]
        n_placed = placed_per_prob.get(meta["problem_idx"], 0)
        convergence_rows.append((n_placed == n_expected, n_expected))

    df_locations = pd.DataFrame.from_records(
        out_rows,
        columns=["person_id", "activity_index", "location_id", "geometry"],
    )
    df_locations = gpd.GeoDataFrame(df_locations, crs=crs)
    df_convergence = pd.DataFrame.from_records(
        convergence_rows, columns=["valid", "size"]
    )
    return df_locations, df_convergence


def _representative_extract_inputs():
    """A result frame + meta exercising every branch of _extract_locations.

    Persons/problems:
      * 7#0 : two secondary legs placed (shop, leisure) + trailing fixed home leg
              (skipped); both legs have known candidate ids -> canonical L1/L2.
      * 9#1 : two secondary legs, the second has NaN coordinates (skipped) ->
              partially placed problem (1 of 2).
      * 9#2 : one secondary leg with an UNKNOWN candidate id -> "cs_2_0" fallback.
      * 9#3 : one secondary leg whose candidate id is non-string (None) ->
              also falls back to a synthesised id.
      * 5#9 : one secondary leg for an UNKNOWN problem_idx (9 not in meta) ->
              the whole row is skipped.
      * a row with a malformed unique_leg_id ("badid", no '#') -> skipped.

    The interleaving (problem 2 emits before problem 1's second leg, etc.) and the
    trailing/skip rows make the result-frame row order non-trivial, so the test
    also locks the surviving-row order.
    """
    result_df = pd.DataFrame({
        "unique_person_id": [
            "7#0", "7#0", "7#0",
            "9#1", "9#1", "9#1",
            "9#2",
            "9#3",
            "5#9",
            "bad",
        ],
        "unique_leg_id": [
            "7#0#0", "7#0#1", "7#0#2",
            "9#1#0", "9#1#1", "9#1#2",
            "9#2#0",
            "9#3#0",
            "5#9#0",
            "badid",
        ],
        "to_act_type": [
            "shop", "leisure", "home",
            "shop", "leisure", "home",
            "other",
            "shop",
            "leisure",
            "shop",
        ],
        "to_x": [
            0.0, 10.0, 99.0,
            0.0, float("nan"), 99.0,
            10.0,
            0.0,
            10.0,
            0.0,
        ],
        "to_y": [
            0.0, 10.0, 99.0,
            0.0, float("nan"), 99.0,
            10.0,
            0.0,
            10.0,
            0.0,
        ],
        "to_act_identifier": [
            "L1", "L2", "Lhome",
            "L1", "L2", "Lhome",
            "UNKNOWN",
            None,
            "L2",
            "L1",
        ],
    })
    meta = [
        {"problem_idx": 0, "person_id": 7, "activity_index": 3, "n_secondary": 2},
        {"problem_idx": 1, "person_id": 9, "activity_index": 5, "n_secondary": 2},
        {"problem_idx": 2, "person_id": 9, "activity_index": 8, "n_secondary": 1},
        {"problem_idx": 3, "person_id": 9, "activity_index": 12, "n_secondary": 1},
    ]
    return result_df, meta


def _assert_locations_identical(df_a, df_b):
    """Assert two location GeoDataFrames are equal in rows, order, ids, dtypes
    and exact geometry."""
    assert list(df_a.columns) == list(df_b.columns)
    assert len(df_a) == len(df_b)
    # dtypes byte-identical (int64 person_id/activity_index, object location_id).
    for col in ("person_id", "activity_index", "location_id"):
        assert df_a[col].dtype == df_b[col].dtype, col
        assert list(df_a[col]) == list(df_b[col]), col
    # Exact geometry equality, in order.
    assert df_a.geometry.dtype == df_b.geometry.dtype
    for ga, gb in zip(df_a.geometry, df_b.geometry):
        assert ga.equals_exact(gb, tolerance=0.0)


def _assert_convergence_identical(df_a, df_b):
    assert list(df_a.columns) == list(df_b.columns)
    assert df_a["valid"].dtype == df_b["valid"].dtype
    assert df_a["size"].dtype == df_b["size"].dtype
    assert list(df_a["valid"]) == list(df_b["valid"])
    assert list(df_a["size"]) == list(df_b["size"])


def test_extract_locations_vectorised_matches_reference():
    result_df, meta = _representative_extract_inputs()

    ref_loc, ref_conv = _extract_locations_reference(
        result_df.copy(), meta, _df_secondary(), crs="EPSG:25832"
    )
    new_loc, new_conv = sc._extract_locations(
        result_df.copy(), meta, _df_secondary(), crs="EPSG:25832"
    )

    _assert_locations_identical(ref_loc, new_loc)
    _assert_convergence_identical(ref_conv, new_conv)

    # Spot-check the expected content so a refactor that breaks BOTH paths the
    # same way is still caught.
    # Surviving placements in result order: 7#0#0, 7#0#1, 9#1#0, 9#2#0, 9#3#0.
    assert list(new_loc["person_id"]) == [7, 7, 9, 9, 9]
    assert list(new_loc["activity_index"]) == [3, 4, 5, 8, 12]
    assert list(new_loc["location_id"]) == ["L1", "L2", "L1", "cs_2_0", "cs_3_0"]
    # Convergence in problem_meta order: prob 0 full (2/2), prob 1 partial (1/2),
    # prob 2 full (1/1), prob 3 full (1/1).
    assert list(new_conv["valid"]) == [True, False, True, True]
    assert list(new_conv["size"]) == [2, 2, 1, 1]


def test_extract_locations_vectorised_matches_reference_empty_result():
    # No result rows at all: both paths must yield the all-object empty locations
    # frame and a convergence frame with one (False, size) row per problem.
    empty = pd.DataFrame({
        "unique_person_id": pd.Series([], dtype=object),
        "unique_leg_id": pd.Series([], dtype=object),
        "to_act_type": pd.Series([], dtype=object),
        "to_x": pd.Series([], dtype=float),
        "to_y": pd.Series([], dtype=float),
        "to_act_identifier": pd.Series([], dtype=object),
    })
    meta = [
        {"problem_idx": 0, "person_id": 7, "activity_index": 3, "n_secondary": 2},
        {"problem_idx": 1, "person_id": 9, "activity_index": 5, "n_secondary": 0},
    ]

    ref_loc, ref_conv = _extract_locations_reference(
        empty.copy(), meta, _df_secondary(), crs="EPSG:25832"
    )
    new_loc, new_conv = sc._extract_locations(
        empty.copy(), meta, _df_secondary(), crs="EPSG:25832"
    )

    assert len(ref_loc) == 0 and len(new_loc) == 0
    assert list(ref_loc.columns) == list(new_loc.columns)
    for col in new_loc.columns:
        assert ref_loc[col].dtype == new_loc[col].dtype, col
    _assert_convergence_identical(ref_conv, new_conv)
    # n_secondary == 0 -> placed (0) equals expected (0) -> valid True.
    assert list(new_conv["valid"]) == [False, True]
    assert list(new_conv["size"]) == [2, 0]


def test_extract_locations_vectorised_matches_reference_all_skipped():
    # Every result row is skipped (all land on fixed anchors), so no placements
    # are produced even though result_df is non-empty -> exercises the
    # "no kept rows" branch. Output must equal the reference (empty locations).
    result_df = pd.DataFrame({
        "unique_person_id": ["7#0", "7#0"],
        "unique_leg_id": ["7#0#0", "7#0#1"],
        "to_act_type": ["home", "work"],
        "to_x": [1.0, 2.0],
        "to_y": [1.0, 2.0],
        "to_act_identifier": ["Lhome", "Lwork"],
    })
    meta = [{"problem_idx": 0, "person_id": 7, "activity_index": 3,
             "n_secondary": 0}]

    ref_loc, ref_conv = _extract_locations_reference(
        result_df.copy(), meta, _df_secondary(), crs="EPSG:25832"
    )
    new_loc, new_conv = sc._extract_locations(
        result_df.copy(), meta, _df_secondary(), crs="EPSG:25832"
    )

    assert len(new_loc) == 0
    assert list(ref_loc.columns) == list(new_loc.columns)
    for col in new_loc.columns:
        assert ref_loc[col].dtype == new_loc[col].dtype, col
    _assert_convergence_identical(ref_conv, new_conv)


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


# ---------------------------------------------------------------------------
# Fallback transparency: PRIMARY (carla) vs FALLBACK accounting.
#
# The PRIMARY method is the carla solver; unbounded chains plus the bounded
# problems carla raises on go to the FALLBACK. _fallback_accounting_summary is a
# pure counting helper (no chainsolvers import, no RNG, no I/O), so the
# primary-vs-fallback split can be asserted in isolation. The bulk of problems
# must be placed by the primary path; a high fallback share is flagged.
# ---------------------------------------------------------------------------

def test_fallback_accounting_primary_path_reports_zero_fallback():
    # A small problem set where carla solved everything (no unbounded chains, no
    # carla failures) -> the fallback count is exactly 0 and the primary share is
    # 100%. This is the "primary path places the bulk" guarantee in its purest
    # form: every problem is placed by carla.
    line = sc._fallback_accounting_summary(
        n_total_problems=10, n_unbounded=0, n_failed_bounded=0,
    )
    assert "primary (carla) placed 10/10 problems (100.0%)" in line
    assert "fallback placed 0/10 (0.0%)" in line
    # No WARNING prefix because the fallback share is below the threshold.
    assert "WARNING" not in line


def test_fallback_accounting_counts_unbounded_and_failed_as_fallback():
    # An unbounded problem (and a carla-failed bounded problem) must be counted
    # as fallback, and the primary count is the remainder. 8 of 10 placed by
    # carla (the bulk), 2 by the fallback (1 unbounded + 1 carla-failed).
    line = sc._fallback_accounting_summary(
        n_total_problems=10, n_unbounded=1, n_failed_bounded=1,
    )
    assert "primary (carla) placed 8/10 problems (80.0%)" in line
    assert "fallback placed 2/10 (20.0%)" in line
    assert "unbounded=1" in line and "carla-failed-bounded=1" in line
    # 20% fallback is AT the default threshold -> flagged.
    assert line.startswith("[braunschweig.secondary_chainsolvers] WARNING: ")


def test_fallback_accounting_warns_only_above_threshold():
    # Just below the default 20% threshold -> no warning; the carla primary path
    # still places the clear majority.
    below = sc._fallback_accounting_summary(
        n_total_problems=100, n_unbounded=19, n_failed_bounded=0,
    )
    assert "WARNING" not in below
    assert "primary (carla) placed 81/100 problems (81.0%)" in below

    # A high fallback share (carla effectively not working) -> warning prefix.
    high = sc._fallback_accounting_summary(
        n_total_problems=100, n_unbounded=40, n_failed_bounded=20,
    )
    assert high.startswith("[braunschweig.secondary_chainsolvers] WARNING: ")
    assert "fallback placed 60/100 (60.0%)" in high


def test_fallback_accounting_respects_custom_warning_share():
    # The threshold is configurable; a 10% fallback share is fine at the default
    # 20% threshold but flagged once the threshold is tightened to 5%.
    counts = dict(n_total_problems=100, n_unbounded=10, n_failed_bounded=0)
    assert "WARNING" not in sc._fallback_accounting_summary(**counts)
    strict = sc._fallback_accounting_summary(**counts, warning_share=0.05)
    assert strict.startswith("[braunschweig.secondary_chainsolvers] WARNING: ")


def test_fallback_accounting_handles_zero_problems():
    # Edge case: no problems at all. No division by zero; reported as 0/0 with a
    # 100% primary share (nothing to fall back on) and no warning.
    line = sc._fallback_accounting_summary(
        n_total_problems=0, n_unbounded=0, n_failed_bounded=0,
    )
    assert "primary (carla) placed 0/0 problems (100.0%)" in line
    assert "fallback placed 0/0 (0.0%)" in line
    assert "WARNING" not in line


# ---------------------------------------------------------------------------
# _build_plans_df columnar build: value-identical to the legacy list-of-dicts
# build (same loop structure -> same per-leg RNG draw order), incl. dtypes.
# ---------------------------------------------------------------------------

def _build_plans_df_reference(problems, distributions, leisure_correction_factor,
                              random):
    """Pre-columnar reference implementation of _build_plans_df (verbatim)."""
    rows = []
    problem_meta = []
    unbounded_idx = []

    for prob_idx, problem in enumerate(problems):
        if problem["origin"] is None or problem["destination"] is None:
            unbounded_idx.append(prob_idx)
            continue

        legs = sc._problem_legs(problem)
        n_legs = len(legs)
        person_id = problem["person_id"]
        problem_meta.append({
            "person_id": person_id,
            "problem_idx": prob_idx,
            "activity_index": problem["activity_index"],
            "n_secondary": problem["size"],
            "n_legs": n_legs,
        })

        origin_xy = (
            (float(problem["origin"][0, 0]), float(problem["origin"][0, 1]))
            if problem["origin"] is not None else (np.nan, np.nan)
        )
        dest_xy = (
            (float(problem["destination"][0, 0]),
             float(problem["destination"][0, 1]))
            if problem["destination"] is not None else (np.nan, np.nan)
        )

        for leg in legs:
            li = leg["leg_index"]
            to_act_type = leg["to_act_type"]
            distance_m = sc._sample_leg_distance(
                distributions, leg["mode"], leg["travel_time"],
                to_act_type if to_act_type in sc.SECONDARY_PURPOSES else "other",
                leisure_correction_factor, random,
            )
            if li == 0:
                from_x, from_y = origin_xy
            else:
                from_x, from_y = (np.nan, np.nan)
            if li == n_legs - 1 and dest_xy[0] == dest_xy[0]:
                to_x, to_y = dest_xy
            else:
                to_x, to_y = (np.nan, np.nan)
            rows.append({
                "unique_person_id": f"{person_id}#{prob_idx}",
                "unique_leg_id": f"{person_id}#{prob_idx}#{li}",
                "to_act_type": (
                    to_act_type if to_act_type != "__fixed__" else "home"
                ),
                "distance_meters": distance_m,
                "from_x": from_x,
                "from_y": from_y,
                "to_x": to_x,
                "to_y": to_y,
                "_leg_index": li,
                "_problem_idx": prob_idx,
            })

    return pd.DataFrame.from_records(rows), problem_meta, unbounded_idx


def _bounded_problems(n_problems=12, seed=11):
    """Bounded multi-leg problems (plus interleaved unbounded ones)."""
    rng = np.random.RandomState(seed)
    problems = []
    for i in range(n_problems):
        if i % 4 == 3:
            problems.append({
                "person_id": 1000 + i, "activity_index": 1, "size": 1,
                "purposes": ["other"], "modes": ["walk"],
                "travel_times": np.array([300.0]),
                "origin": None, "destination": None,
            })
            continue
        size = int(rng.randint(1, 4))
        purposes = [["shop", "leisure", "other"][rng.randint(3)] for _ in range(size)]
        modes = [["car", "pt", "walk", "bicycle"][rng.randint(4)] for _ in range(size + 1)]
        problems.append({
            "person_id": 1000 + i,
            "activity_index": int(rng.randint(0, 5)),
            "size": size,
            "purposes": purposes,
            "modes": modes,
            "travel_times": rng.uniform(120.0, 1200.0, size=size + 1),
            "origin": rng.uniform(0.0, 2000.0, size=(1, 2)),
            "destination": rng.uniform(0.0, 2000.0, size=(1, 2)),
        })
    return problems


def test_build_plans_df_columnar_matches_reference():
    problems = _bounded_problems()
    distributions = _flat_distribution()

    ref_df, ref_meta, ref_unbounded = _build_plans_df_reference(
        problems, distributions, 2.0, np.random.RandomState(5))
    new_df, new_meta, new_unbounded = sc._build_plans_df(
        problems, distributions, 2.0, np.random.RandomState(5))

    assert ref_meta == new_meta
    assert ref_unbounded == new_unbounded
    pd.testing.assert_frame_equal(new_df, ref_df)  # values, dtypes, order


def test_build_plans_df_empty_keeps_legacy_shape():
    # All problems unbounded -> legacy from_records([]) frame (no columns).
    problems = [p for p in _bounded_problems() if p["origin"] is None]
    new_df, meta, unbounded = sc._build_plans_df(
        problems, _flat_distribution(), 2.0, np.random.RandomState(0))
    assert len(new_df) == 0 and len(new_df.columns) == 0
    assert meta == [] and unbounded == list(range(len(problems)))


# ---------------------------------------------------------------------------
# _person_row_ranges: contiguous slices must reproduce the groupby sub-frames.
# ---------------------------------------------------------------------------

def test_person_row_ranges_match_groupby_subframes():
    problems = _bounded_problems()
    plans_df, _, _ = sc._build_plans_df(
        problems, _flat_distribution(), 2.0, np.random.RandomState(5))
    plans_for_cs = plans_df.drop(columns=["_leg_index", "_problem_idx"])
    unique_persons = plans_for_cs["unique_person_id"].drop_duplicates().to_list()

    ranges = sc._person_row_ranges(plans_for_cs)
    assert ranges is not None
    uid_order, starts, ends = ranges
    assert np.array_equal(uid_order, np.asarray(unique_persons, dtype=object))

    by_person = dict(tuple(plans_for_cs.groupby("unique_person_id", sort=False)))
    for i, uid in enumerate(unique_persons):
        sliced = plans_for_cs.iloc[starts[i]:ends[i]]
        pd.testing.assert_frame_equal(sliced, by_person[uid])

    # A chunk of consecutive persons == the legacy per-person concat.
    chunk_uids = unique_persons[1:4]
    legacy_chunk = pd.concat([by_person[u] for u in chunk_uids], ignore_index=True)
    sliced_chunk = plans_for_cs.iloc[starts[1]:ends[3]].reset_index(drop=True)
    pd.testing.assert_frame_equal(sliced_chunk, legacy_chunk)


def test_person_row_ranges_rejects_non_contiguous_rows():
    df = pd.DataFrame({"unique_person_id": ["a", "a", "b", "a"], "x": [1, 2, 3, 4]})
    assert sc._person_row_ranges(df) is None
