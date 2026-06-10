"""Conservation / volume regression guards for cross-cordon commuters.

A prior bug silently zeroed out-commuters via a gravity destination-id key mismatch:
``_append_outbound_flows`` emitted ``destination_id`` values that could not be resolved
against the work-pool ``commune_id``, so the entire outbound mass was lost without any
error or warning.  These tests assert in/out commuter VOLUMES are conserved end-to-end
and that the gravity->work-pool join can always resolve.

Guards:
  1. test_incommuter_count_conserved_per_kreis      -- expand_to_agents * select_inbound_flows
  2. test_incommuter_no_agent_dropped_in_build_frames -- build_incommuter_frames round-trip
  3. test_outcommuter_ext_volume_conserved_per_kreis  -- build_external_workplaces Kreis sum
  4. test_outcommuter_below_threshold_dropped         -- min_flow hard-drop (no redistribution)
  5. test_gravity_injection_conserves_outbound_and_matches_pool -- _append_outbound_flows join
  6. test_largest_remainder_conserves_total           -- _largest_remainder sum invariant
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import geopandas as gpd  # noqa: E402
from shapely.geometry import Point, Polygon  # noqa: E402

from braunschweig.data.cordon.demand import (  # noqa: E402
    expand_to_agents,
    select_inbound_flows,
)
from braunschweig.data.cordon.external_points import _largest_remainder  # noqa: E402
from braunschweig.data.cordon.mode_balancer import balance_incommuter_modes  # noqa: E402
from braunschweig.data.external_workplaces import build_external_workplaces  # noqa: E402
from braunschweig.gravity.model import _append_outbound_flows  # noqa: E402
from braunschweig.synthesis.incommuters import build_incommuter_frames  # noqa: E402


# ---------------------------------------------------------------------------
# Shared fixture builders (copied minimal pattern from tests/test_incommuters.py)
# ---------------------------------------------------------------------------

def _minimal_inputs():
    """Minimal shared inputs for build_incommuter_frames (from test_incommuters.py)."""
    gates = gpd.GeoDataFrame(
        {"gate_id": ["gate_0000"], "capacity": [8000.0], "road_class": ["motorway"]},
        geometry=[Point(605000, 5840000)], crs="EPSG:25832")
    assignment = pd.DataFrame([("03241", "gate_0000", 1000, 0)],
                              columns=["ars5", "gate_id", "inbound", "outbound"])
    flows = pd.DataFrame([("03241", "03101", 1000)],
                         columns=["orig_ars", "dest_ars", "flow"])
    zgb_work = gpd.GeoDataFrame(
        {"location_id": ["work_1"], "commune_id": ["03101000"], "employees": [50]},
        geometry=[Point(606000, 5805000)], crs="EPSG:25832")
    hts_persons = pd.DataFrame({"person_id": [1], "employed": [True], "age": [42],
                                "sex": ["female"]})
    hts_trips = pd.DataFrame([
        (1, "home", "work", 7 * 3600.0, 8 * 3600.0),
        (1, "work", "home", 17 * 3600.0, 18 * 3600.0),
    ], columns=["person_id", "preceding_purpose", "following_purpose",
                "departure_time", "arrival_time"])
    return gates, assignment, flows, zgb_work, hts_persons, hts_trips


def _gem(ars5, gem_ags, ewz, cx):
    """One-row Gemeinde dict helper (matches test_external_workplaces.py)."""
    return {
        "ars5": ars5,
        "gem_ags": gem_ags,
        "ewz": ewz,
        "geometry": Polygon([(cx, 0), (cx + 1, 0), (cx + 1, 1), (cx, 1)]),
    }


# ---------------------------------------------------------------------------
# Guard 1: in-commuter count conserved per Kreis
# ---------------------------------------------------------------------------

def test_incommuter_count_conserved_per_kreis():
    """expand_to_agents(select_inbound_flows(...)) yields round(flow*rate) per origin Kreis.

    Uses a synthetic flows table with 3 external Kreise inbound to ZGB-8, ZGB-internal
    rows (must NOT appear), and out-of-ring rows (must NOT appear).  After filtering and
    expansion every in-scope inbound row produces exactly round(flow * rate) agents,
    and the total agent count equals the sum of per-row counts.
    """
    ZGB = {"03101", "03102"}
    RING = {"03241", "03153", "03158"}   # ring Kreise that are not ZGB

    flows = pd.DataFrame([
        # In-scope inbound (ring -> ZGB): should be expanded
        ("03241", "03101", 100),   # 100 * 0.25 = 25 agents
        ("03153", "03102", 200),   # 200 * 0.25 = 50 agents
        ("03158", "03101",  50),   #  50 * 0.25 = 12.5 -> round() = 12 (banker's: .5 rounds to even)
        # ZGB-internal (origin in ZGB): must be excluded
        ("03101", "03102", 500),
        # Origin in ring but destination not in ZGB: must be excluded
        ("03241", "09999",  80),
        # Origin not in ring at all: must be excluded
        ("09162", "03101", 300),
    ], columns=["orig_ars", "dest_ars", "flow"])

    rate = 0.25
    inbound = select_inbound_flows(flows, ZGB, RING)
    agents = expand_to_agents(inbound, rate)

    # Only the 3 in-scope rows should appear
    assert set(inbound["orig_ars"]) == {"03241", "03153", "03158"}, \
        "select_inbound_flows must keep only ring->ZGB rows that are not ZGB-internal"

    # Per-row count matches np.round(flow * rate) -- banker's rounding
    for _, row in inbound.iterrows():
        expected_count = int(np.round(row["flow"] * rate))
        actual_count = int((agents["orig_ars"] == row["orig_ars"]).sum())
        assert actual_count == expected_count, (
            f"Kreis {row['orig_ars']}: expected {expected_count} agents "
            f"(round({row['flow']} * {rate})), got {actual_count}"
        )

    # Total agent count equals sum of per-row counts
    per_row_total = int(sum(np.round(inbound["flow"].to_numpy() * rate)))
    assert len(agents) == per_row_total, (
        f"Total agents {len(agents)} != sum of per-row counts {per_row_total}"
    )


# ---------------------------------------------------------------------------
# Guard 2: no agent dropped in build_incommuter_frames
# ---------------------------------------------------------------------------

def test_incommuter_no_agent_dropped_in_build_frames():
    """build_incommuter_frames produces exactly as many persons as expand_to_agents predicts.

    Also asserts that len(validation) == len(persons): the validation frame
    must have exactly one row per injected in-commuter.
    """
    gates, assignment, flows, zgb_work, hts_persons, hts_trips = _minimal_inputs()
    rate = 0.05
    ZGB = {"03101"}

    # Pre-compute expected agent count: what select + expand gives.
    inbound = select_inbound_flows(flows, ZGB, in_ring_kreise=set(assignment["ars5"]))
    expected_n = len(expand_to_agents(inbound, rate))

    rng = np.random.default_rng(42)
    frames = build_incommuter_frames(
        flows=flows, zgb_kreise=ZGB, sampling_rate=rate,
        gates=gates, assignment=assignment, zgb_work=zgb_work,
        mode_reference={">=10": {"car": 1.0}}, band_edges=(10,),
        hts_persons=hts_persons, hts_trips=hts_trips, person_col="person_id",
        n_residents=100, n_resident_households=40, rng=rng, gate_speed_kmh=30.0,
    )

    n_persons = len(frames["persons"])
    n_validation = len(frames["validation"])

    assert n_persons == expected_n, (
        f"build_incommuter_frames produced {n_persons} persons but "
        f"expand_to_agents predicted {expected_n}. "
        "An agent was silently dropped between demand expansion and person assembly."
    )
    assert n_validation == n_persons, (
        f"validation frame has {n_validation} rows but persons has {n_persons}. "
        "Every in-commuter must have exactly one validation row."
    )


# ---------------------------------------------------------------------------
# Guard 3: out-commuter EXT volume conserved per Kreis (largest-remainder exact)
# ---------------------------------------------------------------------------

def test_outcommuter_ext_volume_conserved_per_kreis():
    """build_external_workplaces preserves each Kreis's total outbound exactly.

    Two Kreise above min_flow, one below.  For each Kreis above threshold,
    sum(employees) over its Gemeinde points must equal the BA outbound for that Kreis.
    """
    gemeinden = gpd.GeoDataFrame([
        _gem("03241", "03241001", 60000, 0),
        _gem("03241", "03241002", 40000, 9),
        _gem("03153", "03153001", 30000, 50),
        _gem("03153", "03153002", 70000, 60),
        # Below-threshold Kreis: has Gemeinden but flow is too small
        _gem("03158", "03158001", 100000, 100),
    ], crs="EPSG:25832")

    outbound = pd.DataFrame({
        "ars5":      ["03241", "03153", "03158"],
        "employees": [1000,    200,     30],     # 30 < 50 = min_flow
    })

    df = build_external_workplaces(outbound, gemeinden, min_flow=50)

    for ars5, expected_total in [("03241", 1000), ("03153", 200)]:
        kreis_rows = df[df["ars5"] == ars5]
        actual_total = int(kreis_rows["employees"].sum())
        assert actual_total == expected_total, (
            f"Kreis {ars5}: expected {expected_total} employees, "
            f"got {actual_total} (largest-remainder not exact)."
        )


# ---------------------------------------------------------------------------
# Guard 4: below-threshold Kreis is dropped, NOT redistributed
# ---------------------------------------------------------------------------

def test_outcommuter_below_threshold_dropped():
    """A Kreis with outbound < min_flow yields NO EXT workplaces.

    Its mass must NOT be silently redistributed to other Kreise.  The test uses
    only one Kreis (the below-threshold one), so any output would mean redistribution.
    """
    gemeinden = gpd.GeoDataFrame([
        _gem("03241", "03241001", 100000, 0),
    ], crs="EPSG:25832")

    outbound = pd.DataFrame({"ars5": ["03241"], "employees": [49]})  # below 50

    df = build_external_workplaces(outbound, gemeinden, min_flow=50)

    assert len(df) == 0, (
        f"Kreis 03241 with outbound 49 < min_flow 50 produced {len(df)} rows. "
        "The mass must be dropped, not redistributed."
    )
    assert df["employees"].sum() == 0, (
        "Below-threshold Kreis must have zero total employees in output."
    )


# ---------------------------------------------------------------------------
# Guard 5: gravity injection preserves outbound join and employee-share split
# ---------------------------------------------------------------------------

def test_gravity_injection_conserves_outbound_and_matches_pool():
    """_append_outbound_flows emits destination_ids resolvable by the work pool.

    Setup: one ZGB origin Gemeinde (commune_id "03101001", Kreis "03101") with
    one external Kreis "03241" having 2 Gemeinde EXT points with a 3:1 employee
    split.  The Pendler table has a ZGB->external flow.

    Assertions:
      (a) Bug guard: every ``destination_id`` emitted by _append_outbound_flows starts
          with "EXT" and is one of the df_external commune_ids -> the join can resolve.
      (b) The ratio of injected weight to the two EXT destinations matches their
          employee-share ratio (3:1), because _append_outbound_flows scales by
          gem_share = employees / sum(employees in Kreis).
    """
    # ZGB origin Gemeinde row (commune_id must be 8-char AGS)
    df_population = pd.DataFrame({
        "commune_id": ["03101001"],
        "weight": [1000.0],
    })

    # Minimal intra-ZGB OD (needed by _append_outbound_flows as base df_od)
    # One origin -> same destination (self-loop), weight=1.0 (row-normalised).
    df_od = pd.DataFrame({
        "origin_id":      ["03101001"],
        "destination_id": ["03101001"],
        "flow":           [1000.0],
    })

    # BA Pendler: ZGB Kreis 03101 -> external Kreis 03241, 400 SvB
    df_pendler = pd.DataFrame({
        "orig_ars": ["03101"],
        "dest_ars": ["03241"],
        "flow":     [400],
    })

    # EXT workplaces: Kreis 03241 has 2 Gemeinden with 300+100=400 employees (3:1)
    df_external = gpd.GeoDataFrame({
        "ars5":       ["03241",        "03241"],
        "commune_id": ["EXT03241001",  "EXT03241002"],
        "employees":  [300,            100],
        "ewz":        [60000.0,        40000.0],
    }, geometry=[Point(0, 0), Point(1, 0)], crs="EPSG:25832")

    scope = ["03101"]

    result = _append_outbound_flows(df_od, df_population, df_pendler, df_external, scope)

    # (a) Bug guard: all EXT destination_ids in the result must be in df_external.
    ext_commune_ids = set(df_external["commune_id"].astype(str))
    ext_rows = result[result["destination_id"].astype(str).str.startswith("EXT")]

    assert len(ext_rows) > 0, (
        "_append_outbound_flows produced no EXT destination rows for "
        "a ZGB->external Pendler flow. The outbound mass was lost entirely."
    )

    unresolvable = set(ext_rows["destination_id"].astype(str)) - ext_commune_ids
    assert len(unresolvable) == 0, (
        f"_append_outbound_flows emitted destination_id(s) {unresolvable} that are "
        "NOT in df_external commune_ids. The downstream work-pool join would fail "
        "(this is the prior bug: EXT<gem_ags> vs commune_id mismatch)."
    )

    # (b) Employee-share ratio check: weight(EXT03241001) / weight(EXT03241002) ≈ 3.
    #     _append_outbound_flows normalises weight per origin, so we compare the
    #     weight sums of the two EXT destinations (same origin, so ratio is preserved).
    w1 = float(result.loc[
        result["destination_id"].astype(str) == "EXT03241001", "weight"
    ].sum())
    w2 = float(result.loc[
        result["destination_id"].astype(str) == "EXT03241002", "weight"
    ].sum())

    assert w1 > 0 and w2 > 0, (
        f"One EXT destination received zero weight: w(EXT03241001)={w1}, "
        f"w(EXT03241002)={w2}. The employee-share split failed."
    )

    ratio = w1 / w2
    expected_ratio = 300.0 / 100.0  # 3:1 employee split
    assert abs(ratio - expected_ratio) < 0.05, (
        f"EXT destination weight ratio {ratio:.4f} deviates from the expected "
        f"employee-share ratio {expected_ratio:.4f} (300:100 = 3.0). "
        "The gem_share split in _append_outbound_flows is not conserving the "
        "per-Gemeinde employee proportions."
    )


# ---------------------------------------------------------------------------
# Guard 6: _largest_remainder conserves total exactly
# ---------------------------------------------------------------------------

def test_largest_remainder_conserves_total():
    """_largest_remainder(total, weights) sums exactly to total for non-divisible cases.

    Also checks the zero/non-positive boundary: total=0 or sum(weights)=0 -> all zeros.
    """
    cases = [
        (10, [1, 1, 1]),         # 10/3 is not integer: result must sum to 10
        (7,  [3, 1, 1]),         # 7/5 * weights; must sum to 7
        (100, [2, 3, 5]),        # clean 10:15:25 weights -> 20:30:50; sum=100
        (1,  [1, 1, 1]),         # fractional each: exactly 1 total
        (3,  [1, 1, 1]),         # trivially divisible: [1,1,1]
    ]
    for total, weights in cases:
        result = _largest_remainder(total, weights)
        result_sum = int(result.sum())
        assert result_sum == total, (
            f"_largest_remainder({total}, {weights}) summed to {result_sum}, "
            f"expected {total}. Largest-remainder invariant violated."
        )
        assert all(v >= 0 for v in result), (
            f"_largest_remainder({total}, {weights}) produced negative values: {result}"
        )

    # Boundary: total=0 -> all zeros
    z = _largest_remainder(0, [1, 2, 3])
    assert int(z.sum()) == 0, \
        "_largest_remainder(0, weights) must return all zeros"

    # Boundary: zero-sum weights -> all zeros regardless of total
    z2 = _largest_remainder(10, [0, 0, 0])
    assert int(z2.sum()) == 0, \
        "_largest_remainder(total, [0,0,0]) must return all zeros (no positive weights)"


# ---------------------------------------------------------------------------
# Guard 7: balancer conserves global PT target when the pool suffices
# ---------------------------------------------------------------------------

def test_balancer_conserves_global_pt_target():
    """balance_incommuter_modes yields n_pt_final == n_pt_target when pool is large enough.

    Synthetic case:
      - 8 agents, Mikrozensus assigns 3 pt
      - 2 of those 3 cannot board PT (forced to car) -> n_forced = 2
      - 5 car agents, 4 of them reachable (flexible) -> pool >= forced
      - Expected: n_pt_final == 3 (target restored), residual == 0
    """
    modes_mikro = ["pt", "pt", "pt", "car", "car", "car", "car", "car"]
    can_board_pt = [True, False, False, True, True, True, True, False]
    pt_propensity = [0.8, 0.8, 0.8, 0.7, 0.6, 0.5, 0.4, 0.1]

    rng = np.random.default_rng(55)
    modes_final, stats = balance_incommuter_modes(
        modes_mikro, can_board_pt, pt_propensity, rng)

    assert stats["n_pt_final"] == stats["n_pt_target"], (
        f"Balancer failed to conserve global PT target: "
        f"n_pt_final={stats['n_pt_final']}, n_pt_target={stats['n_pt_target']}. "
        "residual=" + str(stats["residual"])
    )
    assert stats["residual"] == 0, (
        f"Unexpected residual {stats['residual']} when pool was sufficient."
    )
    assert int((modes_final == "pt").sum()) == stats["n_pt_target"]


def test_incommuter_count_scales_linearly_with_sampling_rate():
    """In-commuter agents scale linearly with sampling_rate (= the MATSim sample).

    Guards that in-commuters and out-commuters end up at the SAME sampling_rate: the
    in-commuter count is expand_to_agents(flow * sampling_rate) (this test), while
    out-commuters are sampled residents (no separate scaling -- they are the
    sampling_rate-sampled resident population by construction). A regression that
    de-scaled in-commuters (e.g. dropped the sampling_rate factor) would inject
    full-SvB in-commuters against a sampled resident population.
    """
    flows = pd.DataFrame({
        "orig_ars": ["15083", "15003"],
        "dest_ars": ["03101", "03101"],
        "flow": [8000, 2000],   # total 10000
    })
    # np.round (banker's) on each per-row flow*rate, summed.
    for rate, expected in [(0.01, 100), (0.05, 500), (0.25, 2500), (1.0, 10000)]:
        n = len(expand_to_agents(flows, rate))
        assert n == expected, f"rate {rate}: got {n} in-commuter agents, expected {expected}"
    # Strictly increasing with the rate (monotone scaling).
    counts = [len(expand_to_agents(flows, r)) for r in (0.01, 0.1, 0.5, 1.0)]
    assert counts == sorted(counts) and counts[0] < counts[-1]
