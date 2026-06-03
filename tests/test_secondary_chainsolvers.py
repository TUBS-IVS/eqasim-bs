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
