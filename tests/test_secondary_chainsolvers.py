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
