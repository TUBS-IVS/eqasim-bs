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


def _purpose_layered_distribution():
    """Purpose-layered distribution structure (Tier 1): ``{purpose: {mode: ...}}``.

    Mirrors the structure produced by ``braunschweig.popsim.distance_distributions``
    when ``secondary_shop_daily_split`` adds ``shop_daily`` / ``shop_non_daily``
    layers.  Each purpose maps to the same per-mode dict structure as the legacy
    ``_flat_distribution``, so the CDF arrays are the same shape -- the only
    difference is the extra nesting level.
    """
    return {
        "shop_daily": _flat_distribution(),
        "shop_non_daily": _flat_distribution(),
        "shop": _flat_distribution(),
        "leisure": _flat_distribution(),
        "other": _flat_distribution(),
    }


def test_resample_distributions_handles_purpose_layered_structure():
    """``_resample_distributions`` must handle the purpose-layered structure
    ``{purpose: {mode: {bounds, distributions}}}`` (Tier 1 ON) without crashing
    and must apply the per-mode CDF resample factors within each purpose layer.

    Regression guard for C-1: before the fix, ``mode_distributions["distributions"]``
    raised ``KeyError`` on a purpose-level dict whose top-level key does not have a
    ``"distributions"`` sub-key, so Tier1+Tier2 BOTH ON hard-crashed in execute().
    """
    layered = _purpose_layered_distribution()
    # Snapshot every CDF so we can verify the resample was actually applied.
    original_cdfs = {
        purpose: {
            mode: [d["cdf"].copy() for d in layered[purpose][mode]["distributions"]]
            for mode in layered[purpose]
        }
        for purpose in layered
    }

    resampled = sc._resample_distributions(layered, _RESAMPLE_FACTORS)

    # Input must be untouched (deep-copy contract).
    for purpose in layered:
        for mode in layered[purpose]:
            for d, snapshot in zip(layered[purpose][mode]["distributions"],
                                   original_cdfs[purpose][mode]):
                assert np.array_equal(d["cdf"], snapshot), (
                    f"input CDF mutated: purpose={purpose}, mode={mode}"
                )

    # The returned copy must be a distinct object.
    assert resampled is not layered

    # For modes with a non-zero factor, the resampled CDF must differ from the
    # original in every purpose layer (proving the resample was actually applied).
    for purpose in resampled:
        for mode in ("pt", "walk"):  # non-zero factors: pt=0.5, walk=-0.5
            for d_in, d_out in zip(layered[purpose][mode]["distributions"],
                                   resampled[purpose][mode]["distributions"]):
                assert d_out["cdf"] is not d_in["cdf"]
                assert not np.array_equal(d_out["cdf"], d_in["cdf"]), (
                    f"CDF unchanged after resample: purpose={purpose}, mode={mode}"
                )

    # Zero-factor modes (car, bicycle) resample to normalised-same values on a copy.
    for purpose in resampled:
        for mode in ("car", "bicycle"):
            for d_in, d_out in zip(layered[purpose][mode]["distributions"],
                                   resampled[purpose][mode]["distributions"]):
                assert d_out["cdf"] is not d_in["cdf"]
                assert np.allclose(d_out["cdf"], d_in["cdf"])


def test_resample_distributions_legacy_structure_unchanged():
    """The legacy per-mode structure (no purpose layer) still works correctly
    after the C-1 fix (regression guard for the existing path)."""
    original = _flat_distribution()
    original_cdfs = {
        mode: [d["cdf"].copy() for d in original[mode]["distributions"]]
        for mode in original
    }
    resampled = sc._resample_distributions(original, _RESAMPLE_FACTORS)

    # Input untouched.
    for mode in original:
        for d, snapshot in zip(original[mode]["distributions"], original_cdfs[mode]):
            assert np.array_equal(d["cdf"], snapshot)

    # Non-zero-factor modes changed; zero-factor modes produce a normalised copy.
    for mode in ("pt", "walk"):
        for d_in, d_out in zip(original[mode]["distributions"],
                               resampled[mode]["distributions"]):
            assert not np.array_equal(d_out["cdf"], d_in["cdf"])
    for mode in ("car", "bicycle"):
        for d_in, d_out in zip(original[mode]["distributions"],
                               resampled[mode]["distributions"]):
            assert np.allclose(d_out["cdf"], d_in["cdf"])


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
    # OFF path (shop_subtype_decider=None): the 4th return is the subtype stats
    # (all zero) and the frame must stay value-identical to the legacy build.
    new_df, new_meta, new_unbounded, subtype_stats = sc._build_plans_df(
        problems, distributions, 2.0, np.random.RandomState(5))

    assert ref_meta == new_meta
    assert ref_unbounded == new_unbounded
    pd.testing.assert_frame_equal(new_df, ref_df)  # values, dtypes, order
    # OFF path (shop_subtype_decider=None): subtype_stats is {} (empty dict,
    # not allocated on the OFF path so the gate stays consistent with M-1).
    assert subtype_stats == {}


def test_build_plans_df_empty_keeps_legacy_shape():
    # All problems unbounded -> legacy from_records([]) frame (no columns).
    problems = [p for p in _bounded_problems() if p["origin"] is None]
    new_df, meta, unbounded, _subtype_stats = sc._build_plans_df(
        problems, _flat_distribution(), 2.0, np.random.RandomState(0))
    assert len(new_df) == 0 and len(new_df.columns) == 0
    assert meta == [] and unbounded == list(range(len(problems)))


# ---------------------------------------------------------------------------
# _person_row_ranges: contiguous slices must reproduce the groupby sub-frames.
# ---------------------------------------------------------------------------

def test_person_row_ranges_match_groupby_subframes():
    problems = _bounded_problems()
    plans_df, _, _, _ = sc._build_plans_df(
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


# ---------------------------------------------------------------------------
# Tier 2: daily / non-daily shop subtype (distance + retail_daily/non_daily
# placement). The OFF path is byte-identical; the ON path tags shop legs with
# an internal subtype that drives BOTH the distance layer and the building
# placement, while the eqasim output purpose stays "shop".
# ---------------------------------------------------------------------------

import pytest


def _split_candidates():
    """Candidate frame carrying the split retail potentials (Tier 2)."""
    return gpd.GeoDataFrame(
        {
            "location_id": ["sec_0", "sec_1", "sec_2"],
            "offers_shop": [True, True, False],
            "offers_leisure": [False, True, False],
            "offers_other": [False, False, True],
            "pot_shop": [10.0, 5.0, 0.0],
            "pot_shop_daily": [10.0, 0.0, 0.0],      # sec_0 daily-only
            "pot_shop_non_daily": [0.0, 5.0, 0.0],   # sec_1 non-daily-only
            "pot_leisure": [0.0, 3.0, 0.0],
            "pot_other": [0.0, 0.0, 7.0],
        },
        geometry=[geo.Point(0, 0), geo.Point(100, 100), geo.Point(200, 200)],
        crs="EPSG:25832",
    )


def test_build_locations_df_shop_subtype_split_emits_subtype_activities():
    out = sc._build_locations_df(
        _split_candidates(), with_potentials=True, shop_daily_split=True)
    # sec_0 offers shop with daily potential only -> shop_daily (non_daily
    # dropped because its potential is 0).
    assert out.loc[0, "activities"] == "shop_daily"
    assert out.loc[0, "potentials"] == "10.0"
    # sec_1 offers shop with non-daily potential only -> shop_non_daily, plus
    # leisure.
    assert out.loc[1, "activities"] == "shop_non_daily; leisure"
    assert out.loc[1, "potentials"] == "5.0; 3.0"
    # sec_2 offers only other.
    assert out.loc[2, "activities"] == "other"
    assert out.loc[2, "potentials"] == "7.0"
    # The aggregate shop activity never appears on the split path.
    assert not any(a.startswith("shop;") or a == "shop" for a in out["activities"])


def test_build_locations_df_off_path_byte_identical_with_split_columns():
    # A candidate frame that ALSO carries the split columns must, on the
    # non-split path, still produce the legacy shop activity at pot_shop.
    out = sc._build_locations_df(
        _split_candidates(), with_potentials=True, shop_daily_split=False)
    assert out.loc[0, "activities"] == "shop"
    assert out.loc[0, "potentials"] == "10.0"
    assert out.loc[1, "activities"] == "shop; leisure"
    assert out.loc[1, "potentials"] == "5.0; 3.0"


def test_build_locations_df_split_requires_potentials():
    with pytest.raises(ValueError, match="requires with_potentials"):
        sc._build_locations_df(
            _split_candidates(), with_potentials=False, shop_daily_split=True)


def test_build_secondary_candidates_carries_split_retail_columns():
    legacy = gpd.GeoDataFrame(
        {"location_id": ["sec_0"], "commune_id": ["03101000"],
         "iris_id": ["03101000"], "offers_shop": [True],
         "offers_leisure": [True], "offers_other": [True]},
        geometry=[geo.Point(500, 500)], crs="EPSG:25832",
    )
    from shapely.geometry import Polygon
    b = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])
    buildings = gpd.GeoDataFrame(
        {"building_id": [7],
         "potential_retail_daily": [4.0], "potential_retail_non_daily": [3.0],
         "potential_leisure": [9.0], "potential_generic": [100.0],
         "commune_id": ["03101000"]},
        geometry=[b], crs="EPSG:25832",
    )
    out = sc.build_secondary_candidates(legacy, buildings)
    gpkg = out[out["location_id"] == "sec_b_7"].iloc[0]
    # Summed pot_shop preserved (OFF path), components carried separately.
    assert gpkg["pot_shop"] == 7.0
    assert gpkg["pot_shop_daily"] == 4.0
    assert gpkg["pot_shop_non_daily"] == 3.0
    # Legacy other row carries 0.0 for all three shop potentials.
    other = out[out["location_id"] == "sec_0"].iloc[0]
    assert other["pot_shop_daily"] == 0.0 and other["pot_shop_non_daily"] == 0.0


def test_purpose_in_distributions_detects_layer_and_legacy():
    # Legacy per-mode structure -> no purpose sub-keying.
    assert not sc._purpose_in_distributions(_flat_distribution(), "shop_daily")
    # Purpose-layered structure with the subtype present.
    layered = {"shop_daily": _flat_distribution(), "shop": _flat_distribution()}
    assert sc._purpose_in_distributions(layered, "shop_daily")
    # Purpose-layered structure missing the subtype.
    assert not sc._purpose_in_distributions(layered, "shop_non_daily")
    assert not sc._purpose_in_distributions({}, "shop_daily")


def _shop_problem():
    """One bounded problem, single shop leg between two fixed anchors."""
    return [{
        "person_id": 100, "activity_index": 2, "size": 1,
        "purposes": ["shop"], "modes": ["car", "car"],
        "travel_times": np.array([600.0, 600.0]),
        "origin": np.array([[0.0, 0.0]]),
        "destination": np.array([[1000.0, 1000.0]]),
    }]


def test_build_plans_df_subtype_decider_tags_shop_legs_and_uses_subtype_distance():
    # A decider that always returns shop_daily; distributions carry a shop_daily
    # layer, so the distance purpose is shop_daily (no fallback) and the plan's
    # to_act_type becomes shop_daily.
    layered = {"shop_daily": _flat_distribution(),
               "shop": _flat_distribution(),
               "leisure": _flat_distribution(),
               "other": _flat_distribution()}
    df, meta, unbounded, stats = sc._build_plans_df(
        _shop_problem(), layered, 2.0, np.random.RandomState(1),
        shop_subtype_decider=lambda mode, tt: "shop_daily",
    )
    shop_rows = df[df["to_act_type"] == "shop_daily"]
    assert len(shop_rows) == 1                      # the shop leg is tagged
    assert stats["shop_daily"] == 1 and stats["shop_non_daily"] == 0
    assert stats["distance_layer_fallback"] == 0    # shop_daily layer present


def test_build_plans_df_subtype_distance_layer_fallback_counted():
    # The subtype layer is ABSENT -> the distance falls back to the aggregate
    # shop layer and the fallback is counted; the placement activity still
    # carries the subtype.
    layered = {"shop": _flat_distribution(),
               "leisure": _flat_distribution(),
               "other": _flat_distribution()}
    df, meta, unbounded, stats = sc._build_plans_df(
        _shop_problem(), layered, 2.0, np.random.RandomState(1),
        shop_subtype_decider=lambda mode, tt: "shop_non_daily",
    )
    assert (df["to_act_type"] == "shop_non_daily").sum() == 1
    assert stats["shop_non_daily"] == 1
    assert stats["distance_layer_fallback"] == 1


def test_extract_locations_maps_shop_subtypes_back_to_shop():
    # A solver result carrying the internal subtype activities must NOT be
    # dropped at extraction (they are secondary, mapped implicitly to shop).
    rdf = pd.DataFrame({
        "unique_person_id": ["7#0", "7#0"],
        "unique_leg_id": ["7#0#0", "7#0#1"],
        "to_act_type": ["shop_daily", "shop_non_daily"],
        "to_x": [0.0, 10.0],
        "to_y": [0.0, 10.0],
        "to_act_identifier": ["L1", "L2"],
    })
    meta = [{"problem_idx": 0, "person_id": 7, "activity_index": 3,
             "n_secondary": 2}]
    df_loc, df_conv = sc._extract_locations(
        rdf, meta, _df_secondary(), crs="EPSG:25832")
    # Both subtype legs survive -> placed; the output schema carries no purpose.
    assert list(df_loc["person_id"]) == [7, 7]
    assert "to_act_type" not in df_loc.columns
    assert list(df_conv["valid"]) == [True]


def test_carla_accepts_shop_subtype_activities_smoke():
    # End-to-end smoke: carla must accept the internal shop_daily / shop_non_daily
    # activities + their potential columns and place a subtype-tagged leg at the
    # matching subtype building (no KeyError on the unknown activity name).
    cs = pytest.importorskip("chainsolvers")
    locations_df = sc._build_locations_df(
        _split_candidates(), with_potentials=True, shop_daily_split=True)
    # plans: one daily shop leg between two anchors.
    layered = {"shop_daily": _flat_distribution(),
               "shop": _flat_distribution(),
               "leisure": _flat_distribution(),
               "other": _flat_distribution()}
    plans_df, meta, unbounded, stats = sc._build_plans_df(
        _shop_problem(), layered, 2.0, np.random.RandomState(3),
        shop_subtype_decider=lambda mode, tt: "shop_daily",
    )
    plans_for_cs = plans_df.drop(columns=["_leg_index", "_problem_idx"])
    ctx = cs.setup(locations_df=locations_df, solver="carla", rng_seed=7)
    res_df, _seg, _v = cs.solve(ctx=ctx, plans_df=plans_for_cs)
    # The shop_daily leg was placed at the daily-only building sec_0.
    placed = res_df[res_df["to_act_type"] == "shop_daily"]
    assert len(placed) == 1
    assert placed.iloc[0]["to_act_identifier"] == "sec_0"


def test_rda_sample_distances_purpose_resolved_layout_no_keyerror():
    """The rda fallback's distance sampler must handle the Tier-1 purpose-resolved
    layout {purpose: {mode: ...}} -- indexing it by mode (the stock sampler) raised
    KeyError and dropped the long-distance / unbounded chains, crashing downstream."""
    import numpy as np
    from braunschweig.synthesis.locations.secondary_chainsolvers import _rda_sample_distances

    def cell(value):
        return {"bounds": np.array([np.inf]),
                "distributions": [{"values": np.array([value]), "cdf": np.array([1.0])}]}

    # Purpose-resolved: leisure/car -> 5000, other/car -> 9000.
    distributions = {"leisure": {"car": cell(5000.0)}, "other": {"car": cell(9000.0)}}
    rng = np.random.RandomState(0)

    d = _rda_sample_distances(
        distributions,
        {"modes": ["car"], "travel_times": [600.0], "purposes": ["leisure"]},
        1.0, rng)
    assert d.shape == (1,)
    assert d[0] == 5000.0  # indexed [leisure][car] -- no KeyError on the mode

    # Legacy {mode: ...} layout stays byte-identical (auto-detected).
    legacy = {"car": cell(7000.0)}
    d2 = _rda_sample_distances(
        legacy,
        {"modes": ["car"], "travel_times": [600.0], "purposes": ["other"]},
        1.0, rng)
    assert d2[0] == 7000.0


def test_build_secondary_candidates_appends_external_centroids():
    import geopandas as gpd
    import pandas as pd
    from shapely.geometry import Point, Polygon
    import braunschweig.synthesis.locations.secondary_chainsolvers as sc

    # Minimal legacy candidate (one 'other' catalog point) ...
    legacy = gpd.GeoDataFrame({
        "location_id": ["sec_0"], "commune_id": ["03101000"],
        "iris_id": ["0310100000000000"],
        "offers_leisure": [False], "offers_shop": [False], "offers_other": [True],
        "geometry": [Point(600000, 5790000)],
    }, crs="EPSG:25832")
    # ... and one gpkg building with retail potential.
    buildings = gpd.GeoDataFrame({
        "building_id": [1], "commune_id": ["03101000"],
        "potential_retail_daily": [2.0], "potential_retail_non_daily": [1.0],
        "potential_leisure": [0.0], "potential_generic": [0.0],
        "geometry": [Polygon([(600000, 5790000), (600010, 5790000),
                              (600010, 5790010), (600000, 5790010)])],
    }, crs="EPSG:25832")
    external = gpd.GeoDataFrame({
        "commune_id": ["EXT05111000"], "ars5": ["05111"], "gem_ags": ["05111000"],
        "ewz": [600000.0], "geometry": [Point(550000, 5800000)],
    }, crs="EPSG:25832")

    out = sc.build_secondary_candidates(legacy, buildings, df_external=external)

    ext_rows = out[out["location_id"] == "EXT05111000"]
    assert len(ext_rows) == 1
    r = ext_rows.iloc[0]
    assert bool(r["offers_shop"]) and bool(r["offers_leisure"]) and bool(r["offers_other"])
    assert r["pot_leisure"] == 600000.0 and r["pot_other"] == 600000.0
    assert r["pot_shop"] == 600000.0

    # df_external=None -> byte-identical to the no-external result.
    out_none = sc.build_secondary_candidates(legacy, buildings, df_external=None)
    out_default = sc.build_secondary_candidates(legacy, buildings)
    assert list(out_none["location_id"]) == list(out_default["location_id"])
    assert "EXT05111000" not in set(out_none["location_id"])


# ---------------------------------------------------------------------------
# Task 5: build_scorer attr_transform wiring
# ---------------------------------------------------------------------------

def test_build_scorer_passes_attr_transform():
    """build_scorer forwards attr_transform to the chainsolvers Scorer."""
    from braunschweig.synthesis.locations.secondary_chainsolvers import build_scorer
    s = build_scorer(True, "combined", 1.0, 1.0, attr_transform="log1p")
    assert s is not None and getattr(s, "attr_transform", None) == "log1p"


def test_build_scorer_default_linear_byte_identical():
    """build_scorer default (no attr_transform kwarg) produces attr_transform='linear'
    or equivalent, confirming the OFF path is byte-identical."""
    from braunschweig.synthesis.locations.secondary_chainsolvers import build_scorer
    s = build_scorer(True, "combined", 1.0, 1.0)
    assert getattr(s, "attr_transform", "linear") in ("linear", "none")
