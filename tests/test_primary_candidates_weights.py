"""Tests for the multinomial weight normalization in the primary candidates stage.

Ported defensive hardening from eqasim-org/eqasim-france#447: np.random.multinomial
casts pvals to float64 internally and rejects a vector whose leading sum
(``sum(pvals[:-1])``) exceeds 1.0.  A float32 weight vector (e.g. one derived from a
float32 ``area_m2`` / potential column) can overshoot after that cast.  Normalising in
float64 up front removes the risk.

Scope note (verified 2026-07-17, numpy 1.23.5 in the ``eqasim`` env): the failure does
NOT currently manifest in this project -- both live weight paths (gravity model,
legacy census OD) are float64, and multinomial did not raise on 0/2000 adversarial
float32 nor 0/5000 float64 inputs.  For float64 inputs the normalization is
byte-identical (0/5000 draws changed).  These tests therefore pin the invariant as a
regression guard, not a reproduction of an active crash.

Pure-function tests only: no synpp context, no matsim import, no real data.
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import numpy as np
import pytest

from synthesis.population.spatial.primary.candidates import _normalize_weights


def test_normalize_returns_float64():
    """A float32 input is promoted to float64 (the dtype multinomial casts to)."""
    w = np.array([0.1, 0.2, 0.3, 0.4], dtype=np.float32)
    out = _normalize_weights(w)
    assert out.dtype == np.float64


def test_normalize_sums_to_one():
    """The normalized vector sums to 1.0 within float64 tolerance."""
    w = np.array([2.0, 3.0, 5.0], dtype=np.float64)
    out = _normalize_weights(w)
    assert abs(float(out.sum()) - 1.0) < 1e-12


def test_normalize_satisfies_multinomial_invariant_for_float32():
    """After normalization, sum(pvals[:-1]) <= 1.0 -- the exact numpy check that
    would otherwise raise for an overshooting float32 vector."""
    # Engineer many float32 vectors and assert the invariant holds post-normalize,
    # and that multinomial accepts them deterministically.
    for seed in range(500):
        rr = np.random.RandomState(seed)
        raw = (rr.random(rr.randint(2, 50)) + 1e-9).astype(np.float32)
        out = _normalize_weights(raw)
        assert float(out[:-1].sum()) <= 1.0
        # multinomial must accept it and preserve the total count
        draw = np.random.RandomState(seed).multinomial(37, out)
        assert int(draw.sum()) == 37


def test_normalize_is_byte_identical_for_normalized_float64():
    """For an already-normalized float64 vector, multinomial draws are unchanged
    (reproducibility guarantee: this hardening does not alter simulation output)."""
    for seed in range(1000):
        rr = np.random.RandomState(seed)
        raw = rr.random(rr.randint(2, 100)) + 1e-9
        w = raw / raw.sum()  # our pipeline pattern: flow / total (float64)
        count = int(rr.randint(1, 300))

        draw_current = np.random.RandomState(seed + 1).multinomial(count, w)
        draw_fixed = np.random.RandomState(seed + 1).multinomial(count, _normalize_weights(w))
        assert np.array_equal(draw_current, draw_fixed), (
            "Normalization changed the multinomial draw for seed %d" % seed)


def test_normalize_zero_total_does_not_divide():
    """An all-zero weight vector is returned as-is (no division by zero)."""
    w = np.zeros(4, dtype=np.float64)
    out = _normalize_weights(w)
    assert out.dtype == np.float64
    assert float(out.sum()) == 0.0
