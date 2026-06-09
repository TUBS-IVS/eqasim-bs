"""Tests for braunschweig.data.cordon.pt_reachability.

Spec: eligible_rail_entry_stations returns every NON-ZGB rail stop that is
reachable to ZGB directly (single train) or with exactly one transfer, with
"direct" winning when a stop qualifies for both.

weight_entry_stations / sample_pt_station_per_agent: population+accessibility
weighting and per-agent station draw for cross-cordon PT in-commuters.
"""
import numpy as np
import pandas as pd
import pytest

from braunschweig.data.cordon.pt_reachability import (
    eligible_rail_entry_stations,
    weight_entry_stations,
    sample_pt_station_per_agent,
)


# ---------------------------------------------------------------------------
# Main spec test
# ---------------------------------------------------------------------------

def test_direct_and_one_transfer_rail_only():
    """Validate direct/transfer classification and bus-exclusion on a minimal schedule.

    Routes:
      R1 (rail): MD1-MD2-HUB  -- no ZGB stop, not a direct route
      R2 (rail): HUB-BS1      -- HUB is external, BS1 in ZGB -> DIRECT route
      R3 (rail): MD3-BS2      -- MD3 external, BS2 in ZGB -> DIRECT route
      R4 (bus):  MD4-BS3      -- ZGB-serving but BUS -> excluded entirely

    direct_routes = [R2, R3]
    hubs (all stops on direct routes) = {HUB, BS1, MD3, BS2}

    R1 shares HUB with hubs -> one-transfer-eligible:
        MD1, MD2 -> "transfer"; HUB -> already "direct" (on R2), stays "direct"
    R4 is excluded (bus).

    Expected result (external stops only, kreis not None, not ZGB):
      MD3  -> "direct"      (on direct route R3)
      HUB  -> "direct"      (on direct route R2; NOT "transfer" -- direct wins)
      MD1  -> "transfer"    (on R1 which shares HUB with the hub set)
      MD2  -> "transfer"
      MD4  -> absent        (bus route excluded)
      BS1  -> absent        (ZGB stop)
      BS2  -> absent        (ZGB stop)
    """
    routes = [
        ("rail", ["MD1", "MD2", "HUB"]),
        ("rail", ["HUB", "BS1"]),
        ("rail", ["MD3", "BS2"]),
        ("bus",  ["MD4", "BS3"]),
    ]
    stop_kreis = {
        "MD1": "15003", "MD2": "15003", "HUB": "15003", "BS1": "03101",
        "MD3": "15003", "BS2": "03101", "MD4": "15003", "BS3": "03101",
    }
    df = eligible_rail_entry_stations(routes, stop_kreis, zgb_kreise={"03101"})
    got = dict(zip(df["stop_id"], df["reach"]))

    assert got["MD3"] == "direct", "MD3 is on direct route R3"
    # HUB appears on direct route R2 (HUB-BS1), so it gets "direct", not "transfer".
    assert got["HUB"] == "direct", "HUB is on direct route R2; direct beats transfer"
    assert got["MD1"] == "transfer", "MD1 only reachable via R1 which shares HUB with direct routes"
    assert got["MD2"] == "transfer", "MD2 only reachable via R1 which shares HUB with direct routes"
    assert "MD4" not in got, "MD4 is on a bus route; must be excluded"
    assert "BS1" not in got, "BS1 is a ZGB stop; must not appear as entry station"
    assert "BS2" not in got, "BS2 is a ZGB stop; must not appear as entry station"
    assert all(df["source_ars5"] == "15003"), "all external stops belong to Kreis 15003"
    assert set(df.columns) == {"source_ars5", "stop_id", "reach"}


# ---------------------------------------------------------------------------
# "direct beats transfer" when a stop qualifies for both
# ---------------------------------------------------------------------------

def test_direct_beats_transfer_for_same_stop():
    """A stop that is on BOTH a direct route and a transfer-eligible route must be "direct"."""
    # SHARED is on direct route R_d and also on non-direct route R_t.
    # Without the "direct wins" rule SHARED might end up "transfer".
    routes = [
        ("rail", ["SHARED", "ZGB1"]),   # direct: SHARED -> ZGB1
        ("rail", ["EXT1", "SHARED"]),   # EXT1 reaches ZGB via SHARED (transfer)
    ]
    stop_kreis = {
        "SHARED": "99001",
        "ZGB1":   "03101",
        "EXT1":   "99001",
    }
    df = eligible_rail_entry_stations(routes, stop_kreis, zgb_kreise={"03101"})
    got = dict(zip(df["stop_id"], df["reach"]))
    assert got["SHARED"] == "direct", "SHARED is on a direct route; direct must win over transfer"
    assert got["EXT1"] == "transfer", "EXT1 reaches ZGB only via a transfer at SHARED"


# ---------------------------------------------------------------------------
# Stops with kreis=None are omitted
# ---------------------------------------------------------------------------

def test_stop_with_none_kreis_is_omitted():
    """Stops whose kreis is None must not appear in the result."""
    routes = [
        ("rail", ["UNKNOWN", "ZGB1"]),   # direct route; UNKNOWN has no kreis
    ]
    stop_kreis = {
        "UNKNOWN": None,
        "ZGB1":    "03101",
    }
    df = eligible_rail_entry_stations(routes, stop_kreis, zgb_kreise={"03101"})
    assert "UNKNOWN" not in df["stop_id"].values, "stop with kreis=None must be omitted"
    # ZGB1 is a ZGB stop and must also be absent
    assert "ZGB1" not in df["stop_id"].values
    assert len(df) == 0


# ---------------------------------------------------------------------------
# weight_entry_stations tests (B2)
# ---------------------------------------------------------------------------

def test_weight_prefers_direct_and_populous():
    """A direct station with high catchment population must dominate the weight.

    Station A: direct reach, ewz=100000 -> weight proportional to 100000*1.0
    Station B: transfer reach, ewz=1000  -> weight proportional to 1000*0.5

    A's weight must exceed B's, and the within-Kreis weights must sum to 1.0.
    A statistical draw over 1000 agents must pick A more than 80% of the time.
    """
    stations = pd.DataFrame({
        "source_ars5": ["15003", "15003"],
        "stop_id": ["A", "B"],
        "reach": ["direct", "transfer"],
        "ewz": [100000.0, 1000.0],
    })
    w = weight_entry_stations(stations, transfer_penalty=0.5)
    wa = w.loc[w.stop_id == "A", "weight"].iloc[0]
    wb = w.loc[w.stop_id == "B", "weight"].iloc[0]
    assert wa > wb, "direct high-population station must have higher weight than transfer low-population station"
    assert abs(w.groupby("source_ars5")["weight"].sum().iloc[0] - 1.0) < 1e-9, \
        "weights must normalise to 1.0 within each Kreis"

    rng = np.random.default_rng(0)
    draws = sample_pt_station_per_agent(["15003"] * 1000, w, rng)
    share_a = sum(d == "A" for d in draws) / 1000.0
    assert share_a > 0.8, f"station A must be drawn >80% of the time; got {share_a:.3f}"


def test_sample_returns_none_for_unknown_kreis():
    """Agents from an unknown Kreis must get None; known Kreis returns the single station."""
    stations = pd.DataFrame({
        "source_ars5": ["15003"],
        "stop_id": ["A"],
        "reach": ["direct"],
        "ewz": [5000.0],
    })
    w = weight_entry_stations(stations)
    rng = np.random.default_rng(1)
    draws = sample_pt_station_per_agent(["99999", "15003"], w, rng)
    assert draws[0] is None, "unknown Kreis 99999 must yield None"
    assert draws[1] == "A", "known Kreis 15003 with one station must always yield that station"


def test_transfer_only_kreis_weights_normalise_to_one():
    """A Kreis with only transfer-reach stations still produces weights summing to 1.0.

    The transfer_penalty downweights all stations by the same factor, which cancels
    during normalisation; the Kreis remains fully sampled.
    """
    stations = pd.DataFrame({
        "source_ars5": ["03159", "03159", "03159"],
        "stop_id": ["X", "Y", "Z"],
        "reach": ["transfer", "transfer", "transfer"],
        "ewz": [2000.0, 4000.0, 6000.0],
    })
    w = weight_entry_stations(stations, transfer_penalty=0.3)
    total_weight = w.groupby("source_ars5")["weight"].sum().iloc[0]
    assert abs(total_weight - 1.0) < 1e-9, \
        "transfer-only Kreis weights must still normalise to 1.0"

    # Proportions must match ewz ratios (transfer_penalty cancels in normalisation).
    by_id = w.set_index("stop_id")["weight"]
    assert by_id["Y"] > by_id["X"], "station with higher ewz must receive higher weight"
    assert by_id["Z"] > by_id["Y"], "station with highest ewz must receive highest weight"

    rng = np.random.default_rng(42)
    draws = sample_pt_station_per_agent(["03159"] * 500, w, rng)
    assert all(d is not None for d in draws), "all agents from transfer-only Kreis must be assigned a station"


def test_weight_ewz_floored_at_one():
    """ewz <= 0 must be treated as 1.0 (floor) so no station is silently zeroed out."""
    stations = pd.DataFrame({
        "source_ars5": ["03101", "03101"],
        "stop_id": ["P", "Q"],
        "reach": ["direct", "direct"],
        "ewz": [0.0, -5.0],
    })
    w = weight_entry_stations(stations)
    # Both clipped to 1.0 -> equal weight 0.5 each
    wp = w.loc[w.stop_id == "P", "weight"].iloc[0]
    wq = w.loc[w.stop_id == "Q", "weight"].iloc[0]
    assert abs(wp - 0.5) < 1e-9, "zero ewz must be floored to 1.0 and yield weight 0.5"
    assert abs(wq - 0.5) < 1e-9, "negative ewz must be floored to 1.0 and yield weight 0.5"


def test_weight_output_contains_required_columns():
    """weight_entry_stations must return all input columns plus 'weight'."""
    stations = pd.DataFrame({
        "source_ars5": ["03104"],
        "stop_id": ["S1"],
        "reach": ["direct"],
        "ewz": [3000.0],
        "x": [123456.0],
        "y": [654321.0],
    })
    w = weight_entry_stations(stations)
    for col in ("source_ars5", "stop_id", "reach", "ewz", "x", "y", "weight"):
        assert col in w.columns, f"column '{col}' missing from weight_entry_stations output"


def test_sample_pt_station_reproducible_given_seed():
    """Identical seeds must produce identical draws; different seeds may differ."""
    stations = pd.DataFrame({
        "source_ars5": ["15003", "15003"],
        "stop_id": ["M", "N"],
        "reach": ["direct", "transfer"],
        "ewz": [50000.0, 30000.0],
    })
    w = weight_entry_stations(stations)
    orig_ars = ["15003"] * 20

    draws_a = sample_pt_station_per_agent(orig_ars, w, np.random.default_rng(7))
    draws_b = sample_pt_station_per_agent(orig_ars, w, np.random.default_rng(7))
    assert draws_a == draws_b, "same seed must produce identical draws"


# ---------------------------------------------------------------------------
# attach_station_population tests (B3)
# ---------------------------------------------------------------------------

import geopandas as gpd
from shapely.geometry import Polygon
from braunschweig.data.cordon.pt_reachability import attach_station_population


def _gem(ars5, ewz, minx, maxx):
    """Helper: build one Gemeinde row as a horizontal strip polygon in EPSG:25832."""
    return {"ars5": ars5, "ewz": ewz,
            "geometry": Polygon([(minx, 0), (maxx, 0), (maxx, 10), (minx, 10)])}


def test_attach_population_containment_and_nearest():
    """Stations inside a Gemeinde polygon receive that Gemeinde's ewz (primary path).

    Two stations, each clearly inside its own Gemeinde polygon -> containment join
    matches both; fallback count must be 0.  Output must contain all expected columns.
    """
    gem = gpd.GeoDataFrame(
        [_gem("15003", 100000, 0, 10), _gem("15081", 2000, 20, 30)],
        crs="EPSG:25832",
    )
    stations = pd.DataFrame({
        "source_ars5": ["15003", "15081"],
        "stop_id": ["A", "B"],
        "reach": ["direct", "transfer"],
        "x": [5.0, 25.0],
        "y": [5.0, 5.0],
    })
    out, n_fallback = attach_station_population(stations, gem)
    ewz = dict(zip(out["stop_id"], out["ewz"]))
    assert ewz["A"] == 100000, "station A inside 15003 polygon must get ewz 100000"
    assert ewz["B"] == 2000, "station B inside 15081 polygon must get ewz 2000"
    assert n_fallback == 0, "both stations match by containment; fallback count must be 0"
    assert {"source_ars5", "stop_id", "reach", "x", "y", "ewz"} <= set(out.columns), \
        "output must include all required columns"


def test_attach_population_nearest_fallback_counted():
    """A station outside all polygons must be matched to the nearest Gemeinde (fallback).

    The fallback count returned must equal the number of stations that used the
    nearest-neighbour path (i.e. 1 here).  The ewz must still be filled, not NaN.
    """
    gem = gpd.GeoDataFrame([_gem("15003", 100000, 0, 10)], crs="EPSG:25832")
    stations = pd.DataFrame({
        "source_ars5": ["15003"],
        "stop_id": ["C"],
        "reach": ["direct"],
        "x": [100.0],
        "y": [100.0],
    })   # far outside any polygon
    out, n_fallback = attach_station_population(stations, gem)
    assert out["ewz"].iloc[0] == 100000, \
        "station outside all polygons must be filled via nearest-Gemeinde fallback"
    assert n_fallback == 1, "one station used the nearest-neighbour fallback; count must be 1"


# ---------------------------------------------------------------------------
# apply_station_distance_cap tests (B4b Part 1)
# ---------------------------------------------------------------------------

import numpy as np
from braunschweig.data.cordon.pt_reachability import apply_station_distance_cap


def test_distance_cap_drops_far_stations():
    """Stations farther than max_km from every ZGB reference point are dropped.

    One station 10 km from the origin (kept); one station 300 km away (dropped).
    ZGB reference point is at the origin.
    """
    stations = pd.DataFrame({
        "source_ars5": ["15003", "99999"],
        "stop_id": ["near", "far"],
        "reach": ["transfer", "transfer"],
        "x": [10000.0, 300000.0],
        "y": [0.0, 0.0],
    })
    zgb_points = np.array([[0.0, 0.0]])
    kept, n_dropped = apply_station_distance_cap(stations, zgb_points, max_km=150.0)
    assert set(kept["stop_id"]) == {"near"}, "only the near station (10 km) must be kept"
    assert n_dropped == 1, "one station must be dropped"


def test_distance_cap_empty_zgb_points_is_noop():
    """When zgb_points is empty the function must return the input unchanged with n_dropped=0.

    This handles the edge case where the ZGB schedule has no in-scope rail stops
    (e.g. a synthetic schedule used in unit tests).  The cap is inactive; the caller
    logs that it was skipped.
    """
    stations = pd.DataFrame({
        "source_ars5": ["15003"],
        "stop_id": ["A"],
        "reach": ["direct"],
        "x": [5.0],
        "y": [5.0],
    })
    kept, n = apply_station_distance_cap(stations, np.empty((0, 2)), max_km=150.0)
    assert len(kept) == 1, "all stations must be kept when zgb_points is empty"
    assert n == 0, "n_dropped must be 0 when zgb_points is empty"


def test_distance_cap_keeps_stations_within_threshold():
    """All stations within max_km of ANY ZGB point are kept regardless of which ZGB point."""
    # Two ZGB points: origin and (200km, 0).  Stations near either are kept.
    stations = pd.DataFrame({
        "source_ars5": ["15003", "15004", "99999"],
        "stop_id": ["near_a", "near_b", "far"],
        "reach": ["direct", "transfer", "transfer"],
        "x": [10000.0, 190000.0, 500000.0],
        "y": [0.0, 0.0, 0.0],
    })
    zgb_points = np.array([[0.0, 0.0], [200000.0, 0.0]])
    kept, n_dropped = apply_station_distance_cap(stations, zgb_points, max_km=50.0)
    assert set(kept["stop_id"]) == {"near_a", "near_b"}, \
        "stations close to any ZGB point must be kept"
    assert n_dropped == 1


def test_distance_cap_preserves_all_columns():
    """The kept DataFrame must preserve all input columns (not just stop_id/x/y)."""
    stations = pd.DataFrame({
        "source_ars5": ["15003"],
        "stop_id": ["S"],
        "reach": ["direct"],
        "x": [1000.0],
        "y": [0.0],
        "ewz": [99999.0],
    })
    zgb_points = np.array([[0.0, 0.0]])
    kept, _ = apply_station_distance_cap(stations, zgb_points, max_km=50.0)
    for col in ("source_ars5", "stop_id", "reach", "x", "y", "ewz"):
        assert col in kept.columns, f"column '{col}' must be preserved by distance cap"


def test_distance_cap_zero_stations_is_noop():
    """An empty stations frame must pass through without error."""
    stations = pd.DataFrame(columns=["source_ars5", "stop_id", "reach", "x", "y"])
    zgb_points = np.array([[0.0, 0.0]])
    kept, n = apply_station_distance_cap(stations, zgb_points, max_km=150.0)
    assert len(kept) == 0
    assert n == 0


def test_attach_population_preserves_all_stations_with_nonunique_index():
    """Two DISTINCT stations that share a non-unique pandas index must both appear in output.

    Regression: the old dedup used `contained.index.duplicated(keep="first")` which
    keyed on the caller's index, not station identity.  When two stations share the same
    index label (e.g. from a filtered/concatenated frame), the second was silently
    dropped.  After the fix, every input row must produce exactly one output row.
    """
    gem = gpd.GeoDataFrame(
        [{"ars5": "15003", "ewz": 100000, "geometry": Polygon([(0, 0), (10, 0), (10, 10), (0, 10)])},
         {"ars5": "15081", "ewz": 2000,   "geometry": Polygon([(20, 0), (30, 0), (30, 10), (20, 10)])}],
        crs="EPSG:25832",
    )
    # Two DISTINCT stations that share a non-unique index value (e.g. from a filtered frame).
    stations = pd.DataFrame(
        {"source_ars5": ["15003", "15081"], "stop_id": ["A", "B"],
         "reach": ["direct", "direct"], "x": [5.0, 25.0], "y": [5.0, 5.0]},
        index=[0, 0],
    )
    out, n_fallback = attach_station_population(stations, gem)
    assert len(out) == 2, "both distinct stations must be present in the output; one must not be dropped"
    assert set(out["stop_id"]) == {"A", "B"}, "both stop_ids must survive"
    assert dict(zip(out["stop_id"], out["ewz"])) == {"A": 100000.0, "B": 2000.0}, \
        "each station must receive the ewz of its containing Gemeinde"
    assert n_fallback == 0, "both stations match by containment; fallback count must be 0"
    assert out["ewz"].dtype == float, "ewz must be float dtype regardless of source column dtype"


# ---------------------------------------------------------------------------
# weight_entry_stations gravity-beta tests (Task PT-G)
# ---------------------------------------------------------------------------

def test_gravity_beta_prefers_closer_station():
    """With beta<0, the nearer station gets a strictly higher weight; with beta=0 they are equal.

    Two stations, same source_ars5, same ewz, same reach="direct", but different
    dist_to_zgb_km (10 vs 80 km).  The gravity decay is exp(beta * dist_to_zgb_km).

    With beta=0.0: decay=1 for both -> weights proportional to ewz only -> EQUAL.
    With beta=-0.05: decay(10)=exp(-0.5) > decay(80)=exp(-4.0) -> nearer is HIGHER.
    """
    stations = pd.DataFrame({
        "source_ars5": ["15003", "15003"],
        "stop_id": ["near", "far"],
        "reach": ["direct", "direct"],
        "ewz": [10000.0, 10000.0],
        "dist_to_zgb_km": [10.0, 80.0],
    })

    # beta=0.0 -> no decay -> weights must be equal
    w_zero = weight_entry_stations(stations, transfer_penalty=0.5, beta=0.0)
    w_near_zero = w_zero.loc[w_zero["stop_id"] == "near", "weight"].iloc[0]
    w_far_zero = w_zero.loc[w_zero["stop_id"] == "far", "weight"].iloc[0]
    assert abs(w_near_zero - w_far_zero) < 1e-9, \
        "beta=0.0 must produce equal weights for equal-ewz equal-reach stations"

    # beta=-0.05 -> nearer station must dominate
    w_decay = weight_entry_stations(stations, transfer_penalty=0.5, beta=-0.05)
    w_near_decay = w_decay.loc[w_decay["stop_id"] == "near", "weight"].iloc[0]
    w_far_decay = w_decay.loc[w_decay["stop_id"] == "far", "weight"].iloc[0]
    assert w_near_decay > w_far_decay, \
        "beta=-0.05 must give the nearer station (10 km) a higher weight than the far one (80 km)"

    # weights must still sum to 1 within the Kreis
    for w_df in (w_zero, w_decay):
        total = w_df.groupby("source_ars5")["weight"].sum().iloc[0]
        assert abs(total - 1.0) < 1e-9, "weights must normalise to 1.0 within each Kreis"


def test_gravity_no_column_or_zero_beta_unchanged():
    """beta=0 or a missing dist_to_zgb_km column must reproduce the pre-gravity weights.

    Two stations with different ewz but no dist_to_zgb_km column.  The returned
    weights must be identical to the beta=0.0 call (purely ewz * accessibility).
    """
    stations_no_col = pd.DataFrame({
        "source_ars5": ["15003", "15003"],
        "stop_id": ["A", "B"],
        "reach": ["direct", "transfer"],
        "ewz": [80000.0, 20000.0],
    })
    # Call with no beta argument (default 0.0) and with explicit beta=0.0
    w_default = weight_entry_stations(stations_no_col)
    w_beta_zero = weight_entry_stations(stations_no_col, beta=0.0)

    # Add a dist_to_zgb_km column but keep beta=0.0 -> must still be unchanged
    stations_with_col = stations_no_col.copy()
    stations_with_col["dist_to_zgb_km"] = [10.0, 50.0]
    w_col_zero_beta = weight_entry_stations(stations_with_col, beta=0.0)

    for stop_id in ("A", "B"):
        w_d = w_default.loc[w_default["stop_id"] == stop_id, "weight"].iloc[0]
        w_z = w_beta_zero.loc[w_beta_zero["stop_id"] == stop_id, "weight"].iloc[0]
        w_c = w_col_zero_beta.loc[w_col_zero_beta["stop_id"] == stop_id, "weight"].iloc[0]
        assert abs(w_d - w_z) < 1e-12, \
            f"default beta must equal explicit beta=0.0 for stop {stop_id}"
        assert abs(w_d - w_c) < 1e-12, \
            f"beta=0.0 with dist column must equal no-column result for stop {stop_id}"


def test_gravity_nan_distance_treated_as_no_decay():
    """A NaN dist_to_zgb_km row must not produce a NaN weight; it receives decay=1.0.

    When zgb_points are unavailable (e.g. an empty ZGB schedule) the dist column
    may contain NaN.  The gravity formula must guard against this and treat NaN
    distance as decay=1.0 (no gravity applied for that station), so the weight is
    purely ewz * accessibility as in the pre-gravity case.
    """
    stations = pd.DataFrame({
        "source_ars5": ["15003", "15003"],
        "stop_id": ["known", "unknown"],
        "reach": ["direct", "direct"],
        "ewz": [10000.0, 10000.0],
        "dist_to_zgb_km": [30.0, float("nan")],
    })
    w = weight_entry_stations(stations, beta=-0.05)

    # No NaN weights must appear
    assert not w["weight"].isna().any(), \
        "NaN dist_to_zgb_km must not propagate to a NaN weight"

    # The unknown-distance station must have a positive weight
    w_unknown = w.loc[w["stop_id"] == "unknown", "weight"].iloc[0]
    assert w_unknown > 0.0, \
        "station with NaN dist_to_zgb_km must have a positive weight (decay treated as 1.0)"

    # The NaN-distance station gets decay 1.0; the known-distance station gets
    # decay exp(-0.05 * 30) = exp(-1.5) < 1.0 -> the NaN station has HIGHER weight.
    w_known = w.loc[w["stop_id"] == "known", "weight"].iloc[0]
    assert w_unknown > w_known, \
        "station with NaN distance (decay 1.0) must outweigh a station 30 km away (decay<1)"

    # Weights must still sum to 1
    total = w.groupby("source_ars5")["weight"].sum().iloc[0]
    assert abs(total - 1.0) < 1e-9, "weights must normalise to 1.0 even with NaN distance"
