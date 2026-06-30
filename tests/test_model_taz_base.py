"""Tests for the TAZ-capable gravity model extension.

Step 1 (anchor): verify that ``evaluate_gravity`` and ``build_friction_matrix``
can be exercised on an arbitrary zone universe with TAZ-style identifiers.
This is the zone-agnostic anchor that confirms the gravity machinery does not
assume commune-id-shaped inputs, required before the TAZ branch wires into
``_execute_gravity_base``.

Step 2 (compute_work_od): verify the extracted pure helper on a hand-built
4-TAZ input, asserting the returned OD frame is row-normalised (rows sum to 1
per origin) and has the expected schema.  No ``execute()`` or pipeline stages
are involved.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np
import pandas as pd

from braunschweig.gravity.model import compute_work_od, evaluate_gravity
from braunschweig.gravity.friction import build_friction_matrix


# ---------------------------------------------------------------------------
# Anchor: evaluate_gravity is zone-agnostic (can operate over any N zones)
# ---------------------------------------------------------------------------

def test_evaluate_gravity_zone_agnostic_over_taz_universe():
    """evaluate_gravity works on an N-zone universe with arbitrary identifiers.

    This confirms the balancing loop has no dependency on commune-id format;
    it operates purely on arrays, making it reusable for TAZ-keyed inputs.
    """
    n = 4
    population = np.array([10.0, 20.0, 30.0, 40.0])
    employees = np.array([25.0, 25.0, 25.0, 25.0])
    distances = np.abs(np.subtract.outer(np.arange(n), np.arange(n))).astype(float)
    friction = build_friction_matrix(distances, np.full(n, -0.2), -2.4, 1.0)
    flow = evaluate_gravity(population, employees, friction, max_iterations=1000)
    assert flow.shape == (n, n) and np.all(flow >= 0)


# ---------------------------------------------------------------------------
# compute_work_od: pure-frame normalised-OD test on a 4-zone hand-built input
# ---------------------------------------------------------------------------

def _make_4zone_inputs():
    """Build minimal 4-zone population, employees, distances, regiostar frames."""
    zones = ["Z1", "Z2", "Z3", "Z4"]

    df_population = pd.DataFrame({
        "origin_id": zones,
        "population": [10.0, 20.0, 30.0, 40.0],
    })
    df_employees = pd.DataFrame({
        "destination_id": zones,
        "employees": [25.0, 25.0, 25.0, 25.0],
    })

    # Dense distance matrix (every pair).
    rows = []
    for o in zones:
        for d in zones:
            rows.append({
                "origin_id": o,
                "destination_id": d,
                "distance_km": float(abs(int(o[1]) - int(d[1]))),
            })
    df_distances = pd.DataFrame(rows)

    # Empty RegioStaR frame (no overrides, so the scalar slope path is taken).
    df_regiostar = pd.DataFrame(columns=["commune_id", "regiostar7"])

    return df_population, df_employees, df_distances, df_regiostar, zones


def test_compute_work_od_returns_normalised_od_per_origin():
    """compute_work_od returns a row-normalised OD (weights sum to 1 per origin).

    Uses a hand-built 4-zone input with no slope overrides or friction factors
    (both default-None, so the computation is the same as the legacy scalar path).
    rs7_by_zone=None exercises the same RS7 resolution path as the Gemeinde pass.
    """
    df_pop, df_emp, df_dist, df_regiostar, zones = _make_4zone_inputs()

    od = compute_work_od(
        df_population=df_pop,
        df_employees=df_emp,
        df_distances=df_dist,
        df_regiostar=df_regiostar,
        rs7_by_zone=None,
        slope=-0.2,
        constant=-2.4,
        diagonal=1.0,
        slope_overrides=None,
        friction_factors=None,
        max_iterations=1000,
    )

    # Schema: three columns, correct dtypes.
    assert set(od.columns) == {"origin_id", "destination_id", "weight"}

    # Every origin present in the output.
    assert set(od["origin_id"].unique()) == set(zones)

    # Weights >= 0.
    assert (od["weight"] >= 0.0).all()

    # Rows sum to 1 per origin (within the balancing convergence tolerance).
    row_sums = od.groupby("origin_id")["weight"].sum()
    assert np.allclose(row_sums, 1.0, atol=1e-4), \
        f"Row sums not all 1: {row_sums.to_dict()}"


def test_compute_work_od_with_rs7_by_zone_returns_same_shape():
    """With rs7_by_zone given but no slope_overrides the result is byte-identical.

    When slope_overrides and friction_factors are both None (popsim defaults)
    the rs7_by_zone lookup is never consulted in _build_origin_slope_vector, so
    the result should be identical to the rs7_by_zone=None call.
    """
    df_pop, df_emp, df_dist, df_regiostar, zones = _make_4zone_inputs()
    rs7_map = {z: 72 for z in zones}  # map all zones to RS7 72; inert w/o overrides

    od_without = compute_work_od(
        df_population=df_pop, df_employees=df_emp, df_distances=df_dist,
        df_regiostar=df_regiostar, rs7_by_zone=None,
        slope=-0.2, constant=-2.4, diagonal=1.0,
        slope_overrides=None, friction_factors=None, max_iterations=1000,
    )
    od_with = compute_work_od(
        df_population=df_pop, df_employees=df_emp, df_distances=df_dist,
        df_regiostar=df_regiostar, rs7_by_zone=rs7_map,
        slope=-0.2, constant=-2.4, diagonal=1.0,
        slope_overrides=None, friction_factors=None, max_iterations=1000,
    )

    # Same shape and the same row sums.
    assert od_without.shape == od_with.shape
    sums_without = od_without.groupby("origin_id")["weight"].sum().sort_index()
    sums_with = od_with.groupby("origin_id")["weight"].sum().sort_index()
    assert np.allclose(sums_without, sums_with, atol=1e-6)
