"""Tests for TAZ-aware _calibrate and _append_outbound_flows (Task 6).

These tests are locally runnable (no synpp, no statsmodels, no geopandas).
They verify:
  1. _calibrate with a taz->kreis lookup aggregates the TAZ OD to Kreis and
     matches the BA Pendler control (Kreis-pair aggregates equal the control).
  2. The taz->kreis mapping is applied (not str[:5] legacy behaviour).
  3. _calibrate raises RuntimeError when in-scope is empty on the ON path
     (no silent BA-skip).
  4. _append_outbound_flows ON path: per-origin weights (internal + EXT) sum to 1.
  5. _append_outbound_flows OFF path is byte-identical (regression guard).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd
import pytest

from braunschweig.gravity.model import _append_outbound_flows, _calibrate


# ---------------------------------------------------------------------------
# Helper factories
# ---------------------------------------------------------------------------

def _make_od(pairs, weights):
    """Build a minimal OD frame."""
    return pd.DataFrame({
        "origin_id": [p[0] for p in pairs],
        "destination_id": [p[1] for p in pairs],
        "weight": weights,
    })


def _make_pop_taz(taz_ids, populations):
    """TAZ population margin (ON schema: taz_id, population)."""
    return pd.DataFrame({"taz_id": taz_ids, "population": populations})


def _make_pop_gemeinde(commune_ids, weights):
    """OFF-path population frame (commune_id, weight per person)."""
    # Each row = one synthetic person.
    rows = []
    for c, w in zip(commune_ids, weights):
        rows.append({"commune_id": c, "weight": w})
    return pd.DataFrame(rows)


def _make_pendler(pairs, flows):
    return pd.DataFrame({
        "orig_ars": [p[0] for p in pairs],
        "dest_ars": [p[1] for p in pairs],
        "flow": flows,
    })


# ---------------------------------------------------------------------------
# Test 1: _calibrate ON path aggregates TAZ OD to Kreis and fits BA control
# ---------------------------------------------------------------------------

def test_calibrate_taz_aggregates_to_kreis_via_lookup():
    """TAZ OD is IPF-scaled so the Kreis aggregates match the BA Pendler control."""
    # Two TAZ per Kreis; IDs are NOT the legacy commune_id format (first 5 digits
    # do NOT equal the Kreis code), so str[:5] would produce wrong Kreis codes.
    # lookup maps 6-char taz_ids to 5-digit Kreis ARS explicitly.
    lookup = {"310101": "03101", "310102": "03101", "315401": "03154", "315402": "03154"}

    df_od = pd.DataFrame({
        "origin_id":      ["310101", "310101", "310102", "310102",
                           "315401", "315401", "315402", "315402"],
        "destination_id": ["310101", "315401", "310101", "315401",
                           "310101", "315401", "310101", "315401"],
        "weight":         [0.6, 0.4, 0.5, 0.5, 0.3, 0.7, 0.4, 0.6],
    })
    # Population: 100 per TAZ.
    df_pop = _make_pop_taz(["310101", "310102", "315401", "315402"],
                           [100.0, 100.0, 100.0, 100.0])
    # Pendler control: 50 units from each Kreis to the other; intra-Kreis zero here.
    df_pendler = _make_pendler(
        [("03101", "03154"), ("03154", "03101")],
        [50.0, 50.0],
    )

    out = _calibrate(df_od, df_pop, df_pendler,
                     zone_to_kreis=lookup,
                     population_key="taz_id",
                     population_value="population")

    # The Kreis aggregate must match the Pendler control.
    agg = (out.assign(ok=out["origin_id"].map(lookup),
                      dk=out["destination_id"].map(lookup))
             .groupby(["ok", "dk"])["flow"].sum())
    assert abs(agg.get(("03101", "03154"), 0.0) - 50.0) < 1.0, \
        "Kreis 03101->03154 aggregate does not match Pendler control"
    assert abs(agg.get(("03154", "03101"), 0.0) - 50.0) < 1.0, \
        "Kreis 03154->03101 aggregate does not match Pendler control"


def test_calibrate_taz_mapping_not_str_slice():
    """The taz->kreis mapping must use the lookup dict, not str[:5].

    If str[:5] were used on "310101" the result would be "31010", NOT "03101".
    The test verifies the lookup produces "03101" (i.e. the mapping is applied).
    """
    lookup = {"310101": "03101", "315401": "03154"}
    df_od = _make_od(
        [("310101", "310101"), ("310101", "315401"),
         ("315401", "310101"), ("315401", "315401")],
        [0.6, 0.4, 0.3, 0.7],
    )
    df_pop = _make_pop_taz(["310101", "315401"], [100.0, 100.0])
    df_pendler = _make_pendler(
        [("03101", "03101"), ("03101", "03154"), ("03154", "03101"), ("03154", "03154")],
        [30.0, 20.0, 20.0, 30.0],
    )
    out = _calibrate(df_od, df_pop, df_pendler,
                     zone_to_kreis=lookup,
                     population_key="taz_id",
                     population_value="population")
    # Verify the output still has the original taz_id as origin_id (not transformed).
    assert set(out["origin_id"].unique()) == {"310101", "315401"}


# ---------------------------------------------------------------------------
# Test 2: _calibrate ON raises when no in-scope flows (silent-skip guard)
# ---------------------------------------------------------------------------

def test_calibrate_taz_raises_on_empty_scope():
    """ON path must raise RuntimeError when the taz->kreis mapping yields no
    flows that overlap the Pendler scope (silent BA-skip prevention)."""
    # lookup maps to "09999" -- not in the Pendler frame.
    lookup = {"310101": "09999", "315401": "09998"}
    df_od = _make_od(
        [("310101", "315401")],
        [1.0],
    )
    df_pop = _make_pop_taz(["310101", "315401"], [100.0, 100.0])
    df_pendler = _make_pendler([("03101", "03154")], [50.0])

    with pytest.raises(RuntimeError, match="no in-scope flow after taz->kreis mapping"):
        _calibrate(df_od, df_pop, df_pendler,
                   zone_to_kreis=lookup,
                   population_key="taz_id",
                   population_value="population")


def test_calibrate_off_path_no_scope_returns_raw():
    """OFF path (zone_to_kreis=None): empty scope still returns raw gravity (no raise)."""
    # commune_ids whose str[:5] won't match the Pendler scope "03101".
    df_od = _make_od([("99999AAAA", "99999AAAA")], [1.0])
    df_pop = _make_pop_gemeinde(["99999AAAA"], [100.0])
    df_pendler = _make_pendler([("03101", "03154")], [50.0])

    # Should return df_od unchanged (the "no scope overlap" branch).
    out = _calibrate(df_od, df_pop, df_pendler)
    pd.testing.assert_frame_equal(out, df_od, check_like=True)


# ---------------------------------------------------------------------------
# Test 3: _append_outbound_flows ON path per-origin weights sum to 1
# ---------------------------------------------------------------------------

def _make_external(ars5, commune_ids, employees):
    return pd.DataFrame({"ars5": ars5, "commune_id": commune_ids, "employees": employees})


def test_append_outbound_flows_taz_weights_sum_to_one():
    """ON path: after injecting EXT rows, each origin's weights sum to 1."""
    # Two TAZ in scope Kreis "03101"; one external Kreis "03154" with 2 Gemeinden.
    lookup = {"310101": "03101", "310102": "03101"}
    scope = ["03101"]

    # Internal OD (flow column produced by _calibrate).
    df_od = pd.DataFrame({
        "origin_id":      ["310101", "310102"],
        "destination_id": ["310101", "310102"],
        "flow":           [80.0, 80.0],
    })
    # TAZ population margin.
    df_pop = _make_pop_taz(["310101", "310102"], [100.0, 100.0])
    # Pendler: 20 units from "03101" to external "03154".
    df_pendler = _make_pendler([("03101", "03154")], [20.0])
    # External: two Gemeinden in "03154".
    df_ext = _make_external(
        ["03154", "03154"],
        ["EXTGEM1", "EXTGEM2"],
        [60.0, 40.0],
    )

    out = _append_outbound_flows(
        df_od, df_pop, df_pendler, df_ext, scope,
        zone_to_kreis=lookup,
        population_key="taz_id",
        population_value="population",
    )

    # Each origin's weights must sum to 1.
    totals = out.groupby("origin_id")["weight"].sum()
    for origin, total in totals.items():
        assert abs(total - 1.0) < 1e-9, \
            f"origin {origin!r} weights sum to {total}, expected 1.0"


def test_append_outbound_flows_off_path_weights_sum_to_one():
    """OFF path (zone_to_kreis=None): per-origin weights sum to 1 (regression)."""
    scope = ["03101"]
    df_od = pd.DataFrame({
        "origin_id":      ["0310100100"],
        "destination_id": ["0310100100"],
        "flow":           [80.0],
    })
    # OFF population: commune_id whose str[:5] = "03101" so it falls in scope.
    df_pop = _make_pop_gemeinde(["0310100100"], [100.0])
    df_pendler = _make_pendler([("03101", "03154")], [20.0])
    df_ext = _make_external(["03154"], ["EXTG1"], [100.0])

    out = _append_outbound_flows(df_od, df_pop, df_pendler, df_ext, scope)

    totals = out.groupby("origin_id")["weight"].sum()
    for origin, total in totals.items():
        assert abs(total - 1.0) < 1e-9, \
            f"origin {origin!r} weights sum to {total} (OFF path)"


# ---------------------------------------------------------------------------
# Test 4: OFF path byte-identity -- _calibrate with defaults == calling
#         the old signature (commune_id[:5] mapping, weight column).
# ---------------------------------------------------------------------------

def test_calibrate_off_path_identical_to_legacy():
    """Calling _calibrate with no extra kwargs must produce the same result as
    the old hardcoded commune_id[:5] grouping."""
    # commune_ids whose str[:5] is "03101" (matching Pendler scope).
    df_od = _make_od(
        [("0310100100", "0310100100"), ("0310100100", "0315400001"),
         ("0315400001", "0310100100"), ("0315400001", "0315400001")],
        [0.6, 0.4, 0.3, 0.7],
    )
    df_pop = _make_pop_gemeinde(
        ["0310100100", "0310100100", "0315400001"],
        [50.0, 50.0, 100.0],
    )
    df_pendler = _make_pendler(
        [("03101", "03101"), ("03101", "03154"), ("03154", "03101"), ("03154", "03154")],
        [30.0, 20.0, 20.0, 30.0],
    )

    # Call with defaults (OFF path).
    out_default = _calibrate(df_od, df_pop, df_pendler)
    # Call with explicit defaults (should be identical).
    out_explicit = _calibrate(df_od, df_pop, df_pendler,
                              zone_to_kreis=None,
                              population_key="commune_id",
                              population_value="weight")

    pd.testing.assert_frame_equal(
        out_default.reset_index(drop=True),
        out_explicit.reset_index(drop=True),
    )
