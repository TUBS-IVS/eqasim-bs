"""Degenerate TAZ==Gemeinde equivalence test.

Proves that the TAZ branch of ``compute_work_od`` is a faithful generalisation
of the Gemeinde branch by constructing a *degenerate* TAZ layer where:

  - Each commune contains exactly ONE TAZ whose polygon covers the entire commune
    polygon (1:1 bijection, distinct TAZ ids like "T_<commune>").
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

Note on scope of TAZ margin inputs
-----------------------------------
The TAZ population and employee margins used below are hand-constructed to equal
the Gemeinde aggregates on the degenerate (1-TAZ-per-commune) layer.  The real
pipeline helpers ``build_origin_population_per_taz`` and
``build_dest_attraction_per_taz`` are covered separately in
``test_taz_origin_margin.py`` / ``test_taz_dest_margin.py``.  This file proves
only that the gravity + calibrate **kernel** treats relabelled-equal inputs
identically.

The ``_calibrate`` equivalence (Test 3) is genuinely non-tautological because
Kreis ``03101`` contains **two** communes (``03101000`` and ``03101001``).
With only one commune per Kreis, IPF would pin every Kreis-pair flow to its
Pendler observation regardless of the OD shape (one OD cell per Kreis pair ->
nothing to split).  With two communes in Kreis ``03101`` the within-Kreis
intra-Kreis flow (03101->03101) must be distributed across the four
(origin, destination) OD cells ``{A, A2} x {A, A2}`` by OD shape and
population; the comparison can only pass if the TAZ path reproduces the
Gemeinde path's within-Kreis split exactly -- the assertion now BITES.
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
# THREE communes across TWO Kreise:
#   Kreis 03101: communes 03101000 (A) and 03101001 (A2)  <- 2 communes!
#   Kreis 03154: commune  03154000 (B)                    <- 1 commune
#
# Having >=2 communes in Kreis 03101 makes the _calibrate equivalence
# non-tautological: the within-Kreis IPF must split the intra-Kreis target
# across four OD cells (A->A, A->A2, A2->A, A2->A2), so the comparison only
# passes if the TAZ path reproduces the Gemeinde path's within-Kreis split.
#
# Non-uniform employees:  A=300, A2=120, B=100  (break symmetry in all dims).
# Population: 3 persons in A, 2 in A2, 2 in B (weight 1.0 each).
# Distances: realistic non-symmetric values to prevent degenerate cancellation.

COMMUNE_A  = "03101000"   # Kreis 03101, commune 1
COMMUNE_A2 = "03101001"   # Kreis 03101, commune 2  (makes Test 3 non-tautological)
COMMUNE_B  = "03154000"   # Kreis 03154, commune 1

KREIS_A = "03101"   # COMMUNE_A[:5] == COMMUNE_A2[:5]
KREIS_B = "03154"

TAZ_A  = "T_03101000"
TAZ_A2 = "T_03101001"
TAZ_B  = "T_03154000"

# Employees at each commune/TAZ (non-uniform; A:A2:B = 300:120:100).
EMP_A  = 300.0
EMP_A2 = 120.0
EMP_B  = 100.0

# Per-person weight (3 persons in A, 2 in A2, 2 in B).
PERSONS_A  = [1.0, 1.0, 1.0]
PERSONS_A2 = [1.0, 1.0]
PERSONS_B  = [1.0, 1.0]
POP_A  = sum(PERSONS_A)    # 3.0
POP_A2 = sum(PERSONS_A2)   # 2.0
POP_B  = sum(PERSONS_B)    # 2.0

# Distances (km) — non-symmetric within Kreis 03101 to avoid degenerate splits.
# Matrix row = origin, column = destination (A, A2, B).
DIST_AA   = 0.1    # intra-commune diagonal (small but positive)
DIST_AA2  = 5.0    # A -> A2  (within-Kreis, short)
DIST_AA2r = 5.0    # A2 -> A  (symmetric)
DIST_A2A2 = 0.1    # A2 intra-commune diagonal
DIST_AB   = 50.0   # A -> B   (cross-Kreis)
DIST_A2B  = 47.0   # A2 -> B  (slightly shorter than A->B)
DIST_BA   = 50.0   # B -> A
DIST_BA2  = 47.0   # B -> A2
DIST_BB   = 0.1    # B intra-commune diagonal

# Gravity parameters (pre-feature defaults -- must stay in sync with model.py constants).
SLOPE    = -0.2
CONSTANT = -2.4
DIAGONAL = 1.0
MAX_ITER = 10000


# ---------------------------------------------------------------------------
# Build Gemeinde inputs  (3 communes: A, A2, B)
# ---------------------------------------------------------------------------

def _gemeinde_population():
    """Per-person population frame (commune_id, weight) as expected by _calibrate."""
    rows = (
        [{"commune_id": COMMUNE_A,  "weight": w} for w in PERSONS_A]
        + [{"commune_id": COMMUNE_A2, "weight": w} for w in PERSONS_A2]
        + [{"commune_id": COMMUNE_B,  "weight": w} for w in PERSONS_B]
    )
    return pd.DataFrame(rows)


def _gemeinde_population_for_compute():
    """Aggregated Gemeinde population frame for compute_work_od (origin_id, population)."""
    return pd.DataFrame({
        "origin_id":  [COMMUNE_A, COMMUNE_A2, COMMUNE_B],
        "population": [POP_A, POP_A2, POP_B],
    })


def _gemeinde_employees():
    """Gemeinde employee frame for compute_work_od (destination_id, employees)."""
    return pd.DataFrame({
        "destination_id": [COMMUNE_A, COMMUNE_A2, COMMUNE_B],
        "employees":      [EMP_A, EMP_A2, EMP_B],
    })


def _gemeinde_distance_matrix():
    """Dense 3x3 Gemeinde distance matrix."""
    origins      = [COMMUNE_A,  COMMUNE_A,   COMMUNE_A,  COMMUNE_A2, COMMUNE_A2,  COMMUNE_A2, COMMUNE_B,  COMMUNE_B,   COMMUNE_B]
    destinations = [COMMUNE_A,  COMMUNE_A2,  COMMUNE_B,  COMMUNE_A,  COMMUNE_A2,  COMMUNE_B,  COMMUNE_A,  COMMUNE_A2,  COMMUNE_B]
    distances    = [DIST_AA,    DIST_AA2,    DIST_AB,    DIST_AA2r,  DIST_A2A2,   DIST_A2B,   DIST_BA,    DIST_BA2,    DIST_BB]
    return pd.DataFrame({
        "origin_id":      origins,
        "destination_id": destinations,
        "distance_km":    distances,
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
        "origin_id":  [TAZ_A, TAZ_A2, TAZ_B],
        "population": [POP_A, POP_A2, POP_B],
    })


def _taz_population_for_calibrate():
    """TAZ population frame for _calibrate (taz_id, population).

    This is the pop_taz schema produced by build_origin_population_per_taz;
    _calibrate receives it with population_key="taz_id", population_value="population".
    """
    return pd.DataFrame({
        "taz_id":     [TAZ_A, TAZ_A2, TAZ_B],
        "population": [POP_A, POP_A2, POP_B],
    })


def _taz_employees():
    """TAZ employee frame for compute_work_od (destination_id=taz_id, employees).

    In the degenerate case the full commune employee count lands in the single TAZ.
    """
    return pd.DataFrame({
        "destination_id": [TAZ_A, TAZ_A2, TAZ_B],
        "employees":      [EMP_A, EMP_A2, EMP_B],
    })


def _taz_distance_matrix():
    """Dense 3x3 TAZ distance matrix.

    Identical distances as the Gemeinde matrix, just with taz_id keys.
    """
    origins      = [TAZ_A,   TAZ_A,   TAZ_A,   TAZ_A2, TAZ_A2,  TAZ_A2, TAZ_B,  TAZ_B,   TAZ_B]
    destinations = [TAZ_A,   TAZ_A2,  TAZ_B,   TAZ_A,  TAZ_A2,  TAZ_B,  TAZ_A,  TAZ_A2,  TAZ_B]
    distances    = [DIST_AA, DIST_AA2, DIST_AB, DIST_AA2r, DIST_A2A2, DIST_A2B, DIST_BA, DIST_BA2, DIST_BB]
    return pd.DataFrame({
        "origin_id":      origins,
        "destination_id": destinations,
        "distance_km":    distances,
    })


def _taz_to_kreis():
    """TAZ->Kreis lookup: both A and A2 map to Kreis 03101; B maps to 03154."""
    return {TAZ_A: KREIS_A, TAZ_A2: KREIS_A, TAZ_B: KREIS_B}


def _rs7_by_zone_taz():
    """No RS7 overrides for TAZ (inert lookup, matches empty regiostar on Gemeinde path)."""
    return None


# ---------------------------------------------------------------------------
# Pendler frame
# ---------------------------------------------------------------------------

def _pendler():
    """BA Pendler frame with intra-Kreis + cross-Kreis flows.

    Kreis 03101 has a substantial within-Kreis self-flow (150.0) that the IPF
    must distribute across four OD cells: (A, A), (A, A2), (A2, A), (A2, A2).
    This intra-Kreis split is governed by OD shape and population and cannot be
    trivially pinned when there are multiple communes per Kreis -- making Test 3
    genuinely non-tautological.
    """
    return pd.DataFrame({
        "orig_ars": [KREIS_A, KREIS_A, KREIS_B, KREIS_B],
        "dest_ars": [KREIS_A, KREIS_B, KREIS_A, KREIS_B],
        "flow":     [150.0, 50.0, 30.0, 70.0],
    })


# ---------------------------------------------------------------------------
# Helper: relabel TAZ OD -> Gemeinde OD
# ---------------------------------------------------------------------------

_TAZ_TO_COMMUNE = {TAZ_A: COMMUNE_A, TAZ_A2: COMMUNE_A2, TAZ_B: COMMUNE_B}


def _relabel_taz_to_gemeinde(df_taz_od: pd.DataFrame) -> pd.DataFrame:
    """Replace taz_id -> commune_id in origin_id and destination_id.

    The degenerate 1:1 map is: TAZ_A -> COMMUNE_A, TAZ_A2 -> COMMUNE_A2,
    TAZ_B -> COMMUNE_B.
    """
    df = df_taz_od.copy()
    df["origin_id"]      = df["origin_id"].map(_TAZ_TO_COMMUNE)
    df["destination_id"] = df["destination_id"].map(_TAZ_TO_COMMUNE)
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
# Test 3: _calibrate full-OD equivalence (hardened: non-tautological)
# ---------------------------------------------------------------------------

def test_degenerate_taz_calibrate_full_od_equal():
    """_calibrate on the TAZ OD and on the Gemeinde OD must produce equal
    calibrated flows for every (origin, destination) pair within 1e-6, after
    relabelling taz_id -> commune_id.

    Why this is NOT tautological (unlike a single-commune-per-Kreis fixture):
    Kreis 03101 contains TWO communes (03101000 and 03101001).  The Pendler
    observation for (03101, 03101) = 150.0 must be distributed across the four
    OD cells (A->A, A->A2, A2->A, A2->A2) by the IPF proportional to their
    flow = weight * pop.  This within-Kreis split depends on the OD shape and
    population; it is different for every (origin, destination) pair and is NOT
    pinned to the Pendler target by mere convergence.  The full-OD comparison
    passes only if the TAZ path reproduces the Gemeinde path's within-Kreis
    distribution cell-by-cell -- a meaningful check on whether relabelling and
    the zone->Kreis mapping are consistent.

    Steps:
    1. Run compute_work_od on both universes.
    2. Run _calibrate with the appropriate population/key arguments.
    3. Relabel the TAZ calibrated OD (taz_id -> commune_id).
    4. Align both frames by (origin_id, destination_id).
    5. Assert assert_allclose(atol=1e-6) on the full flow column.
    """
    df_regiostar = _empty_regiostar()
    df_pendler   = _pendler()

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
    calibrated_gemeinde = _calibrate(
        od_gemeinde,
        _gemeinde_population(),
        df_pendler,
        zone_to_kreis=None,
        population_key="commune_id",
        population_value="weight",
    )

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
    calibrated_taz = _calibrate(
        od_taz,
        _taz_population_for_calibrate(),
        df_pendler,
        zone_to_kreis=_taz_to_kreis(),
        population_key="taz_id",
        population_value="population",
    )

    # Relabel TAZ calibrated OD: taz_id -> commune_id.
    calibrated_taz_relabelled = _relabel_taz_to_gemeinde(calibrated_taz)

    # Align both frames by (origin_id, destination_id).
    cal_g = (calibrated_gemeinde
             .sort_values(["origin_id", "destination_id"])
             .reset_index(drop=True))
    cal_t = (calibrated_taz_relabelled
             .sort_values(["origin_id", "destination_id"])
             .reset_index(drop=True))

    # Shape must match.
    assert cal_g.shape == cal_t.shape, (
        "Calibrated OD shape mismatch: Gemeinde %s vs TAZ (relabelled) %s"
        % (cal_g.shape, cal_t.shape)
    )

    # Keys must match.
    assert (cal_g["origin_id"] == cal_t["origin_id"]).all(), \
        "origin_id keys do not align in calibrated OD after relabelling"
    assert (cal_g["destination_id"] == cal_t["destination_id"]).all(), \
        "destination_id keys do not align in calibrated OD after relabelling"

    # Full calibrated flow must be equal within tolerance.
    # With >=2 communes per Kreis the within-Kreis split is non-trivial, so
    # this comparison bites: a mismatched zone->Kreis mapping or a broken
    # population-key lookup would produce a different within-Kreis distribution.
    npt.assert_allclose(
        cal_t["flow"].to_numpy(),
        cal_g["flow"].to_numpy(),
        atol=1e-6,
        err_msg=(
            "Calibrated flows differ between TAZ and Gemeinde paths (relabelled) "
            "on a degenerate 1-TAZ-per-commune layer.  The within-Kreis split for "
            "Kreis 03101 (which has 2 communes) must match cell-by-cell.\n"
            "Gemeinde:\n%s\nTAZ (relabelled):\n%s"
            % (cal_g.to_string(), cal_t.to_string())
        ),
    )


# ---------------------------------------------------------------------------
# Test 4: degenerate scenario fixture sanity (non-uniform employees + pop)
# ---------------------------------------------------------------------------

def test_fixture_is_non_trivial():
    """The fixture uses non-uniform employees and >1 person per commune.

    A trivially uniform fixture could mask rescaling or aggregation bugs
    (equal inputs -> equal outputs regardless of the path taken).
    Also verifies that Kreis 03101 has >=2 communes (necessary for Test 3 to
    be non-tautological).
    """
    # Non-uniform employees across communes.
    assert len({EMP_A, EMP_A2, EMP_B}) == 3, "employees must be non-uniform across all communes"

    # >1 person per commune at distinct locations.
    assert len(PERSONS_A) > 1,  "commune A must have >1 person"
    assert len(PERSONS_A2) > 1, "commune A2 must have >1 person"
    assert len(PERSONS_B) > 1,  "commune B must have >1 person"

    # TAZ id is distinct from commune_id (the test exercises the id-translation).
    assert TAZ_A  != COMMUNE_A
    assert TAZ_A2 != COMMUNE_A2
    assert TAZ_B  != COMMUNE_B

    # Kreis 03101 has >=2 communes (required for non-tautological _calibrate test).
    kreis_a_communes = [c for c in [COMMUNE_A, COMMUNE_A2, COMMUNE_B]
                        if c.startswith(KREIS_A)]
    assert len(kreis_a_communes) >= 2, (
        "Kreis %s must contain >=2 communes for Test 3 to be non-tautological; "
        "found: %s" % (KREIS_A, kreis_a_communes)
    )
