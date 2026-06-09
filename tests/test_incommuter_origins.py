"""Tests for braunschweig.data.cordon.incommuter_origins.

The helper is pure (no I/O): given per-agent origin-Kreis ARS, per-Kreis inbound
flow, per-Gemeinde polygons, a ZGB polygon, and a ring buffer, it assigns a
population-weighted in-ring home point per agent or marks the agent as far
(is_in_ring=False, home=nan) when its origin Kreis has no in-ring Gemeinden.

Geometry setup
--------------
ZGB = square [(0,0) -> (2,2)].  source_buffer_m = 5.0.
zgb.buffer(5.0) covers roughly x in [-5, 7], y in [-5, 7].

In-ring Gemeinde (ars5 "15083"): polygon at cx=3 -> representative point ~ (3.5, 0.5).
  3.5 < 7 and 0.5 in [-5,7] -> INSIDE the buffered ZGB -> in-ring.

Far Gemeinde (ars5 "11000"): polygon at cx=1000 -> representative point ~ (1000.5, 0.5).
  1000.5 >> 7 -> OUTSIDE the buffered ZGB -> far.
"""
from __future__ import annotations

import numpy as np
import geopandas as gpd
import pandas as pd
from shapely.geometry import Polygon

from braunschweig.data.cordon.incommuter_origins import incommuter_origin_homes


# ---------------------------------------------------------------------------
# shared geometry helpers
# ---------------------------------------------------------------------------

def _gem(ars5: str, ags: str, ewz: float, cx: float) -> dict:
    """1x1 unit square at x=cx, y in [0,1]."""
    return {
        "ars5": ars5,
        "gem_ags": ags,
        "ewz": ewz,
        "geometry": Polygon([(cx, 0), (cx + 1, 0), (cx + 1, 1), (cx, 1)]),
    }


ZGB = Polygon([(0, 0), (2, 0), (2, 2), (0, 2)])
SOURCE_BUFFER_M = 5.0
# buffered ZGB covers x in [-5, 7] -- cx=3 -> rep_point x~3.5 is INSIDE; cx=1000 is OUTSIDE


# ---------------------------------------------------------------------------
# Test 1: in-ring agent gets real origin, far agent gets nan + False
# ---------------------------------------------------------------------------

def test_in_ring_agent_gets_real_origin_far_agent_does_not():
    gem = gpd.GeoDataFrame(
        [
            _gem("15083", "15083001", 9000, 3),     # in-ring (cx=3, rep_pt ~3.5, inside buffer)
            _gem("11000", "11000001", 50000, 1000),  # far (cx=1000, rep_pt ~1000.5, outside buffer)
        ],
        crs="EPSG:25832",
    )
    inbound = pd.DataFrame({"ars5": ["15083", "11000"], "flow": [8000, 700]})
    rng = np.random.default_rng(0)

    hx, hy, inr = incommuter_origin_homes(
        ["15083", "11000"], inbound, gem, ZGB, source_buffer_m=SOURCE_BUFFER_M, rng=rng
    )

    assert len(hx) == 2 and len(hy) == 2 and len(inr) == 2

    # Agent 0 (15083): in-ring -> finite home coordinates
    assert inr[0] is True or bool(inr[0]) is True
    assert np.isfinite(hx[0]), f"Expected finite home_x for in-ring agent, got {hx[0]}"
    assert np.isfinite(hy[0]), f"Expected finite home_y for in-ring agent, got {hy[0]}"

    # Agent 1 (11000): far -> nan + not in-ring
    assert not bool(inr[1]), f"Expected is_in_ring=False for far agent, got {inr[1]}"
    assert np.isnan(hx[1]), f"Expected nan home_x for far agent, got {hx[1]}"
    assert np.isnan(hy[1]), f"Expected nan home_y for far agent, got {hy[1]}"


# ---------------------------------------------------------------------------
# Test 2: population-weighted draw + determinism
# ---------------------------------------------------------------------------

def test_population_weighted_draw_and_determinism():
    """High-ewz Gemeinde should dominate draws; same seed -> same result."""
    gem = gpd.GeoDataFrame(
        [
            _gem("15083", "15083001", 90000, 3),  # high pop, in-ring (cx=3)
            _gem("15083", "15083002", 1000, 3),   # low pop, in-ring (same area, cx=3)
        ],
        crs="EPSG:25832",
    )
    inbound = pd.DataFrame({"ars5": ["15083"], "flow": [10000]})

    hx_500, _, _ = incommuter_origin_homes(
        ["15083"] * 500, inbound, gem, ZGB, SOURCE_BUFFER_M, np.random.default_rng(1)
    )

    # Both Gemeinden have cx=3 so their rep_pt x is similar (~3.5) -- check finite
    assert np.all(np.isfinite(hx_500)), "All 500 draws should be finite (both Gemeinden in-ring)"

    # Determinism: same seed -> same first draw
    a = incommuter_origin_homes(["15083"], inbound, gem, ZGB, SOURCE_BUFFER_M, np.random.default_rng(2))[0]
    b = incommuter_origin_homes(["15083"], inbound, gem, ZGB, SOURCE_BUFFER_M, np.random.default_rng(2))[0]
    assert a[0] == b[0], f"Same seed must produce same draw; got {a[0]} vs {b[0]}"


# ---------------------------------------------------------------------------
# Test 3: unknown Kreis (not in inbound / gemeinden) -> far (nan + False)
# ---------------------------------------------------------------------------

def test_unknown_kreis_is_treated_as_far():
    """An orig_ars not present in inbound or gemeinden must yield nan + is_in_ring=False."""
    gem = gpd.GeoDataFrame(
        [_gem("15083", "15083001", 9000, 3)],
        crs="EPSG:25832",
    )
    inbound = pd.DataFrame({"ars5": ["15083"], "flow": [8000]})
    rng = np.random.default_rng(0)

    hx, hy, inr = incommuter_origin_homes(
        ["99999"],  # completely unknown
        inbound, gem, ZGB, SOURCE_BUFFER_M, rng,
    )

    assert len(hx) == 1
    assert not bool(inr[0]), "Unknown Kreis must be is_in_ring=False"
    assert np.isnan(hx[0]), "Unknown Kreis must have nan home_x"
    assert np.isnan(hy[0]), "Unknown Kreis must have nan home_y"


# ---------------------------------------------------------------------------
# Test 4: Kreis with points but ALL outside the ring -> far for its agents
# ---------------------------------------------------------------------------

def test_all_far_gemeinden_kreis_yields_far():
    """A Kreis whose every Gemeinde point falls outside the buffered ZGB -> is_in_ring=False."""
    gem = gpd.GeoDataFrame(
        [_gem("11000", "11000001", 50000, 1000)],  # far
        crs="EPSG:25832",
    )
    inbound = pd.DataFrame({"ars5": ["11000"], "flow": [700]})
    rng = np.random.default_rng(0)

    hx, hy, inr = incommuter_origin_homes(
        ["11000"], inbound, gem, ZGB, SOURCE_BUFFER_M, rng,
    )

    assert not bool(inr[0])
    assert np.isnan(hx[0])


# ---------------------------------------------------------------------------
# Test 5: mixed Gemeinden (some in-ring, some far) for same Kreis -> only in-ring drawn
# ---------------------------------------------------------------------------

def test_mixed_gemeinden_only_in_ring_drawn():
    """When a Kreis has both in-ring and far Gemeinden, only in-ring are drawn."""
    gem = gpd.GeoDataFrame(
        [
            _gem("15083", "15083001", 5000, 3),    # in-ring (cx=3 -> rep_pt x~3.5)
            _gem("15083", "15083002", 5000, 500),  # far (cx=500)
        ],
        crs="EPSG:25832",
    )
    inbound = pd.DataFrame({"ars5": ["15083"], "flow": [1000]})
    rng = np.random.default_rng(0)

    hx, hy, inr = incommuter_origin_homes(
        ["15083"] * 100, inbound, gem, ZGB, SOURCE_BUFFER_M, rng,
    )

    assert np.all(inr), "All agents should be in-ring (Kreis has in-ring Gemeinden)"
    # The far Gemeinde (cx=500) rep_point x ~ 500.5 -- all draws should have x < 10
    assert np.all(hx < 10), f"All draws should be from in-ring Gemeinde (x<10), max was {hx.max()}"


# ---------------------------------------------------------------------------
# Test 6: empty inbound -> all agents far
# ---------------------------------------------------------------------------

def test_empty_inbound_all_far():
    gem = gpd.GeoDataFrame(
        [_gem("15083", "15083001", 9000, 3)],
        crs="EPSG:25832",
    )
    inbound = pd.DataFrame({"ars5": pd.Series([], dtype=str), "flow": pd.Series([], dtype=int)})
    rng = np.random.default_rng(0)

    hx, hy, inr = incommuter_origin_homes(
        ["15083"], inbound, gem, ZGB, SOURCE_BUFFER_M, rng,
    )

    assert not bool(inr[0])
    assert np.isnan(hx[0])


# ---------------------------------------------------------------------------
# Test 7: zero-length orig_ars -> empty arrays returned
# ---------------------------------------------------------------------------

def test_empty_orig_ars_returns_empty_arrays():
    gem = gpd.GeoDataFrame(
        [_gem("15083", "15083001", 9000, 3)],
        crs="EPSG:25832",
    )
    inbound = pd.DataFrame({"ars5": ["15083"], "flow": [8000]})
    rng = np.random.default_rng(0)

    hx, hy, inr = incommuter_origin_homes(
        [], inbound, gem, ZGB, SOURCE_BUFFER_M, rng,
    )

    assert len(hx) == 0 and len(hy) == 0 and len(inr) == 0
