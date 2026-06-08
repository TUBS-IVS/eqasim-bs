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


# ---------------------------------------------------------------------------
# B4b Part 3 — NaN->None hardening in _map_stop_kreis
# ---------------------------------------------------------------------------

def test_stop_outside_all_kreise_does_not_produce_nan_source_ars5():
    """A transit stop outside every Kreis polygon must not generate a NaN source_ars5 row.

    The _map_stop_kreis helper uses a left sjoin (predicate="within"), which returns
    NaN for unmatched rows.  The B4b hardening coerces NaN -> None so the downstream
    ``kreis not in zgb`` guard filters the stop out instead of leaking a NaN-keyed row.

    We place a rail stop far outside ALL Kreis polygons (kreise cover [0,200] in x;
    the orphan stop is at x=9999).  It serves a direct ZGB route, so without the
    NaN->None fix it would survive the eligibility filter and appear in the output
    with source_ars5=NaN.  After the fix it must be absent.
    """
    kreise = _kreise()   # external Kreis 15003 in [0,100] x [0,100], ZGB in [100,200]
    gemeinden = _gemeinden()

    # An extra stop far outside ALL Kreis polygons (x=9999, well beyond the [0,200] range).
    stops = {
        "RAIL_EXT":   (50.0, 50.0),    # inside external Kreis 15003
        "RAIL_ZGB":   (150.0, 50.0),   # inside ZGB Kreis 03101
        "ORPHAN_EXT": (9999.0, 50.0),  # outside all Kreis polygons -> must get None
    }
    # ORPHAN_EXT and RAIL_EXT are on a direct route into ZGB.
    routes = [
        ("rail", ["RAIL_EXT",   "RAIL_ZGB"]),
        ("rail", ["ORPHAN_EXT", "RAIL_ZGB"]),
    ]
    df, _n_fb = build_rail_entry_stations(stops, routes, kreise, gemeinden, ZGB)

    # ORPHAN_EXT's Kreis is NaN from the sjoin -> must be coerced to None -> filtered out.
    assert "ORPHAN_EXT" not in df["stop_id"].values, \
        "stop outside all Kreis polygons must be excluded (NaN source_ars5 must be None'd)"
    # No NaN values must appear in source_ars5.
    assert not df["source_ars5"].isna().any(), \
        "source_ars5 column must never contain NaN values"


def test_distance_cap_applied_via_build_rail_entry_stations():
    """build_rail_entry_stations with max_station_distance_km drops far stations.

    RAIL_EXT at (50, 50) is close to the ZGB stop RAIL_ZGB at (150, 50): ~100 km apart.
    FAR_EXT at (99000, 50) is ~99 km from the nearest ZGB stop -- wait, we need it
    actually far.  Use 300 km (300000 m) to be safely over a 150 km cap.
    ZGB stops are used as zgb_points for the cap, so FAR_EXT must be dropped.
    """
    kreise_ext = gpd.GeoDataFrame(
        {"ars5": ["15003", "99998", "03101"]},
        geometry=[
            box(0, 0, 100, 100),          # external Kreis 15003  (RAIL_EXT at 50,50)
            box(290000, 0, 310000, 100),   # external Kreis 99998  (FAR_EXT at 300000,50)
            box(100, 0, 200, 100),         # ZGB Kreis 03101
        ],
        crs="EPSG:25832",
    )
    gem_ext = gpd.GeoDataFrame(
        {"ars5": ["15003", "99998"], "ewz": [50000.0, 10000.0]},
        geometry=[
            Polygon([(0, 0), (100, 0), (100, 100), (0, 100)]),
            Polygon([(290000, 0), (310000, 0), (310000, 100), (290000, 100)]),
        ],
        crs="EPSG:25832",
    )
    stops = {
        "RAIL_EXT": (50.0, 50.0),
        "RAIL_ZGB": (150.0, 50.0),
        "FAR_EXT":  (300000.0, 50.0),
    }
    routes = [
        ("rail", ["RAIL_EXT", "RAIL_ZGB"]),
        ("rail", ["FAR_EXT",  "RAIL_ZGB"]),
    ]
    # Without cap both external stops appear; with cap=150 km FAR_EXT (300 km away) drops.
    df_no_cap, _ = build_rail_entry_stations(stops, routes, kreise_ext, gem_ext, {"03101"})
    assert "FAR_EXT" in df_no_cap["stop_id"].values, \
        "without distance cap FAR_EXT must appear (baseline check)"

    df_capped, _ = build_rail_entry_stations(
        stops, routes, kreise_ext, gem_ext, {"03101"},
        max_station_distance_km=150.0,
    )
    assert "FAR_EXT" not in df_capped["stop_id"].values, \
        "with 150 km cap FAR_EXT (300 km from ZGB) must be dropped"
    assert "RAIL_EXT" in df_capped["stop_id"].values, \
        "RAIL_EXT (close to ZGB) must survive the distance cap"
