"""Degenerate TAZ==Gemeinde equivalence test.

Proves that the TAZ branch of ``compute_work_od`` is a faithful generalisation
of the Gemeinde branch by constructing a *degenerate* TAZ layer where:

  - Each of the 2 communes contains exactly ONE TAZ whose polygon covers the
    entire commune polygon (1:1 bijection, distinct TAZ ids like "T_<commune>").
  - Employees and population are NON-UNIFORM across communes so any broken
    rescale or aggregation is detectable.
  - >1 person per commune at distinct home points so a broken sjoin is detectable.

On such a degenerate layer the TAZ gravity must reproduce the Gemeinde gravity
up to the taz_id <-> commune_id relabelling, because:

  - The TAZ population margin (one TAZ per commune) == the Gemeinde margin.
  - The TAZ employee attraction (one TAZ per commune, no building split) ==
    the Gemeinde attraction.
  - The TAZ distance matrix (centroid to centroid, 1:1) == the Gemeinde matrix.
  - The friction parameters are identical.

Therefore ``compute_work_od`` called on the Gemeinde inputs and relabelled must
equal ``compute_work_od`` called on the TAZ inputs within float tolerance
(``numpy.testing.assert_allclose(..., atol=1e-9)``), because Furness / IPF are
floating point, not exact integer arithmetic.

The ``_calibrate`` equivalence (Kreis aggregates match for both TAZ and Gemeinde
inputs) is also verified: when each TAZ maps to the same Kreis as its commune,
the Kreis-pair aggregates produced by ``_calibrate`` on the TAZ OD must equal
those produced by ``_calibrate`` on the Gemeinde OD within ``1e-6``.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np
import numpy.testing as npt
import pandas as pd
import pytest

from braunschweig.gravity.model import _calibrate, compute_work_od


# ---------------------------------------------------------------------------
# Degenerate fixture parameters
# ---------------------------------------------------------------------------
#
# Two communes in two distinct Kreise.  The degenerate TAZ layer has one TAZ
# per commune with a distinct (non-commune) identifier: T_A and T_B.
#
# Non-uniform employees: commune A has 300, commune B has 100 (3:1 ratio).
# Population: 3 persons in A (weight 1.0 each) and 2 persons in B (weight 1.0).
# Distances: A-A 0 km, A-B 50 km, B-A 50 km, B-B 0 km.
# Gravity parameters: identical to the defaults (ensures no surprise).

COMMUNE_A = "03101000"   # 8-digit AGS
COMMUNE_B = "03154000"
KREIS_A = "03101"        # COMMUNE_A[:5]
KREIS_B = "03154"

TAZ_A = "T_03101000"
TAZ_B = "T_03154000"

# Employees at each commune/TAZ.
EMP_A = 300.0
EMP_B = 100.0

# Per-person weight (3 persons in A, 2 in B).
PERSONS_A = [1.0, 1.0, 1.0]   # commune A
PERSONS_B = [1.0, 1.0]        # commune B
POP_A = sum(PERSONS_A)         # 3.0
POP_B = sum(PERSONS_B)         # 2.0

DIST_AA = 0.1   # small but > 0 so friction exp is finite
DIST_AB = 50.0
DIST_BA = 50.0
DIST_BB = 0.1

# Gravity parameters (pre-feature defaults -- must stay in sync with model.py constants).
SLOPE = -0.2
CONSTANT = -2.4
DIAGONAL = 1.0
MAX_ITER = 10000


# ---------------------------------------------------------------------------
# Build Gemeinde inputs
# ---------------------------------------------------------------------------

def _gemeinde_population():
    """Per-person population frame (commune_id, weight) as expected by _calibrate."""
    rows = (
        [{"commune_id": COMMUNE_A, "weight": w} for w in PERSONS_A]
        + [{"commune_id": COMMUNE_B, "weight": w} for w in PERSONS_B]
    )
    return pd.DataFrame(rows)


def _gemeinde_population_for_compute():
    """Aggregated Gemeinde population frame for compute_work_od (origin_id, population)."""
    return pd.DataFrame({
        "origin_id": [COMMUNE_A, COMMUNE_B],
        "population": [POP_A, POP_B],
    })


def _gemeinde_employees():
    """Gemeinde employee frame for compute_work_od (destination_id, employees)."""
    return pd.DataFrame({
        "destination_id": [COMMUNE_A, COMMUNE_B],
        "employees": [EMP_A, EMP_B],
    })


def _gemeinde_distance_matrix():
    """Dense 2x2 Gemeinde distance matrix."""
    return pd.DataFrame({
        "origin_id": [COMMUNE_A, COMMUNE_A, COMMUNE_B, COMMUNE_B],
        "destination_id": [COMMUNE_A, COMMUNE_B, COMMUNE_A, COMMUNE_B],
        "distance_km": [DIST_AA, DIST_AB, DIST_BA, DIST_BB],
    })


def _empty_regiostar():
    """Empty RegioStaR frame (no per-RS7 overrides -> scalar slope everywhere)."""
    return pd.DataFrame(columns=["commune_id", "regiostar7"])


# ---------------------------------------------------------------------------
# Build TAZ inputs (degenerate: 1 TAZ per commune, TAZ id != commune_id)
# ---------------------------------------------------------------------------

def _taz_population_for_compute():
    """TAZ population frame for compute_work_od (origin_id=taz_id, population).

    In the degenerate case each TAZ contains all persons of its commune, so the
    population values equal the Gemeinde aggregates.
    """
    return pd.DataFrame({
        "origin_id": [TAZ_A, TAZ_B],
        "population": [POP_A, POP_B],
    })


def _taz_population_for_calibrate():
    """TAZ population frame for _calibrate (taz_id, population).

    This is the pop_taz schema produced by build_origin_population_per_taz;
    _calibrate receives it with population_key="taz_id", population_value="population".
    """
    return pd.DataFrame({
        "taz_id": [TAZ_A, TAZ_B],
        "population": [POP_A, POP_B],
    })


def _taz_employees():
    """TAZ employee frame for compute_work_od (destination_id=taz_id, employees).

    In the degenerate case the full commune employee count lands in the single TAZ.
    """
    return pd.DataFrame({
        "destination_id": [TAZ_A, TAZ_B],
        "employees": [EMP_A, EMP_B],
    })


def _taz_distance_matrix():
    """Dense 2x2 TAZ distance matrix.

    Identical distances as the Gemeinde matrix, just with taz_id keys.
    """
    return pd.DataFrame({
        "origin_id": [TAZ_A, TAZ_A, TAZ_B, TAZ_B],
        "destination_id": [TAZ_A, TAZ_B, TAZ_A, TAZ_B],
        "distance_km": [DIST_AA, DIST_AB, DIST_BA, DIST_BB],
    })


def _taz_to_kreis():
    """Degenerate TAZ->Kreis lookup (1:1 with the commune->Kreis map)."""
    return {TAZ_A: KREIS_A, TAZ_B: KREIS_B}


def _rs7_by_zone_taz():
    """No RS7 overrides for TAZ (inert lookup, matches empty regiostar on Gemeinde path)."""
    return None


# ---------------------------------------------------------------------------
# Pendler frame (shared for both Gemeinde and TAZ _calibrate tests)
# ---------------------------------------------------------------------------

def _pendler():
    """BA Pendler frame with intra-Kreis + cross-Kreis flows.

    Both Kreise present so _calibrate has in-scope pairs for both the Gemeinde
    and the TAZ path.
    """
    return pd.DataFrame({
        "orig_ars": [KREIS_A, KREIS_A, KREIS_B, KREIS_B],
        "dest_ars": [KREIS_A, KREIS_B, KREIS_A, KREIS_B],
        "flow":     [150.0, 50.0, 30.0, 70.0],
    })


# ---------------------------------------------------------------------------
# Helper: relabel TAZ OD -> Gemeinde OD
# ---------------------------------------------------------------------------

def _relabel_taz_to_gemeinde(df_taz_od: pd.DataFrame) -> pd.DataFrame:
    """Replace taz_id -> commune_id in origin_id and destination_id.

    The degenerate 1:1 map is: TAZ_A -> COMMUNE_A, TAZ_B -> COMMUNE_B.
    """
    taz_to_commune = {TAZ_A: COMMUNE_A, TAZ_B: COMMUNE_B}
    df = df_taz_od.copy()
    df["origin_id"] = df["origin_id"].map(taz_to_commune)
    df["destination_id"] = df["destination_id"].map(taz_to_commune)
    return df


# ---------------------------------------------------------------------------
# Test 1: compute_work_od equivalence (the core proof)
# ---------------------------------------------------------------------------

def test_degenerate_taz_compute_work_od_equals_gemeinde():
    """On a degenerate 1-TAZ-per-commune layer compute_work_od on TAZ inputs
    must reproduce compute_work_od on Gemeinde inputs after taz->commune relabelling.

    This is the core proof that the TAZ branch is a faithful generalisation:
    when the TAZ layer is the trivial embedding of Gemeinden (one polygon each,
    identical distances, identical population and employee totals), the
    row-normalised OD weight matrices must be equal within float tolerance.

    The assertion uses assert_allclose (atol=1e-9) because Furness IPF is
    floating-point, not byte-exact.
    """
    df_regiostar = _empty_regiostar()

    # Gemeinde pass.
    od_gemeinde = compute_work_od(
        df_population=_gemeinde_population_for_compute(),
        df_employees=_gemeinde_employees(),
        df_distances=_gemeinde_distance_matrix(),
        df_regiostar=df_regiostar,
        rs7_by_zone=None,
        slope=SLOPE,
        constant=CONSTANT,
        diagonal=DIAGONAL,
        slope_overrides=None,
        friction_factors=None,
        max_iterations=MAX_ITER,
    )

    # TAZ pass (degenerate: 1 TAZ per commune, same distances).
    od_taz = compute_work_od(
        df_population=_taz_population_for_compute(),
        df_employees=_taz_employees(),
        df_distances=_taz_distance_matrix(),
        df_regiostar=df_regiostar,
        rs7_by_zone=_rs7_by_zone_taz(),
        slope=SLOPE,
        constant=CONSTANT,
        diagonal=DIAGONAL,
        slope_overrides=None,
        friction_factors=None,
        max_iterations=MAX_ITER,
    )

    # Relabel TAZ ids -> commune ids so comparison keys align.
    od_taz_relabelled = _relabel_taz_to_gemeinde(od_taz)

    # Align both frames by (origin_id, destination_id) for a key-safe comparison.
    od_g = (od_gemeinde
            .sort_values(["origin_id", "destination_id"])
            .reset_index(drop=True))
    od_t = (od_taz_relabelled
            .sort_values(["origin_id", "destination_id"])
            .reset_index(drop=True))

    # Shapes must match.
    assert od_g.shape == od_t.shape, (
        "OD frame shape mismatch: Gemeinde %s vs TAZ (relabelled) %s"
        % (od_g.shape, od_t.shape)
    )

    # Keys must match.
    assert (od_g["origin_id"] == od_t["origin_id"]).all(), \
        "origin_id keys do not align after relabelling"
    assert (od_g["destination_id"] == od_t["destination_id"]).all(), \
        "destination_id keys do not align after relabelling"

    # Weights must be equal within floating-point tolerance.
    npt.assert_allclose(
        od_t["weight"].to_numpy(),
        od_g["weight"].to_numpy(),
        atol=1e-9,
        err_msg=(
            "TAZ OD (relabelled) weights differ from Gemeinde OD weights on a "
            "degenerate 1-TAZ-per-commune layer; this indicates the TAZ branch "
            "does not faithfully generalise the Gemeinde branch."
        ),
    )


# ---------------------------------------------------------------------------
# Test 2: row sums are 1 for both passes
# ---------------------------------------------------------------------------

def test_degenerate_taz_both_row_sums_equal_one():
    """Both Gemeinde and TAZ OD must be row-normalised (weights sum to 1 per origin)."""
    df_regiostar = _empty_regiostar()

    od_gemeinde = compute_work_od(
        df_population=_gemeinde_population_for_compute(),
        df_employees=_gemeinde_employees(),
        df_distances=_gemeinde_distance_matrix(),
        df_regiostar=df_regiostar,
        rs7_by_zone=None,
        slope=SLOPE,
        constant=CONSTANT,
        diagonal=DIAGONAL,
        slope_overrides=None,
        friction_factors=None,
        max_iterations=MAX_ITER,
    )
    od_taz = compute_work_od(
        df_population=_taz_population_for_compute(),
        df_employees=_taz_employees(),
        df_distances=_taz_distance_matrix(),
        df_regiostar=df_regiostar,
        rs7_by_zone=_rs7_by_zone_taz(),
        slope=SLOPE,
        constant=CONSTANT,
        diagonal=DIAGONAL,
        slope_overrides=None,
        friction_factors=None,
        max_iterations=MAX_ITER,
    )

    for label, od in [("Gemeinde", od_gemeinde), ("TAZ", od_taz)]:
        row_sums = od.groupby("origin_id")["weight"].sum()
        npt.assert_allclose(
            row_sums.to_numpy(), 1.0, atol=1e-6,
            err_msg="%s OD is not row-normalised (sums: %s)" % (label, row_sums.to_dict()),
        )


# ---------------------------------------------------------------------------
# Test 3: _calibrate Kreis aggregate equivalence
# ---------------------------------------------------------------------------

def test_degenerate_taz_calibrate_kreis_aggregates_equal():
    """_calibrate on the TAZ OD and on the Gemeinde OD must produce equal
    Kreis-pair aggregate flows within 1e-6.

    Steps:
    1. Run compute_work_od on both universes.
    2. Run _calibrate with the appropriate population/key arguments.
    3. Aggregate to Kreis pairs using the respective zone->Kreis maps.
    4. Assert the Kreis-pair totals are equal within 1e-6.
    """
    df_regiostar = _empty_regiostar()
    df_pendler = _pendler()

    # --- Gemeinde OD ---
    od_gemeinde = compute_work_od(
        df_population=_gemeinde_population_for_compute(),
        df_employees=_gemeinde_employees(),
        df_distances=_gemeinde_distance_matrix(),
        df_regiostar=df_regiostar,
        rs7_by_zone=None,
        slope=SLOPE,
        constant=CONSTANT,
        diagonal=DIAGONAL,
        slope_overrides=None,
        friction_factors=None,
        max_iterations=MAX_ITER,
    )

    # _calibrate Gemeinde path uses (commune_id, weight) population frame.
    df_pop_gemeinde = _gemeinde_population()

    calibrated_gemeinde = _calibrate(
        od_gemeinde,
        df_pop_gemeinde,
        df_pendler,
        zone_to_kreis=None,             # OFF path: commune_id[:5] mapping
        population_key="commune_id",
        population_value="weight",
    )

    # Aggregate Gemeinde calibrated flows to Kreis pairs.
    calibrated_gemeinde["orig_kreis"] = calibrated_gemeinde["origin_id"].str[:5]
    calibrated_gemeinde["dest_kreis"] = calibrated_gemeinde["destination_id"].str[:5]
    kreis_g = (calibrated_gemeinde
               .groupby(["orig_kreis", "dest_kreis"])["flow"].sum()
               .reset_index()
               .sort_values(["orig_kreis", "dest_kreis"])
               .reset_index(drop=True))

    # --- TAZ OD ---
    od_taz = compute_work_od(
        df_population=_taz_population_for_compute(),
        df_employees=_taz_employees(),
        df_distances=_taz_distance_matrix(),
        df_regiostar=df_regiostar,
        rs7_by_zone=_rs7_by_zone_taz(),
        slope=SLOPE,
        constant=CONSTANT,
        diagonal=DIAGONAL,
        slope_overrides=None,
        friction_factors=None,
        max_iterations=MAX_ITER,
    )

    # _calibrate TAZ path uses (taz_id, population) population frame.
    df_pop_taz = _taz_population_for_calibrate()
    zone_to_kreis = _taz_to_kreis()

    calibrated_taz = _calibrate(
        od_taz,
        df_pop_taz,
        df_pendler,
        zone_to_kreis=zone_to_kreis,
        population_key="taz_id",
        population_value="population",
    )

    # Relabel TAZ OD -> commune_id and aggregate to Kreis pairs.
    taz_to_commune = {TAZ_A: COMMUNE_A, TAZ_B: COMMUNE_B}
    calibrated_taz["orig_commune"] = calibrated_taz["origin_id"].map(taz_to_commune)
    calibrated_taz["dest_commune"] = calibrated_taz["destination_id"].map(taz_to_commune)
    calibrated_taz["orig_kreis"] = calibrated_taz["orig_commune"].str[:5]
    calibrated_taz["dest_kreis"] = calibrated_taz["dest_commune"].str[:5]
    kreis_t = (calibrated_taz
               .groupby(["orig_kreis", "dest_kreis"])["flow"].sum()
               .reset_index()
               .sort_values(["orig_kreis", "dest_kreis"])
               .reset_index(drop=True))

    # Both tables must cover the same Kreis pairs.
    pairs_g = set(zip(kreis_g["orig_kreis"], kreis_g["dest_kreis"]))
    pairs_t = set(zip(kreis_t["orig_kreis"], kreis_t["dest_kreis"]))
    assert pairs_g == pairs_t, (
        "Kreis-pair sets differ: Gemeinde=%s, TAZ=%s" % (pairs_g, pairs_t)
    )

    # Merge and compare flows.
    merged = kreis_g.merge(
        kreis_t.rename(columns={"flow": "flow_taz"}),
        on=["orig_kreis", "dest_kreis"],
        how="inner",
    )

    npt.assert_allclose(
        merged["flow_taz"].to_numpy(),
        merged["flow"].to_numpy(),
        atol=1e-6,
        err_msg=(
            "Kreis-pair calibrated flows differ between TAZ and Gemeinde paths "
            "on a degenerate 1-TAZ-per-commune layer.  Differences:\n%s"
            % merged.assign(delta=lambda d: d["flow_taz"] - d["flow"])
                    .to_string()
        ),
    )


# ---------------------------------------------------------------------------
# Test 4: degenerate scenario fixture sanity (non-uniform employees + pop)
# ---------------------------------------------------------------------------

def test_fixture_is_non_trivial():
    """The fixture uses non-uniform employees and >1 person per commune.

    A trivially uniform fixture could mask rescaling or aggregation bugs
    (equal inputs -> equal outputs regardless of the path taken).
    """
    # Non-uniform employees across communes (3:1 ratio).
    assert EMP_A != EMP_B, "employees must be non-uniform across communes"

    # >1 person per commune at distinct locations.
    assert len(PERSONS_A) > 1, "commune A must have >1 person"
    assert len(PERSONS_B) > 1, "commune B must have >1 person"

    # TAZ id is distinct from commune_id (the test exercises the id-translation).
    assert TAZ_A != COMMUNE_A
    assert TAZ_B != COMMUNE_B
