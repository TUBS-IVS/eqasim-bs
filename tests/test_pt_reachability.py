"""Tests for braunschweig.data.cordon.pt_reachability.

Spec: eligible_rail_entry_stations returns every NON-ZGB rail stop that is
reachable to ZGB directly (single train) or with exactly one transfer, with
"direct" winning when a stop qualifies for both.
"""
import pandas as pd
import pytest

from braunschweig.data.cordon.pt_reachability import eligible_rail_entry_stations


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
