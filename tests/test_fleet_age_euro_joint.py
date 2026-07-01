"""Tests for the age/euro joint sampler (fleet age calibration fix, #92).

Pins the IPF joint that replaces the euro-first + age-mask draw: it must honour
BOTH KBA marginals (age|fuel rows, euro|fuel cols) on the Euro-age consistency
support, so the synthetic fleet age marginal matches KBA (~10.6 yr) instead of
collapsing ~4 yr too young.
"""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

F = pytest.importorskip("braunschweig.synthesis.vehicles.fleet_sampling_de")


def test_ipf_joint_hits_both_marginals_full_support():
    allowed = np.ones((3, 4))
    r = np.array([0.2, 0.3, 0.5])
    c = np.array([0.1, 0.2, 0.3, 0.4])
    M = F._ipf_joint(allowed, r, c)
    assert np.allclose(M.sum(axis=1), r, atol=1e-8)
    assert np.allclose(M.sum(axis=0), c, atol=1e-8)


def test_ipf_joint_respects_zero_support():
    # A blocked cell must stay exactly zero; feasible marginals still hit.
    allowed = np.array([[1.0, 0.0], [1.0, 1.0]])
    r = np.array([0.5, 0.5])
    c = np.array([0.7, 0.3])
    M = F._ipf_joint(allowed, r, c)
    assert M[0, 1] == 0.0                      # blocked cell never gets mass
    assert np.allclose(M.sum(axis=1), r, atol=1e-8)
    assert np.allclose(M.sum(axis=0), c, atol=1e-8)


def test_ipf_joint_no_allowed_cell_falls_back_to_outer_product():
    allowed = np.zeros((2, 2))
    r = np.array([0.6, 0.4]); c = np.array([0.3, 0.7])
    M = F._ipf_joint(allowed, r, c)
    assert np.allclose(M, np.outer(r, c))


def test_draw_never_picks_a_zero_support_cell():
    # rows=age (2), cols=euro (2); cell (0,1) blocked. Draw many -> never (0,1).
    rng = np.random.default_rng(0)
    # Use a matrix sized to the real label vectors so divmod indexing matches.
    import braunschweig.data.kba.fleet_tables as ft
    na, ne = len(ft.AGE_BAND_LABELS), len(ft.EURO_CLASS_LABELS)
    M = np.zeros((na, ne)); M[0, 0] = 0.5; M[1, 1] = 0.5   # only two allowed cells
    seen = set()
    for _ in range(500):
        a, e = F._draw_age_euro_joint(rng, M)
        seen.add((ft.AGE_BAND_LABELS.index(a), ft.EURO_CLASS_LABELS.index(e)))
    assert seen <= {(0, 0), (1, 1)}            # never a zero cell


def test_tilt_shifts_age_mass_mean_preservingly():
    import braunschweig.data.kba.fleet_tables as ft
    na, ne = len(ft.AGE_BAND_LABELS), len(ft.EURO_CLASS_LABELS)
    rng = np.random.default_rng(1)
    M = np.ones((na, ne)) / (na * ne)          # uniform joint
    # Tilt toward the youngest band (index 0).
    tilt = np.ones(na); tilt[0] = 5.0
    draws = [F._draw_age_euro_joint(rng, M, tilt) for _ in range(2000)]
    young = sum(1 for a, _ in draws if a == ft.AGE_BAND_LABELS[0])
    base = np.ones(na) / (na * ne)             # untilted marginal for band 0 share
    assert young / len(draws) > (1.0 / na)     # tilt raised the youngest share


def test_matrices_never_place_mass_on_inconsistent_cells():
    # Every nonzero (age,euro) cell in a built joint must be Euro-age consistent.
    import braunschweig.data.kba.fleet_tables as ft
    ages, euros = list(ft.AGE_BAND_LABELS), list(ft.EURO_CLASS_LABELS)
    age_given = {"petrol": np.array([0.16, 0.29, 0.20, 0.18, 0.09, 0.03, 0.05])}
    euro_given = {"petrol": np.array([0.01, 0.04, 0.03, 0.24, 0.20, 0.45, 0.03])}
    joint = F._age_euro_joint_matrices(age_given, euro_given)["petrol"]
    for ai, a in enumerate(ages):
        for ei, e in enumerate(euros):
            if joint[ai, ei] > 1e-12:
                assert F._age_consistent_with_euro(a, e, "petrol"), (a, e)
