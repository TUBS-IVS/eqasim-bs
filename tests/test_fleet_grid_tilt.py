"""Tests for the sub-communal grid EV tilt logic (T9a).

``PowertrainModel._apply_grid_tilt`` multiplies the bev/phev mass by
``clip(grid_ev_share / gemeinde_grid_mean, 0.2, 5.0)`` when both params
are provided and the mean is > 0.  When either param is None, the mean is
<= 0, or grid_ev_share is NaN the pmf is returned unchanged and a fallback
is counted.

All tests use a minimal synthetic PowertrainModel built without any CSV
data -- the grid tilt logic is pure arithmetic on the pmf array, so no
derived CSVs are needed.

Key assertions:
  * grid_ev_share > gemeinde_grid_mean  -> bev/phev mass rises.
  * grid_ev_share < gemeinde_grid_mean  -> bev/phev mass falls.
  * ratio is clipped to [0.2, 5.0].
  * None params (either) -> pmf byte-identical to no-grid call (byte-identity
    for all EXISTING callers, which do not pass the new params).
  * gemeinde_grid_mean == 0 -> no-op + fallback, no ZeroDivisionError.
  * grid_ev_share NaN -> no-op + fallback, no NaN in output.
  * non-electric powertrain mass unchanged by the tilt.
  * _grid_primary / _grid_fallback counters updated correctly.
  * log_fallback_rate reports grid-tilt rate (no-silent-fallback rule).
"""

from __future__ import annotations

import logging
import math
import sys
from pathlib import Path

import numpy as np
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.synthesis.vehicles import fleet_sampling_de as fs  # noqa: E402


# ---------------------------------------------------------------------------
# Minimal synthetic PowertrainModel builder (no CSV data required).
# ---------------------------------------------------------------------------

def _minimal_model() -> fs.PowertrainModel:
    """Build a PowertrainModel with synthetic but valid internals.

    Uses the canonical POWERTRAINS order, a single segment ``kompaktklasse``,
    and a single Kreis ``03101`` with a simple uniform distribution over
    powertrains, so the test is independent of the real KBA data.  The
    Gemeinde electric share dict is intentionally empty so the Gemeinde tilt
    is always a fallback, keeping the baseline pmf purely determined by the
    synthetic Kreis matrix.
    """
    powertrains = list(fs.POWERTRAINS)        # canonical order, e.g. 9 items
    segments = ["kompaktklasse"]

    n_pt = len(powertrains)
    # Uniform distribution over powertrains for the single segment row.
    row = np.ones(n_pt, dtype=float) / n_pt
    kreis_matrix = row[np.newaxis, :].copy()  # shape (1, n_pt)

    return fs.PowertrainModel(
        segments=segments,
        powertrains=powertrains,
        kreis_segment_powertrain={"03101": kreis_matrix},
        national_segment_powertrain=kreis_matrix.copy(),
        kreis_private_electric_share={},
        gemeinde_private_electric_share={},
    )


def _powertrain_index(model: fs.PowertrainModel, pt: str) -> int:
    return model.powertrains.index(pt)


# ---------------------------------------------------------------------------
# Helper: get bev+phev mass from a normalised pmf.
# ---------------------------------------------------------------------------

def _electric_mass(model: fs.PowertrainModel, pmf: np.ndarray) -> float:
    bev = pmf[_powertrain_index(model, "bev")]
    phev = pmf[_powertrain_index(model, "phev")]
    return float(bev + phev)


def _non_electric_mass(model: fs.PowertrainModel, pmf: np.ndarray) -> float:
    electric_idx = {_powertrain_index(model, p) for p in fs.ELECTRIC_POWERTRAINS}
    return float(sum(pmf[i] for i in range(len(model.powertrains))
                     if i not in electric_idx))


# ---------------------------------------------------------------------------
# Baseline pmf (no grid params -> Gemeinde tilt fallback -> Kreis uniform pmf).
# ---------------------------------------------------------------------------

def _baseline_pmf(model: fs.PowertrainModel) -> np.ndarray:
    """powertrain_probabilities with no grid params: should be byte-identical to
    the pre-T9a pmf for all existing callers."""
    return model.powertrain_probabilities("kompaktklasse", "03101", None)


# ---------------------------------------------------------------------------
# T1: grid_ev_share > gemeinde_grid_mean -> bev/phev mass increases.
# ---------------------------------------------------------------------------

class TestGridTiltDirection:
    def test_higher_cell_share_increases_electric_mass(self):
        model = _minimal_model()
        base = _baseline_pmf(model)
        tilted = model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=0.10,
            gemeinde_grid_mean=0.05,
        )
        assert _electric_mass(model, tilted) > _electric_mass(model, base), (
            f"expected electric mass to rise: base={_electric_mass(model, base):.6f}, "
            f"tilted={_electric_mass(model, tilted):.6f}"
        )

    def test_lower_cell_share_decreases_electric_mass(self):
        model = _minimal_model()
        base = _baseline_pmf(model)
        tilted = model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=0.02,
            gemeinde_grid_mean=0.10,
        )
        assert _electric_mass(model, tilted) < _electric_mass(model, base), (
            f"expected electric mass to fall: base={_electric_mass(model, base):.6f}, "
            f"tilted={_electric_mass(model, tilted):.6f}"
        )

    def test_equal_cell_share_and_mean_is_noop(self):
        """If grid_ev_share == gemeinde_grid_mean the factor is 1.0 and the pmf
        is unchanged (up to floating-point precision)."""
        model = _minimal_model()
        base = _baseline_pmf(model)
        tilted = model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=0.07,
            gemeinde_grid_mean=0.07,
        )
        np.testing.assert_allclose(tilted, base, atol=1e-12)


# ---------------------------------------------------------------------------
# T2: None params -> byte-identical to baseline (existing caller identity).
# ---------------------------------------------------------------------------

class TestNoneParamsByteIdentity:
    def test_no_grid_params_byte_identical(self):
        """Calling powertrain_probabilities without the new grid params must
        produce a result numerically identical to the baseline call (existing
        caller contract).
        """
        model = _minimal_model()
        without_params = model.powertrain_probabilities(
            "kompaktklasse", "03101", None)
        with_none_params = model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=None,
            gemeinde_grid_mean=None,
        )
        np.testing.assert_array_equal(without_params, with_none_params)

    def test_grid_ev_share_none_only(self):
        """grid_ev_share=None with a valid mean -> no-op."""
        model = _minimal_model()
        base = _baseline_pmf(model)
        tilted = model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=None,
            gemeinde_grid_mean=0.05,
        )
        np.testing.assert_array_equal(tilted, base)

    def test_gemeinde_grid_mean_none_only(self):
        """gemeinde_grid_mean=None with a valid share -> no-op."""
        model = _minimal_model()
        base = _baseline_pmf(model)
        tilted = model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=0.10,
            gemeinde_grid_mean=None,
        )
        np.testing.assert_array_equal(tilted, base)


# ---------------------------------------------------------------------------
# T3: gemeinde_grid_mean == 0 -> no-op + fallback, no ZeroDivisionError.
# ---------------------------------------------------------------------------

class TestZeroMeanHandling:
    def test_zero_mean_returns_unchanged(self):
        model = _minimal_model()
        base = _baseline_pmf(model)
        tilted = model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=0.05,
            gemeinde_grid_mean=0.0,
        )
        np.testing.assert_array_equal(tilted, base)

    def test_zero_mean_counts_fallback(self):
        model = _minimal_model()
        before = model._grid_fallback
        model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=0.05,
            gemeinde_grid_mean=0.0,
        )
        assert model._grid_fallback == before + 1

    def test_zero_mean_no_primary_increment(self):
        model = _minimal_model()
        before = model._grid_primary
        model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=0.05,
            gemeinde_grid_mean=0.0,
        )
        assert model._grid_primary == before


# ---------------------------------------------------------------------------
# T4: grid_ev_share NaN -> no-op + fallback, no NaN in output.
# ---------------------------------------------------------------------------

class TestNanShareHandling:
    def test_nan_share_returns_unchanged(self):
        model = _minimal_model()
        base = _baseline_pmf(model)
        tilted = model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=float("nan"),
            gemeinde_grid_mean=0.05,
        )
        np.testing.assert_array_equal(tilted, base)

    def test_nan_share_counts_fallback(self):
        model = _minimal_model()
        before = model._grid_fallback
        model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=float("nan"),
            gemeinde_grid_mean=0.05,
        )
        assert model._grid_fallback == before + 1

    def test_nan_share_output_has_no_nans(self):
        model = _minimal_model()
        result = model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=float("nan"),
            gemeinde_grid_mean=0.05,
        )
        assert not np.any(np.isnan(result))


# ---------------------------------------------------------------------------
# T5: non-electric powertrain mass unchanged by grid tilt.
# ---------------------------------------------------------------------------

class TestNonElectricUnchanged:
    def test_non_electric_mass_unchanged(self):
        """The tilt only touches bev/phev; petrol/diesel/hybrid etc. should
        have the same RELATIVE mass after renormalisation.  Check that the
        non-electric-to-non-electric ratios are preserved.
        """
        model = _minimal_model()
        base = _baseline_pmf(model)
        tilted = model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=0.15,
            gemeinde_grid_mean=0.05,
        )
        # All non-electric powertrains should scale identically (because only
        # bev/phev are multiplied before renormalisation).  Check ratio
        # between any two non-electric powertrains is preserved.
        non_elec = [
            p for p in model.powertrains if p not in fs.ELECTRIC_POWERTRAINS
        ]
        if len(non_elec) < 2:
            pytest.skip("fewer than 2 non-electric powertrains in this model")
        i0, i1 = (_powertrain_index(model, non_elec[0]),
                  _powertrain_index(model, non_elec[1]))
        # In the uniform model every powertrain starts with equal mass, so
        # ratios are all 1.0; after renorm they should stay 1.0.
        ratio_base = base[i0] / base[i1] if base[i1] > 0 else float("inf")
        ratio_tilt = (tilted[i0] / tilted[i1] if tilted[i1] > 0
                      else float("inf"))
        assert ratio_tilt == pytest.approx(ratio_base, rel=1e-9)


# ---------------------------------------------------------------------------
# T6: ratio clipping at [0.2, 5.0].
# ---------------------------------------------------------------------------

class TestRatioClipping:
    def _electric_factor(self, model: fs.PowertrainModel,
                         base: np.ndarray, tilted: np.ndarray) -> float:
        """Ratio of bev mass (tilted / base) before renormalisation is used
        to verify clipping.  We back-compute the effective factor by looking
        at the ratio of bev probability after adjusting for renormalisation.
        """
        # In a uniform base pmf the non-electric mass is untouched (as raw
        # counts before renorm).  We can therefore recover the factor from
        # the bev ratio relative to any non-electric powertrain.
        bev_idx = _powertrain_index(model, "bev")
        non_elec = [p for p in model.powertrains
                    if p not in fs.ELECTRIC_POWERTRAINS]
        assert non_elec, "model has no non-electric powertrains"
        ref_idx = _powertrain_index(model, non_elec[0])
        # ratio (bev/ref) in tilted vs base gives the factor applied to bev
        # (because ref is unscaled and both are renormalised by the same sum).
        bev_base_rel = base[bev_idx] / base[ref_idx]
        bev_tilt_rel = tilted[bev_idx] / tilted[ref_idx]
        return float(bev_tilt_rel / bev_base_rel)

    def test_clip_upper_at_5(self):
        """grid_ev_share / mean = 100 -> factor clipped to 5.0."""
        model = _minimal_model()
        base = _baseline_pmf(model)
        # 100x ratio -> should clip to 5.0
        tilted = model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=1.0,
            gemeinde_grid_mean=0.01,
        )
        factor = self._electric_factor(model, base, tilted)
        assert factor == pytest.approx(5.0, rel=1e-6), (
            f"expected clip at 5.0, got factor={factor:.6f}"
        )

    def test_clip_lower_at_0_2(self):
        """grid_ev_share / mean = 0.01 -> factor clipped to 0.2."""
        model = _minimal_model()
        base = _baseline_pmf(model)
        # 0.01 ratio -> should clip to 0.2
        tilted = model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=0.001,
            gemeinde_grid_mean=0.1,
        )
        factor = self._electric_factor(model, base, tilted)
        assert factor == pytest.approx(0.2, rel=1e-6), (
            f"expected clip at 0.2, got factor={factor:.6f}"
        )

    def test_unclipped_ratio(self):
        """A ratio of 2.0 (within the band) must be applied exactly."""
        model = _minimal_model()
        base = _baseline_pmf(model)
        tilted = model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=0.10,
            gemeinde_grid_mean=0.05,
        )
        factor = self._electric_factor(model, base, tilted)
        assert factor == pytest.approx(2.0, rel=1e-6), (
            f"expected factor=2.0, got {factor:.6f}"
        )


# ---------------------------------------------------------------------------
# T7: counters incremented correctly.
# ---------------------------------------------------------------------------

class TestCounters:
    def test_primary_counter_incremented_on_valid_call(self):
        model = _minimal_model()
        before = model._grid_primary
        model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=0.08,
            gemeinde_grid_mean=0.04,
        )
        assert model._grid_primary == before + 1
        assert model._grid_fallback == 0

    def test_fallback_counter_incremented_on_none(self):
        model = _minimal_model()
        before = model._grid_fallback
        model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=None,
            gemeinde_grid_mean=0.04,
        )
        assert model._grid_fallback == before + 1
        assert model._grid_primary == 0

    def test_no_counter_increment_without_grid_params(self):
        """When grid params are not passed at all, neither counter changes."""
        model = _minimal_model()
        model.powertrain_probabilities("kompaktklasse", "03101", None)
        assert model._grid_primary == 0
        assert model._grid_fallback == 0

    def test_counters_accumulate_across_calls(self):
        model = _minimal_model()
        # 3 primary calls
        for _ in range(3):
            model.powertrain_probabilities(
                "kompaktklasse", "03101", None,
                grid_ev_share=0.08, gemeinde_grid_mean=0.04,
            )
        # 2 fallback calls (None share)
        for _ in range(2):
            model.powertrain_probabilities(
                "kompaktklasse", "03101", None,
                grid_ev_share=None, gemeinde_grid_mean=0.04,
            )
        assert model._grid_primary == 3
        assert model._grid_fallback == 2


# ---------------------------------------------------------------------------
# T8: log_fallback_rate reports the grid-tilt rate.
# ---------------------------------------------------------------------------

class TestLogFallbackRate:
    def test_grid_tilt_rate_in_log(self, caplog):
        """log_fallback_rate must mention the grid-tilt primary/fallback counts
        (no-silent-fallback rule: every fallback path must be observable)."""
        model = _minimal_model()
        model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=0.08, gemeinde_grid_mean=0.04,
        )
        model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=None, gemeinde_grid_mean=0.04,
        )
        with caplog.at_level(logging.INFO):
            model.log_fallback_rate()
        text = " ".join(r.message for r in caplog.records).lower()
        # Must mention the grid tilt context.
        assert "grid" in text, (
            f"'grid' not found in log output: {text!r}"
        )
        # Must mention both primary and fallback counts/rates.
        assert "primary" in text
        assert "fallback" in text

    def test_log_fallback_rate_no_error_with_zero_total(self, caplog):
        """log_fallback_rate with zero grid calls must not crash (division by
        zero guard)."""
        model = _minimal_model()
        with caplog.at_level(logging.INFO):
            model.log_fallback_rate()  # no calls made -> 0/0 must be handled


# ---------------------------------------------------------------------------
# T9: pmf always sums to 1 (renormalisation).
# ---------------------------------------------------------------------------

class TestPmfNormalisation:
    @pytest.mark.parametrize("grid_share,mean", [
        (0.05, 0.10),   # below mean
        (0.10, 0.05),   # above mean
        (0.10, 0.10),   # equal
        (1.0, 0.01),    # clip upper
        (0.001, 0.1),   # clip lower
    ])
    def test_pmf_sums_to_one(self, grid_share, mean):
        model = _minimal_model()
        pmf = model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=grid_share,
            gemeinde_grid_mean=mean,
        )
        assert pmf.sum() == pytest.approx(1.0, abs=1e-9)

    def test_pmf_sums_to_one_on_fallback(self):
        model = _minimal_model()
        pmf = model.powertrain_probabilities(
            "kompaktklasse", "03101", None,
            grid_ev_share=None,
            gemeinde_grid_mean=0.05,
        )
        assert pmf.sum() == pytest.approx(1.0, abs=1e-9)
