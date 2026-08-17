"""Final-review guard: per-Kreis powertrain marginal degenerate-Kreis fallback.

Covers ``PowertrainModel._rake_per_kreis_powertrain`` (extracted from
``from_data_path`` during the final whole-branch review). The Task B3 change made
the per-Kreis powertrain marginal cover EVERY German Kreis so cross-cordon
in-commuters can draw their real home-Kreis fuel mix. That widens exposure to a
degenerate Kreis: one that carries ``insg>0`` yet has every fuel component
suppressed (read as ``NaN`` by ``load_kreis_fuel``) or zero. Without a guard,
``col_target / col_target.sum()`` yields a NaN column target -> ``rake_2d``
returns a NaN matrix -> ``rng.choice(p=nan)`` crashes at draw time.

These tests exercise the PRIMARY path (real mass -> raked, correct marginal) AND
the FALLBACK path (degenerate -> national ``P(powertrain | segment)``), asserting
the no-NA guarantee (every returned pmf is finite and sums to 1) and that the
primary/fallback counts are reported for logging (no-silent-fallback rule).

Pure-function tests: no data directory required, so they run locally.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.data.kba import fleet_tables as ft  # noqa: E402
from braunschweig.synthesis.vehicles import fleet_sampling_de as fs  # noqa: E402

_PT = list(ft.POWERTRAIN_LABELS)
_IDX = {p: i for i, p in enumerate(_PT)}

# National P(powertrain | segment) over 3 synthetic segments; each row is a valid
# pmf (sums to 1) in the canonical powertrain order
# (petrol, diesel, gas, bev, phev, hybrid, hydrogen, other).
_NATIONAL_PSG = np.array([
    [0.45, 0.40, 0.02, 0.05, 0.03, 0.04, 0.00, 0.01],
    [0.50, 0.35, 0.02, 0.04, 0.03, 0.05, 0.00, 0.01],
    [0.40, 0.45, 0.03, 0.05, 0.02, 0.04, 0.00, 0.01],
])
_SEG_SHARE = np.array([0.5, 0.3, 0.2])


def _vec(**counts) -> np.ndarray:
    """Build a count-like powertrain vector in the canonical order."""
    v = np.zeros(len(_PT), dtype=float)
    for label, value in counts.items():
        v[_IDX[label]] = value
    return v


def _rake(kreis_marginal):
    return fs.PowertrainModel._rake_per_kreis_powertrain(
        kreis_marginal, _NATIONAL_PSG, _SEG_SHARE,
        max_iterations=200, tolerance=1e-12,
    )


def _column_marginal(mat: np.ndarray) -> np.ndarray:
    """Per-Kreis powertrain marginal implied by the raked P(pt|seg) matrix."""
    return mat.T @ _SEG_SHARE


# --------------------------------------------------------------------------- #
# PRIMARY path
# --------------------------------------------------------------------------- #

def test_primary_two_kreise_raked_and_counted():
    """Two Kreise with real mass are both raked; counts report 2 primary, 0 degen."""
    marginal = {
        "03101": _vec(petrol=70_000, diesel=30_000, gas=1_000,
                      bev=5_000, phev=2_000, hybrid=3_000, other=500),
        "03102": _vec(petrol=20_000, diesel=80_000, gas=500,
                      bev=1_000, phev=500, hybrid=800, other=200),
    }
    kreis_psp, n_primary, n_degenerate = _rake(marginal)
    assert set(kreis_psp) == {"03101", "03102"}
    assert n_primary == 2
    assert n_degenerate == 0


def test_primary_column_marginal_matches_real_ratio():
    """Raking forces the column marginal to the real per-Kreis petrol:diesel ratio."""
    marginal = {
        "A": _vec(petrol=70_000, diesel=30_000),   # 70:30
        "B": _vec(petrol=20_000, diesel=80_000),   # 20:80
    }
    kreis_psp, _, _ = _rake(marginal)
    for kreis, expected_petrol_frac in (("A", 0.70), ("B", 0.20)):
        col = _column_marginal(kreis_psp[kreis])
        petrol = col[_IDX["petrol"]]
        diesel = col[_IDX["diesel"]]
        assert petrol + diesel > 0
        assert petrol / (petrol + diesel) == pytest.approx(expected_petrol_frac, abs=1e-6)


def test_primary_outputs_are_valid_pmfs():
    """Every raked row is a finite pmf summing to 1 (no-NA / draw-safe)."""
    marginal = {"A": _vec(petrol=60_000, diesel=40_000, bev=4_000)}
    kreis_psp, _, _ = _rake(marginal)
    mat = kreis_psp["A"]
    assert np.isfinite(mat).all()
    np.testing.assert_allclose(mat.sum(axis=1), np.ones(mat.shape[0]), atol=1e-9)


# --------------------------------------------------------------------------- #
# DEGENERATE fallback path (the crash the guard prevents)
# --------------------------------------------------------------------------- #

def test_all_zero_kreis_falls_back_to_national():
    """An all-zero Kreis marginal uses the national P(pt|seg) fallback, not NaN."""
    marginal = {"DEAD": np.zeros(len(_PT), dtype=float)}
    kreis_psp, n_primary, n_degenerate = _rake(marginal)
    assert n_primary == 0
    assert n_degenerate == 1
    np.testing.assert_array_equal(kreis_psp["DEAD"], _NATIONAL_PSG)
    assert np.isfinite(kreis_psp["DEAD"]).all()


def test_all_nan_kreis_falls_back_without_nan():
    """A fully suppressed (all-NaN) Kreis marginal must not leak NaN into the pmf."""
    marginal = {"SUPP": np.full(len(_PT), np.nan)}
    kreis_psp, n_primary, n_degenerate = _rake(marginal)
    assert n_primary == 0
    assert n_degenerate == 1
    assert np.isfinite(kreis_psp["SUPP"]).all()
    np.testing.assert_allclose(kreis_psp["SUPP"].sum(axis=1),
                               np.ones(_NATIONAL_PSG.shape[0]), atol=1e-9)


def test_partial_suppression_stays_primary_and_zeroes_nan_component():
    """A Kreis with real petrol/diesel but a NaN minor component is still primary;
    the NaN component is treated as 0 (46251-02 suppresses only small counts)."""
    v = _vec(petrol=60_000, diesel=40_000)
    v[_IDX["bev"]] = np.nan  # a suppressed minor cell
    kreis_psp, n_primary, n_degenerate = _rake({"P": v})
    assert n_primary == 1
    assert n_degenerate == 0
    mat = kreis_psp["P"]
    assert np.isfinite(mat).all()
    col = _column_marginal(mat)
    assert col[_IDX["bev"]] == pytest.approx(0.0, abs=1e-9)


def test_mixed_dict_reports_split_and_never_emits_nan():
    """A mix of healthy and degenerate Kreise: correct counts and no NaN anywhere."""
    marginal = {
        "OK1": _vec(petrol=50_000, diesel=50_000, bev=3_000),
        "ZERO": np.zeros(len(_PT), dtype=float),
        "OK2": _vec(petrol=80_000, diesel=20_000),
        "NAN": np.full(len(_PT), np.nan),
    }
    kreis_psp, n_primary, n_degenerate = _rake(marginal)
    assert n_primary == 2
    assert n_degenerate == 2
    for kreis, mat in kreis_psp.items():
        assert np.isfinite(mat).all(), f"Kreis {kreis} emitted a non-finite pmf"
        np.testing.assert_allclose(mat.sum(axis=1),
                                   np.ones(mat.shape[0]), atol=1e-9,
                                   err_msg=f"Kreis {kreis} rows are not pmfs")


def test_national_psg_not_mutated_by_fallback():
    """The shared national seed must not be mutated (degenerate uses a copy)."""
    before = _NATIONAL_PSG.copy()
    marginal = {"ZERO": np.zeros(len(_PT), dtype=float),
                "OK": _vec(petrol=60_000, diesel=40_000)}
    kreis_psp, _, _ = _rake(marginal)
    np.testing.assert_array_equal(_NATIONAL_PSG, before)
    # Mutating a returned fallback matrix must not bleed into the national seed.
    kreis_psp["ZERO"][0, 0] = 999.0
    np.testing.assert_array_equal(_NATIONAL_PSG, before)
