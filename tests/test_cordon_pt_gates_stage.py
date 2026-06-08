"""Tests for the cordon_pt_gates stage pure core builder.

Verifies that ``build_rail_entry_stations`` (the testable pure function exposed by
the stage module) returns RAIL-ONLY eligible entry stations with the new schema
[source_ars5, stop_id, x, y, reach, ewz] and that bus stops are excluded.
"""
from __future__ import annotations

import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Polygon, box

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.cordon_pt_gates import build_rail_entry_stations  # noqa: E402


ZGB = {"03101"}


def _kreise():
    """Minimal Kreise GeoDataFrame: one external Kreis (15003) and one ZGB Kreis (03101)."""
    return gpd.GeoDataFrame(
        {"ars5": ["15003", "03101"]},
        geometry=[
            box(0, 0, 100, 100),       # external Kreis 15003
            box(100, 0, 200, 100),     # ZGB Kreis 03101
        ],
        crs="EPSG:25832",
    )


def _gemeinden():
    """External Gemeinden frame: one Gemeinde covering the external Kreis polygon."""
    return gpd.GeoDataFrame(
        {"ars5": ["15003"], "ewz": [50000.0]},
        geometry=[Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])],
        crs="EPSG:25832",
    )


def _stops_and_routes():
    """
    Stops:
      RAIL_EXT  at (50, 50)  — in external Kreis 15003; on a rail route serving ZGB.
      RAIL_ZGB  at (150, 50) — in ZGB Kreis 03101.
      BUS_EXT   at (20, 20)  — in external Kreis 15003; on a bus route serving ZGB.

    Routes:
      R_rail (rail): [RAIL_EXT, RAIL_ZGB]   — ZGB-serving rail route.
      R_bus  (bus):  [BUS_EXT,  RAIL_ZGB]   — ZGB-serving but BUS: must be excluded.
    """
    stops = {
        "RAIL_EXT": (50.0, 50.0),
        "RAIL_ZGB": (150.0, 50.0),
        "BUS_EXT":  (20.0, 20.0),
    }
    routes = [
        ("rail", ["RAIL_EXT", "RAIL_ZGB"]),
        ("bus",  ["BUS_EXT",  "RAIL_ZGB"]),
    ]
    return stops, routes


def test_build_rail_entry_stations_schema():
    """Output DataFrame must have exactly the new schema columns."""
    stops, routes = _stops_and_routes()
    df, _n_fb = build_rail_entry_stations(stops, routes, _kreise(), _gemeinden(), ZGB)
    assert list(df.columns) == ["source_ars5", "stop_id", "x", "y", "reach", "ewz"], \
        f"unexpected columns: {list(df.columns)}"


def test_build_rail_entry_stations_excludes_bus_stops():
    """BUS_EXT is on a ZGB-serving bus route but must NOT appear in the result."""
    stops, routes = _stops_and_routes()
    df, _n_fb = build_rail_entry_stations(stops, routes, _kreise(), _gemeinden(), ZGB)
    assert "BUS_EXT" not in df["stop_id"].values, \
        "bus stops must be excluded from rail-only entry stations"


def test_build_rail_entry_stations_includes_rail_stops():
    """RAIL_EXT must appear as a direct entry station (it is on a direct rail route)."""
    stops, routes = _stops_and_routes()
    df, _n_fb = build_rail_entry_stations(stops, routes, _kreise(), _gemeinden(), ZGB)
    assert "RAIL_EXT" in df["stop_id"].values, \
        "RAIL_EXT must be included as a direct entry station"
    row = df[df["stop_id"] == "RAIL_EXT"].iloc[0]
    assert row["reach"] == "direct", "RAIL_EXT is on a direct ZGB-serving rail route"


def test_build_rail_entry_stations_zgb_stop_absent():
    """ZGB stop RAIL_ZGB must never appear in the output."""
    stops, routes = _stops_and_routes()
    df, _n_fb = build_rail_entry_stations(stops, routes, _kreise(), _gemeinden(), ZGB)
    assert "RAIL_ZGB" not in df["stop_id"].values, \
        "ZGB stops must not be returned as entry stations"


def test_build_rail_entry_stations_coordinates():
    """x, y columns must match the stop coordinates."""
    stops, routes = _stops_and_routes()
    df, _n_fb = build_rail_entry_stations(stops, routes, _kreise(), _gemeinden(), ZGB)
    row = df[df["stop_id"] == "RAIL_EXT"].iloc[0]
    assert abs(row["x"] - 50.0) < 1e-6 and abs(row["y"] - 50.0) < 1e-6, \
        "x, y must equal the stop's schedule coordinates"


def test_build_rail_entry_stations_ewz_populated():
    """ewz must be populated (non-NaN) for stations whose Gemeinde is found."""
    stops, routes = _stops_and_routes()
    df, _n_fb = build_rail_entry_stations(stops, routes, _kreise(), _gemeinden(), ZGB)
    assert df["ewz"].notna().all(), "ewz must be non-NaN for every returned station"
    row = df[df["stop_id"] == "RAIL_EXT"].iloc[0]
    assert row["ewz"] == 50000.0, "RAIL_EXT is inside the 50000-ewz Gemeinde polygon"


def test_build_rail_entry_stations_returns_fallback_count():
    """Second return value must be the integer nearest-Gemeinde fallback count."""
    stops, routes = _stops_and_routes()
    df, n_fb = build_rail_entry_stations(stops, routes, _kreise(), _gemeinden(), ZGB)
    assert isinstance(n_fb, int), "fallback count must be an int"
    # RAIL_EXT is clearly inside the Gemeinde polygon; containment join should succeed.
    assert n_fb == 0, "no station should fall back to nearest-Gemeinde for this fixture"


def test_build_rail_entry_stations_direct_vs_transfer():
    """Validate the direct/transfer classification end-to-end through build_rail_entry_stations.

    Routes:
      R_direct (rail): [EXT_A, ZGB_A]    -> EXT_A is a direct station.
      R_other  (rail): [EXT_B, EXT_A]    -> EXT_B reaches ZGB via transfer at EXT_A.
    """
    stops = {
        "EXT_A": (50.0, 50.0),   # external Kreis 15003
        "EXT_B": (30.0, 30.0),   # external Kreis 15003
        "ZGB_A": (150.0, 50.0),  # ZGB Kreis 03101
    }
    routes = [
        ("rail", ["EXT_A", "ZGB_A"]),
        ("rail", ["EXT_B", "EXT_A"]),
    ]
    df, _n_fb = build_rail_entry_stations(stops, routes, _kreise(), _gemeinden(), ZGB)
    got = dict(zip(df["stop_id"], df["reach"]))
    assert got.get("EXT_A") == "direct", "EXT_A is on a direct ZGB-serving rail route"
    assert got.get("EXT_B") == "transfer", "EXT_B reaches ZGB via one transfer at EXT_A"


def test_build_rail_entry_stations_empty_when_no_rail_routes():
    """When there are no rail routes at all, the result must be an empty DataFrame with new schema."""
    stops = {
        "BUS_EXT": (50.0, 50.0),
        "BUS_ZGB": (150.0, 50.0),
    }
    routes = [("bus", ["BUS_EXT", "BUS_ZGB"])]
    df, n_fb = build_rail_entry_stations(stops, routes, _kreise(), _gemeinden(), ZGB)
    assert len(df) == 0, "no rail routes -> no entry stations"
    assert list(df.columns) == ["source_ars5", "stop_id", "x", "y", "reach", "ewz"], \
        "empty result must still have the new schema columns"
    assert n_fb == 0


def test_build_rail_entry_stations_source_ars5_matches_kreis():
    """source_ars5 must be the external Kreis ARS, not a ZGB Kreis."""
    stops, routes = _stops_and_routes()
    df, _n_fb = build_rail_entry_stations(stops, routes, _kreise(), _gemeinden(), ZGB)
    external_ars5 = set(df["source_ars5"].unique())
    assert external_ars5.issubset({"15003"}), \
        "all returned source_ars5 values must be external (non-ZGB) Kreise"
