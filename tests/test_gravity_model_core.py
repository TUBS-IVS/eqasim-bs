"""Unit tests for the pure functions in ``braunschweig.gravity.model``.

These cover the data-free, side-effect-light helpers that can be exercised on
small hand-built inputs, namely:

- ``evaluate_gravity`` -- the doubly-constrained (Furness) balancing loop. The
  invariants checked follow directly from the loop body in ``model.py``: at
  convergence ``flow[i, j] = production[i] * attraction[j] * friction[i, j]``,
  so the row sums must reproduce the production target (``population``) and the
  column sums the attraction target (``employees``). Doubly-constrained
  balancing only admits a solution when total production equals total
  attraction, so every test builds inputs with
  ``sum(population) == sum(employees)``.

- ``_build_origin_slope_vector`` -- the per-RegioStaR-7 slope flatten/fallback
  helper (scalar default, per-class override, unmapped-code fallback, and the
  12-digit ARS -> 8-digit AGS normalisation).

- ``apply_sector_aware_attraction`` -- the within-Kreis establishment-density
  tilt, whose defining invariant is that the employee-weighted Kreis total is
  preserved.

The synpp ``configure``/``execute`` entry points are intentionally NOT tested
here: they require real pipeline stages and data. Linear-algebra-heavy code
(the Poisson-GLM gravity calibration scripts) is also avoided because the test
environment ships a reference LAPACK that crashes on SVD/lstsq; the functions
exercised here use only elementwise array math and IPF-style balancing, which
are unaffected.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.gravity.model import (  # noqa: E402
    _append_outbound_flows,
    _build_origin_slope_vector,
    apply_sector_aware_attraction,
    compute_work_od,
    evaluate_gravity,
)


# --- evaluate_gravity: doubly-constrained Furness balancing ----------------

def test_evaluate_gravity_matches_production_and_attraction_targets():
    """Row sums reproduce ``population``, column sums reproduce ``employees``.

    This is the defining property of the doubly-constrained balancing in
    ``model.py``. Inputs are balanced (equal totals) so a solution exists.
    """
    population = np.array([10.0, 20.0, 30.0])
    employees = np.array([15.0, 15.0, 30.0])
    assert np.isclose(population.sum(), employees.sum())

    # Asymmetric, strictly positive friction (not the production friction with a
    # diagonal term -- just a generic decay-like matrix) so the balancing has a
    # non-trivial structure to resolve.
    friction = np.array([
        [1.0, 0.5, 0.2],
        [0.4, 1.0, 0.3],
        [0.1, 0.6, 1.0],
    ])

    flow = evaluate_gravity(population, employees, friction)

    # The internal convergence threshold is 1e-3 on the per-step deltas, so the
    # residual on the margins is comfortably below 1e-2.
    assert np.allclose(flow.sum(axis=1), population, atol=1e-2)
    assert np.allclose(flow.sum(axis=0), employees, atol=1e-2)
    assert np.all(flow >= 0.0)


def test_evaluate_gravity_single_zone():
    """A one-zone system places the whole flow on the single cell."""
    population = np.array([5.0])
    employees = np.array([5.0])
    friction = np.array([[1.0]])

    flow = evaluate_gravity(population, employees, friction)

    assert flow.shape == (1, 1)
    assert np.isclose(flow[0, 0], 5.0, atol=1e-2)


def test_evaluate_gravity_uniform_friction_recovers_independent_product():
    """With a constant friction the balanced flow is the outer product.

    When ``friction`` is constant the doubly-constrained solution is the
    independence table ``flow[i, j] = population[i] * employees[j] / total``
    (the maximum-entropy fit consistent with both margins). We assert both the
    margins and this closed-form expectation.
    """
    population = np.array([10.0, 30.0])
    employees = np.array([20.0, 20.0])
    total = population.sum()
    assert np.isclose(total, employees.sum())

    friction = np.full((2, 2), 0.7)

    flow = evaluate_gravity(population, employees, friction)

    expected = np.outer(population, employees) / total
    assert np.allclose(flow, expected, atol=1e-2)
    assert np.allclose(flow.sum(axis=1), population, atol=1e-2)
    assert np.allclose(flow.sum(axis=0), employees, atol=1e-2)


def test_evaluate_gravity_symmetric_inputs_yield_symmetric_flow():
    """Symmetric margins + symmetric friction give a symmetric flow matrix."""
    population = np.array([12.0, 12.0])
    employees = np.array([12.0, 12.0])
    friction = np.array([
        [1.0, 0.3],
        [0.3, 1.0],
    ])

    flow = evaluate_gravity(population, employees, friction)

    assert np.allclose(flow, flow.T, atol=1e-3)
    assert np.allclose(flow.sum(axis=1), population, atol=1e-2)


# --- _build_origin_slope_vector: per-RS7 slope flatten/fallback ------------

def test_slope_vector_scalar_default_when_overrides_none():
    """No overrides -> every origin gets the scalar default slope."""
    municipalities = ["03101000", "03158021", "03153000"]
    vector = _build_origin_slope_vector(municipalities, -0.065, None, None)
    assert vector.shape == (3,)
    assert np.allclose(vector, -0.065)


def test_slope_vector_scalar_default_when_overrides_empty_or_regiostar_empty():
    """Empty override map or empty/None RegioStaR table -> scalar default."""
    municipalities = ["03101000", "03158021"]
    empty_rs7 = pd.DataFrame(columns=["commune_id", "regiostar7"])

    # Empty override dict, with a populated RegioStaR table present.
    rs7 = pd.DataFrame({"commune_id": ["03101000"], "regiostar7": [72]})
    vector = _build_origin_slope_vector(municipalities, -0.05, {}, rs7)
    assert np.allclose(vector, -0.05)

    # Non-empty override but no RegioStaR table at all.
    vector = _build_origin_slope_vector(municipalities, -0.05, {72: -0.03}, None)
    assert np.allclose(vector, -0.05)

    # Non-empty override but an empty RegioStaR table.
    vector = _build_origin_slope_vector(
        municipalities, -0.05, {72: -0.03}, empty_rs7
    )
    assert np.allclose(vector, -0.05)


def test_slope_vector_applies_per_class_override_and_falls_back_for_unmapped():
    """Mapped RS7 code uses its slope; everything else keeps the scalar."""
    # 03101000 -> RS7 72 (overridden), 03158021 -> RS7 75 (NOT in overrides),
    # 03153000 -> absent from the RegioStaR table entirely.
    municipalities = ["03101000", "03158021", "03153000"]
    rs7 = pd.DataFrame({
        "commune_id": ["03101000", "03158021"],
        "regiostar7": [72, 75],
    })
    overrides = {72: -0.0333}

    vector = _build_origin_slope_vector(municipalities, -0.065, overrides, rs7)

    assert np.isclose(vector[0], -0.0333)   # mapped + overridden
    assert np.isclose(vector[1], -0.065)    # mapped but no override -> scalar
    assert np.isclose(vector[2], -0.065)    # not in RegioStaR table -> scalar


def test_slope_vector_normalises_12_digit_ars_to_8_digit_ags():
    """A 12-digit ARS origin is matched after ARS[0:5] + ARS[9:12] folding.

    The RegioStaR table keys on the 8-digit AGS; ``_build_origin_slope_vector``
    folds 12-char ARS ids to AGS before lookup. ARS 031540160160 ->
    AGS 03154 + 160 = 03154160.
    """
    municipalities = ["031540160160"]
    rs7 = pd.DataFrame({"commune_id": ["03154160"], "regiostar7": [76]})
    overrides = {76: -0.0782}

    vector = _build_origin_slope_vector(municipalities, -0.065, overrides, rs7)

    assert np.isclose(vector[0], -0.0782)


def test_slope_vector_handles_string_override_keys():
    """Override keys are coerced to int (config may carry string keys)."""
    municipalities = ["03101000"]
    rs7 = pd.DataFrame({"commune_id": ["03101000"], "regiostar7": [72]})
    overrides = {"72": -0.04}

    vector = _build_origin_slope_vector(municipalities, -0.065, overrides, rs7)

    assert np.isclose(vector[0], -0.04)


# --- apply_sector_aware_attraction: within-Kreis density tilt --------------

def test_sector_aware_disabled_returns_input_unchanged():
    """Disabled flag -> the exact input object is returned (byte-identical)."""
    df_employees = pd.DataFrame({
        "commune_id": ["03101000000", "03101000001"],
        "employees": [100.0, 50.0],
    })
    result = apply_sector_aware_attraction(df_employees, None, enabled=False)
    assert result is df_employees


def test_sector_aware_enabled_preserves_kreis_total():
    """Within a Kreis the employee-weighted total is preserved after the tilt.

    Two Gemeinden in the same Kreis (commune_id[:5] == '03101') with different
    establishment densities. The tilt reshapes the split but the Kreis sum of
    ``employees`` must be unchanged.
    """
    df_employees = pd.DataFrame({
        "commune_id": ["03101000000", "03101000001"],
        "employees": [100.0, 100.0],
    })
    df_betriebe = pd.DataFrame({
        "commune_id": ["03101000000", "03101000001"],
        "n_betriebe": [40, 10],   # first Gemeinde is establishment-denser
    })

    result = apply_sector_aware_attraction(
        df_employees, df_betriebe, enabled=True
    )

    # Kreis total preserved exactly.
    assert np.isclose(result["employees"].sum(), 200.0)
    # The denser Gemeinde gains attraction, the sparser one loses it.
    assert result.loc[0, "employees"] > 100.0
    assert result.loc[1, "employees"] < 100.0


def test_sector_aware_equal_density_leaves_split_unchanged():
    """Equal establishment density -> neutral tilt (no reshaping)."""
    df_employees = pd.DataFrame({
        "commune_id": ["03101000000", "03101000001"],
        "employees": [80.0, 120.0],
    })
    # rho = n_betriebe / employees is equal (0.25) for both, so the relative
    # tilt is 1.0 everywhere and the split is unchanged.
    df_betriebe = pd.DataFrame({
        "commune_id": ["03101000000", "03101000001"],
        "n_betriebe": [20, 30],
    })

    result = apply_sector_aware_attraction(
        df_employees, df_betriebe, enabled=True
    )

    assert np.allclose(result["employees"].to_numpy(), [80.0, 120.0])


def test_sector_aware_missing_betriebe_raises_when_enabled():
    """Enabled flag with no establishment table is a hard error."""
    df_employees = pd.DataFrame({
        "commune_id": ["03101000000"],
        "employees": [100.0],
    })
    try:
        apply_sector_aware_attraction(df_employees, None, enabled=True)
    except ValueError as error:
        assert "n_betriebe" in str(error)
    else:
        raise AssertionError("expected ValueError when betriebe table missing")


# --- zero-total-origin self-loop fallback: observability (no-silent-fallback) --

def test_compute_work_od_zero_population_origin_logs_and_keeps_self_loop(caplog):
    """A zero-population origin has an all-zero flow row (Furness leaves it at 0),
    so the row-normalisation would divide 0/0; the self-loop is forced to weight
    1.0 instead. The fallback must be counted and logged (CLAUDE.md
    "Fallback transparency"), and the forced weight must be exactly 1.0 while the
    other (non-zero) origins are unaffected."""
    df_population = pd.DataFrame({
        "origin_id": ["A", "B", "C"],
        "population": [10.0, 20.0, 0.0],
    })
    df_employees = pd.DataFrame({
        "destination_id": ["A", "B", "C"],
        "employees": [15.0, 15.0, 0.0],
    })
    municipalities = ["A", "B", "C"]
    df_distances = pd.DataFrame(
        [(o, d, 5.0) for o in municipalities for d in municipalities],
        columns=["origin_id", "destination_id", "distance_km"],
    )

    import logging
    with caplog.at_level(logging.INFO, logger="braunschweig.gravity.model"):
        df_matrix = compute_work_od(
            df_population, df_employees, df_distances,
            df_regiostar=None, rs7_by_zone=None,
            slope=-0.065, constant=0.0, diagonal=1.0,
            slope_overrides=None, friction_factors=None,
            max_iterations=1000,
        )

    assert any(
        "zero-total origins forced to self-loop" in r.message for r in caplog.records
    )
    row_c = df_matrix[(df_matrix["origin_id"] == "C") & (df_matrix["destination_id"] == "C")]
    assert np.isclose(row_c["weight"].iloc[0], 1.0)
    # Origin A's weights are unaffected by the fallback and still sum to 1.0.
    row_a = df_matrix[df_matrix["origin_id"] == "A"]
    assert np.isclose(row_a["weight"].sum(), 1.0)


def test_compute_work_od_no_zero_origins_stays_silent(caplog):
    """No zero-population origin -> no fallback log line is emitted."""
    df_population = pd.DataFrame({
        "origin_id": ["A", "B"],
        "population": [10.0, 20.0],
    })
    df_employees = pd.DataFrame({
        "destination_id": ["A", "B"],
        "employees": [15.0, 15.0],
    })
    df_distances = pd.DataFrame(
        [(o, d, 5.0) for o in ["A", "B"] for d in ["A", "B"]],
        columns=["origin_id", "destination_id", "distance_km"],
    )

    import logging
    with caplog.at_level(logging.INFO, logger="braunschweig.gravity.model"):
        compute_work_od(
            df_population, df_employees, df_distances,
            df_regiostar=None, rs7_by_zone=None,
            slope=-0.065, constant=0.0, diagonal=1.0,
            slope_overrides=None, friction_factors=None,
            max_iterations=1000,
        )

    assert not any(
        "zero-total origins forced to self-loop" in r.message for r in caplog.records
    )


def test_append_outbound_flows_zero_total_origin_logs_and_keeps_self_loop(caplog):
    """Same fallback in the outbound-injection path: an origin with an all-zero
    outbound OD row (and no BA-Pendler outbound flow to inject) gets its self-loop
    forced to weight 1.0, and the event is counted and logged."""
    df_od = pd.DataFrame({
        "origin_id": ["A", "A", "B"],
        "destination_id": ["A", "B", "B"],
        "flow": [0.0, 0.0, 5.0],
    })
    df_population = pd.DataFrame({
        "commune_id": ["A", "B"],
        "weight": [10.0, 20.0],
    })
    # Empty BA-Pendler outbound frame -> df_out_pendler stays empty -> df_all = df_od.
    df_pendler = pd.DataFrame(columns=["orig_ars", "dest_ars", "flow"])
    df_external = pd.DataFrame(columns=["ars5", "commune_id", "employees"])

    import logging
    with caplog.at_level(logging.INFO, logger="braunschweig.gravity.model"):
        df_all = _append_outbound_flows(
            df_od, df_population, df_pendler, df_external, scope=["A", "B"],
        )

    assert any(
        "zero-total origins forced to self-loop" in r.message for r in caplog.records
    )
    row_a_self = df_all[(df_all["origin_id"] == "A") & (df_all["destination_id"] == "A")]
    assert np.isclose(row_a_self["weight"].iloc[0], 1.0)


def test_sector_aware_gemeinde_without_betriebe_keeps_neutral_tilt():
    """A Gemeinde absent from the betriebe table gets a neutral tilt.

    The defined-density Gemeinde and the undefined one share a Kreis. Since one
    Gemeinde has no defined density, the within-Kreis reference and the
    renormalisation must still leave the Kreis total preserved and never produce
    a NaN or zero attraction out of the missing entry.
    """
    df_employees = pd.DataFrame({
        "commune_id": ["03101000000", "03101000001"],
        "employees": [100.0, 100.0],
    })
    df_betriebe = pd.DataFrame({
        "commune_id": ["03101000000"],   # second Gemeinde missing
        "n_betriebe": [40],
    })

    result = apply_sector_aware_attraction(
        df_employees, df_betriebe, enabled=True
    )

    assert np.isfinite(result["employees"].to_numpy()).all()
    assert (result["employees"].to_numpy() > 0.0).all()
    assert np.isclose(result["employees"].sum(), 200.0)
