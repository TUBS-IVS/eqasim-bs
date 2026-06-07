"""Tests for the income-coupled vehicle segment IPF.

``braunschweig.synthesis.vehicles.segment`` builds a ``segment x economic_status``
contingency that is raked to (a) the KBA FZ 27.10 segment marginal (exact) and
(b) the MiD ``P(status | segment, region)`` association, then exposes
``segment_probabilities(economic_status, raumtyp)`` -- a normalised pmf over
segments.

Covered properties:
  (a) marginal reproduction -- averaging ``segment_probabilities`` over the
      population's status distribution reproduces the KBA segment marginal;
  (b) monotonicity -- higher economic status puts more mass on upper segments
      (P(oberklasse | very_high) > P(oberklasse | very_low); mean segment rank
      increases with status);
  (c) every returned pmf sums to 1;
  (d) the raking / normalisation converges;
  (e) a missing (status, raumtyp) cell falls back to the NDS-only pmf and the
      primary-vs-fallback rate is logged.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data"
sys.path.insert(0, str(REPO))

from braunschweig.data.kba import fleet_tables as ft  # noqa: E402
from braunschweig.synthesis.vehicles import segment as seg  # noqa: E402

DATA_PATH = str(DATA)


@pytest.fixture(scope="module")
def model():
    return seg.SegmentModel.from_data_path(DATA_PATH)


# ---------------------------------------------------------------------------
# (c) every returned pmf is a valid probability vector
# ---------------------------------------------------------------------------
def test_every_pmf_sums_to_one(model):
    for status in ft.STATUS_LABELS:
        for rs7 in ft.RS7_TO_RAUMTYP_REGION:
            pmf = model.segment_probabilities(status, rs7)
            assert pmf.shape == (len(model.segments),)
            assert np.all(pmf >= -1e-12)
            assert pmf.sum() == pytest.approx(1.0, abs=1e-9)


def test_nds_only_pmf_sums_to_one(model):
    for status in ft.STATUS_LABELS:
        pmf = model.segment_probabilities(status, raumtyp=None)
        assert pmf.sum() == pytest.approx(1.0, abs=1e-9)


# ---------------------------------------------------------------------------
# (a) marginal reproduction
# ---------------------------------------------------------------------------
def test_population_average_reproduces_kba_segment_marginal(model):
    """Averaging segment_probabilities over the status distribution reproduces
    the KBA segment marginal (NDS base, no raumtyp tilt)."""
    # Population status distribution = the MiD NDS weighted-base status shares.
    status_dist = model.nds_status_distribution()
    assert status_dist.sum() == pytest.approx(1.0, abs=1e-9)

    implied = np.zeros(len(model.segments))
    for i, status in enumerate(ft.STATUS_LABELS):
        implied += status_dist[i] * model.segment_probabilities(status, raumtyp=None)
    implied /= implied.sum()

    kba = model.kba_segment_marginal()
    # The raking pins the marginal exactly for the consistent (NDS) status
    # distribution; allow a small tolerance for the residual rake gap.
    assert np.max(np.abs(implied - kba)) < 0.02


# ---------------------------------------------------------------------------
# (b) monotonicity: income coupling present
# ---------------------------------------------------------------------------
def test_oberklasse_mass_increases_with_status(model):
    ober = model.segments.index("oberklasse")
    low = model.segment_probabilities("very_low", raumtyp=None)[ober]
    high = model.segment_probabilities("very_high", raumtyp=None)[ober]
    assert high > low


def test_mean_segment_rank_increases_with_status(model):
    """The expected price-tier segment rank (small/cheap -> large/expensive)
    rises monotonically with economic status (NDS base). The diagnostic is over
    the price-tier segments only (utilities/sonstige/wohnmobile excluded)."""
    means = []
    for status in ft.STATUS_LABELS:
        pmf = model.segment_probabilities(status, raumtyp=None)
        means.append(model.mean_segment_rank(pmf))
    diffs = np.diff(means)
    # Strictly increasing across the 5 ordered status classes.
    assert np.all(diffs > 0), f"mean segment rank not monotone: {means}"


# ---------------------------------------------------------------------------
# (d) convergence
# ---------------------------------------------------------------------------
def test_raking_converges(model):
    assert model.rake_converged, (
        f"segment rake did not converge (max abs residual "
        f"{model.rake_residual:.3e})"
    )
    assert model.rake_residual < 1e-3


# ---------------------------------------------------------------------------
# (e) missing-cell fallback logged
# ---------------------------------------------------------------------------
def test_missing_raumtyp_cell_falls_back_to_nds_and_logs(model, caplog):
    # An RS7 code that has no raumtyp region mapping must fall back to NDS-only
    # and log the fallback (project no-silent-fallback rule).
    with caplog.at_level(logging.WARNING):
        pmf = model.segment_probabilities("medium", raumtyp=9999)
    nds = model.segment_probabilities("medium", raumtyp=None)
    assert np.allclose(pmf, nds)
    assert any("fallback" in r.message.lower() for r in caplog.records)


def test_fallback_rate_logged(model, caplog):
    # Drawing a batch with some unknown raumtyp codes logs an explicit
    # primary-vs-fallback rate.
    with caplog.at_level(logging.INFO):
        model.log_fallback_rate(
            [("medium", 71), ("high", 9999), ("low", 72), ("very_high", None)]
        )
    text = " ".join(r.message for r in caplog.records).lower()
    assert "primary" in text and "fallback" in text
