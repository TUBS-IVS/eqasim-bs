"""Tests for the per-Gemeinde BEV/PHEV composition tilt (issue #317).

ADR-0086 had to tilt BOTH electric powertrains by ONE combined factor because
the 2026 per-Gemeinde EV export publishes a literal ``0`` in every BEV /
plug-in-hybrid column. That keeps the electric LEVEL signal but discards the
STRUCTURE signal -- where BEV rather than PHEV dominates. This module covers the
restoration of that structure from the FZ 27.17 private-car table, which still
carries both counts per Gemeinde.

Covers:
  * ``_gemeinde_electric_composition``: the per-Gemeinde composition factors and
    the per-Kreis reference BEV fraction, including the load-bearing
    ELECTRIC-stock weighting (total-car-stock weighting does NOT average to 1);
  * clip + renormalisation, so a measured zero-PHEV Gemeinde is floored rather
    than fully suppressed while the Kreis mean stays exactly 1.0;
  * ``PowertrainModel._apply_gemeinde_tilt``: BEV and PHEV now receive DIFFERENT
    factors, the Kreis electric aggregate is preserved, and the OFF path is
    byte-identical to the pre-#317 behaviour;
  * the committed FZ 27.17 table's measured coverage (105/113 usable).
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data"
sys.path.insert(0, str(REPO))

from braunschweig.synthesis.vehicles import fleet_sampling_de as fs  # noqa: E402

FZ2717_CSV = DATA / "braunschweig" / "kba" / "derived" / "kba_gemeinde_private_bev.csv"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fz_row(kreis_ags5: str, gemeinde: str, private_total: float,
            private_bev, private_phev) -> dict:
    """One FZ 27.17 ``kba_gemeinde_private_bev.csv`` row.

    The share columns are derived from the counts exactly as the committed table
    does (verified: ``|private_bev/private_total - private_bev_share| < 1e-16``),
    so a fixture can never drift from the real table's internal consistency.
    """
    def _share(count):
        return np.nan if pd.isna(count) else float(count) / float(private_total)

    return {
        "kreis_ags5": kreis_ags5,
        "kreis_name": "Kreis " + kreis_ags5,
        "gemeinde": gemeinde,
        "private_total": float(private_total),
        "private_bev": private_bev,
        "private_phev": private_phev,
        "private_bev_share": _share(private_bev),
        "private_phev_share": _share(private_phev),
    }


def _weighted_mean(per_gemeinde: dict, weights: dict, kreis_ags5: str,
                   powertrain: str) -> float:
    """Electric-stock-weighted mean of one powertrain's factor across a Kreis."""
    num = 0.0
    den = 0.0
    for (kreis, gem), factors in per_gemeinde.items():
        if kreis != kreis_ags5:
            continue
        weight = weights[(kreis, gem)]
        num += weight * factors[powertrain]
        den += weight
    return num / den


# ---------------------------------------------------------------------------
# Unit tests: _gemeinde_electric_composition
# ---------------------------------------------------------------------------

class TestGemeindeElectricComposition:
    """The builder that turns FZ 27.17 counts into within-Kreis composition factors."""

    def test_factors_are_the_ratio_of_bev_fractions(self):
        """f_bev = bev_frac(Gemeinde) / bev_frac(Kreis), f_phev the complement.

        Two equally sized Gemeinden, one BEV-heavy (80 % of its electric stock)
        and one PHEV-heavy (60 %). The electric-stock-weighted Kreis BEV fraction
        is (80+60)/200 = 0.70, so the factors are hand-computable.
        """
        df = pd.DataFrame([
            _fz_row("03158", "ALPHA", 1000, 80, 20),
            _fz_row("03158", "BETA", 1000, 60, 40),
        ])
        per_gemeinde, per_kreis = fs._gemeinde_electric_composition(df)

        assert per_kreis["03158"] == pytest.approx(0.70)
        assert per_gemeinde[("03158", "ALPHA")]["bev"] == pytest.approx(0.80 / 0.70)
        assert per_gemeinde[("03158", "ALPHA")]["phev"] == pytest.approx(0.20 / 0.30)
        assert per_gemeinde[("03158", "BETA")]["bev"] == pytest.approx(0.60 / 0.70)
        assert per_gemeinde[("03158", "BETA")]["phev"] == pytest.approx(0.40 / 0.30)

    def test_electric_stock_weighted_kreis_mean_is_exactly_one(self):
        """The invariance the whole design rests on, with UNEQUAL Gemeinde sizes.

        ADR-0085 rakes onto the TILTED mean, so a composition tilt whose Kreis
        mean is not 1.0 would shift the per-Kreis BEV aggregate and the rake
        would then PRESERVE that shift rather than correct it.
        """
        df = pd.DataFrame([
            _fz_row("03158", "BIG", 50000, 800, 200),     # electric stock 1000
            _fz_row("03158", "MID", 5000, 60, 40),        # electric stock  100
            _fz_row("03158", "SMALL", 500, 6, 14),        # electric stock   20
        ])
        per_gemeinde, _ = fs._gemeinde_electric_composition(df)
        weights = {("03158", "BIG"): 1000.0, ("03158", "MID"): 100.0,
                   ("03158", "SMALL"): 20.0}

        assert _weighted_mean(per_gemeinde, weights, "03158", "bev") == pytest.approx(1.0, abs=1e-12)
        assert _weighted_mean(per_gemeinde, weights, "03158", "phev") == pytest.approx(1.0, abs=1e-12)

    def test_total_car_stock_weighting_would_not_average_to_one(self):
        """Pins WHY the weight is the electric stock and not ``private_total``.

        Same frame as above. Weighting the very same factors by each Gemeinde's
        TOTAL car stock (the intuitive but wrong choice named in issue #317)
        gives a mean materially away from 1.0 -- i.e. a silent per-Kreis BEV
        level shift. This test fails if someone "simplifies" the weight later.
        """
        df = pd.DataFrame([
            _fz_row("03158", "BIG", 50000, 800, 200),
            _fz_row("03158", "MID", 5000, 60, 40),
            _fz_row("03158", "SMALL", 500, 6, 14),
        ])
        per_gemeinde, _ = fs._gemeinde_electric_composition(df)
        total_car_weights = {("03158", "BIG"): 50000.0, ("03158", "MID"): 5000.0,
                             ("03158", "SMALL"): 500.0}

        wrong = _weighted_mean(per_gemeinde, total_car_weights, "03158", "phev")
        assert abs(wrong - 1.0) > 0.01, (
            "total-car-stock weighting must NOT reproduce the invariance; if it "
            "does, this fixture no longer discriminates the two weightings"
        )

    def test_single_gemeinde_kreis_gets_unit_factors(self):
        """A kreisfreie Stadt is its own Kreis mean, so both factors are 1.0."""
        df = pd.DataFrame([_fz_row("03101", "BRAUNSCHWEIG", 110515, 2646, 991)])
        per_gemeinde, _ = fs._gemeinde_electric_composition(df)

        assert per_gemeinde[("03101", "BRAUNSCHWEIG")]["bev"] == pytest.approx(1.0)
        assert per_gemeinde[("03101", "BRAUNSCHWEIG")]["phev"] == pytest.approx(1.0)

    def test_missing_phev_count_yields_no_entry(self):
        """A Gemeinde with a suppressed PHEV count has no usable composition."""
        df = pd.DataFrame([
            _fz_row("03154", "BEIERSTEDT", 253, 7, np.nan),
            _fz_row("03154", "OTHER", 1000, 60, 40),
        ])
        per_gemeinde, _ = fs._gemeinde_electric_composition(df)

        assert ("03154", "BEIERSTEDT") not in per_gemeinde
        assert ("03154", "OTHER") in per_gemeinde

    def test_missing_bev_count_yields_no_entry(self):
        """Wolfsburg / Hedeper case: the BEV count is the suppressed one."""
        df = pd.DataFrame([
            _fz_row("03158", "HEDEPER", 308, np.nan, 4),
            _fz_row("03158", "OTHER", 1000, 60, 40),
        ])
        per_gemeinde, _ = fs._gemeinde_electric_composition(df)

        assert ("03158", "HEDEPER") not in per_gemeinde

    def test_excluded_gemeinden_do_not_enter_the_kreis_mean(self):
        """A Gemeinde without a usable composition must not skew the reference.

        It receives factor 1.0 at draw time, so leaving it out of the mean is
        what keeps the overall Kreis mean at 1.0.
        """
        df = pd.DataFrame([
            _fz_row("03154", "ALPHA", 1000, 80, 20),
            _fz_row("03154", "BETA", 1000, 60, 40),
            _fz_row("03154", "NOPHEV", 1000, 90, np.nan),
        ])
        _, per_kreis = fs._gemeinde_electric_composition(df)

        # 0.70 is the mean of ALPHA+BETA only; including NOPHEV's 90 BEV would
        # push it to (80+60+90)/(100+100+90) = 0.793.
        assert per_kreis["03154"] == pytest.approx(0.70)

    def test_measured_zero_phev_is_floored_not_suppressed(self):
        """Dorstadt / Roklum: PHEV count is a measured 0, so bev_frac == 1.0.

        ADR-0086 decision 1 treats a zero INSIDE an informative column as a
        measurement handled by the documented 0.2 clip floor. A raw factor of
        0.0 would wipe the PHEV mass out of that Gemeinde entirely on a ~14-car
        electric stock.
        """
        df = pd.DataFrame([
            _fz_row("03158", "DORSTADT", 461, 14, 0),
            _fz_row("03158", "BULK", 100000, 2000, 1000),
        ])
        per_gemeinde, _ = fs._gemeinde_electric_composition(df)

        f_phev = per_gemeinde[("03158", "DORSTADT")]["phev"]
        assert f_phev > 0.0, "a measured zero must be floored, not applied as 0"
        assert f_phev == pytest.approx(fs.GEMEINDE_TILT_CLIP[0], rel=0.05)

    def test_clipped_factors_are_renormalised_to_mean_one(self):
        """Clipping injects mass; the renormalisation must take it back out.

        Without renormalisation the floored PHEV factor raises the Kreis mean
        above 1.0 and the ADR-0085 rake would preserve that as a PHEV level
        shift.
        """
        df = pd.DataFrame([
            _fz_row("03158", "DORSTADT", 461, 14, 0),
            _fz_row("03158", "BULK", 100000, 2000, 1000),
        ])
        per_gemeinde, _ = fs._gemeinde_electric_composition(df)
        weights = {("03158", "DORSTADT"): 14.0, ("03158", "BULK"): 3000.0}

        assert _weighted_mean(per_gemeinde, weights, "03158", "phev") == pytest.approx(1.0, abs=1e-12)
        assert _weighted_mean(per_gemeinde, weights, "03158", "bev") == pytest.approx(1.0, abs=1e-12)

    def test_kreise_are_independent(self):
        """Each Kreis is normalised against its OWN mean, never a pooled one."""
        df = pd.DataFrame([
            _fz_row("03151", "A1", 1000, 90, 10),
            _fz_row("03151", "A2", 1000, 70, 30),
            _fz_row("03158", "B1", 1000, 50, 50),
            _fz_row("03158", "B2", 1000, 30, 70),
        ])
        _, per_kreis = fs._gemeinde_electric_composition(df)

        assert per_kreis["03151"] == pytest.approx(0.80)
        assert per_kreis["03158"] == pytest.approx(0.40)

    def test_empty_frame_returns_empty_maps(self):
        df = pd.DataFrame(columns=[
            "kreis_ags5", "kreis_name", "gemeinde", "private_total",
            "private_bev", "private_phev", "private_bev_share",
            "private_phev_share"])
        per_gemeinde, per_kreis = fs._gemeinde_electric_composition(df)

        assert per_gemeinde == {}
        assert per_kreis == {}

    def test_keys_use_the_canonical_gemeinde_normalisation(self):
        """The map must join the population's normalised Gemeinde label."""
        df = pd.DataFrame([
            _fz_row("03151", "GIFHORN,ST.", 1000, 80, 20),
            _fz_row("03151", "BROME,FLECKEN", 1000, 60, 40),
        ])
        per_gemeinde, _ = fs._gemeinde_electric_composition(df)

        assert ("03151", "GIFHORN") in per_gemeinde
        assert ("03151", "BROME") in per_gemeinde

    def test_fallback_rate_is_logged(self, caplog):
        """No-silent-fallback rule: excluded Gemeinden are counted and named."""
        df = pd.DataFrame([
            _fz_row("03154", "ALPHA", 1000, 80, 20),
            _fz_row("03154", "BEIERSTEDT", 253, 7, np.nan),
        ])
        with caplog.at_level(logging.INFO, logger=fs.logger.name):
            fs._gemeinde_electric_composition(df)

        text = caplog.text
        assert "composition" in text.lower()
        assert "1/2" in text or "1 (" in text


# ---------------------------------------------------------------------------
# Integration: the tilt actually differentiates BEV from PHEV
# ---------------------------------------------------------------------------

class TestCompositionTiltApplication:
    """``PowertrainModel._apply_gemeinde_tilt`` with the composition factors."""

    POWERTRAINS = ["petrol", "diesel", "bev", "phev", "hybrid", "gas", "other", "hydrogen"]

    def _model(self, composition_on: bool = True,
               base_bev: float = 0.075, base_phev: float = 0.025) -> fs.PowertrainModel:
        """Two Gemeinden in one Kreis, identical combined EV share.

        With an identical combined share the ADR-0086 tilt gives both Gemeinden
        the SAME factor for bev and phev; only the composition tilt can make
        them differ.

        The default base pmf splits its electric mass 75:25, matching the FZ
        27.17 Kreis composition of this fixture (150 BEV : 50 PHEV). That is the
        realistic case -- measured on the committed tables, the 46251-02
        all-ownership BEV fraction and the FZ 27.17 private one agree to within
        1-5 pp per ZGB Kreis. ``base_bev``/``base_phev`` let one test drive them
        deliberately apart to pin the residual that gap produces.
        """
        df = pd.DataFrame([
            _fz_row("03158", "BEVTOWN", 1000, 90, 10),
            _fz_row("03158", "PHEVTOWN", 1000, 60, 40),
        ])
        per_gemeinde, per_kreis = fs._gemeinde_electric_composition(df)
        idx = {p: i for i, p in enumerate(self.POWERTRAINS)}
        base = np.zeros(len(self.POWERTRAINS))
        base[idx["petrol"]] = 1.0 - base_bev - base_phev
        base[idx["bev"]] = base_bev
        base[idx["phev"]] = base_phev
        return fs.PowertrainModel(
            segments=["klein"],
            powertrains=list(self.POWERTRAINS),
            kreis_segment_powertrain={"03158": base.reshape(1, -1)},
            national_segment_powertrain=base.reshape(1, -1),
            kreis_private_electric_share={"03158": {fs.COMBINED_ELECTRIC_KEY: 0.10}},
            gemeinde_private_electric_share={
                ("03158", "BEVTOWN"): {fs.COMBINED_ELECTRIC_KEY: 0.10},
                ("03158", "PHEVTOWN"): {fs.COMBINED_ELECTRIC_KEY: 0.10},
            },
            gemeinde_electric_composition=per_gemeinde,
            kreis_electric_composition=per_kreis,
            gemeinde_bev_composition_tilt=composition_on,
        )

    def test_bev_and_phev_receive_different_factors(self):
        """The defect issue #317 fixes: today both are tilted by ONE factor."""
        model = self._model()
        idx = {p: i for i, p in enumerate(self.POWERTRAINS)}

        bev_town = model.powertrain_probabilities("klein", "03158", "BEVTOWN")
        phev_town = model.powertrain_probabilities("klein", "03158", "PHEVTOWN")

        bev_ratio = bev_town[idx["bev"]] / phev_town[idx["bev"]]
        phev_ratio = bev_town[idx["phev"]] / phev_town[idx["phev"]]
        assert bev_ratio > 1.0, "the BEV-heavy Gemeinde must get more BEV mass"
        assert phev_ratio < 1.0, "the BEV-heavy Gemeinde must get less PHEV mass"

    def test_electric_total_per_gemeinde_is_not_moved_by_composition(self):
        """Composition redistributes WITHIN the electric mass, it does not add.

        Both Gemeinden have the same combined EV share, so both must keep the
        same TOTAL electric probability; only its BEV:PHEV split differs.
        """
        model = self._model()
        idx = {p: i for i, p in enumerate(self.POWERTRAINS)}

        bev_town = model.powertrain_probabilities("klein", "03158", "BEVTOWN")
        phev_town = model.powertrain_probabilities("klein", "03158", "PHEVTOWN")

        electric_a = bev_town[idx["bev"]] + bev_town[idx["phev"]]
        electric_b = phev_town[idx["bev"]] + phev_town[idx["phev"]]
        assert electric_a == pytest.approx(electric_b, rel=1e-12)

    def test_electric_total_preserved_even_when_base_split_differs(self):
        """The level guarantee must not rest on base and FZ splits agreeing.

        Driven deliberately apart (base 60:40 against an FZ Kreis 75:25), the
        per-Gemeinde electric TOTAL must still be untouched -- that separation is
        a construction property of the rescale, not a property of the data.
        """
        model = self._model(base_bev=0.06, base_phev=0.04)
        off = self._model(composition_on=False, base_bev=0.06, base_phev=0.04)
        idx = {p: i for i, p in enumerate(self.POWERTRAINS)}

        for gemeinde in ("BEVTOWN", "PHEVTOWN"):
            tilted = model.powertrain_probabilities("klein", "03158", gemeinde)
            untilted = off.powertrain_probabilities("klein", "03158", gemeinde)
            assert (tilted[idx["bev"]] + tilted[idx["phev"]]) == pytest.approx(
                untilted[idx["bev"]] + untilted[idx["phev"]], rel=1e-12)

    def test_kreis_electric_aggregate_is_preserved(self):
        """Acceptance criterion: the Kreis BEV and PHEV aggregates are unchanged.

        Averaging the tilted pmf over the Kreis's cars, weighted by each
        Gemeinde's electric stock, must reproduce the untilted Kreis pmf -- that
        is what stops the ADR-0085 rake from locking in a shift.
        """
        model = self._model()
        off = self._model(composition_on=False)
        idx = {p: i for i, p in enumerate(self.POWERTRAINS)}
        weights = {"BEVTOWN": 100.0, "PHEVTOWN": 100.0}

        def kreis_mean(m):
            acc = np.zeros(len(self.POWERTRAINS))
            total = 0.0
            for gem, weight in weights.items():
                acc += weight * m.powertrain_probabilities("klein", "03158", gem)
                total += weight
            return acc / total

        tilted = kreis_mean(model)
        untilted = kreis_mean(off)
        assert tilted[idx["bev"]] == pytest.approx(untilted[idx["bev"]], rel=1e-12)
        assert tilted[idx["phev"]] == pytest.approx(untilted[idx["phev"]], rel=1e-12)

    def test_base_split_gap_leaves_only_a_second_order_residual(self):
        """Honest bound on the one thing the design does NOT preserve exactly.

        The composition factors average to 1.0 under the FZ 27.17 electric-stock
        weighting, but they act on a pmf whose BEV:PHEV split comes from
        46251-02 (all ownership). Where the two disagree, the realised Kreis
        BEV:PHEV ratio moves slightly. This pins that the effect is second-order
        even under a 15 pp source gap far larger than any measured on the
        committed tables (<= 5.04 pp; measured worst-case residual 0.0224 pp of
        the all-car fleet in Kreis 03154, ADR-0124).
        """
        model = self._model(base_bev=0.06, base_phev=0.04)
        off = self._model(composition_on=False, base_bev=0.06, base_phev=0.04)
        idx = {p: i for i, p in enumerate(self.POWERTRAINS)}
        weights = {"BEVTOWN": 100.0, "PHEVTOWN": 100.0}

        def kreis_mean(m):
            acc = np.zeros(len(self.POWERTRAINS))
            for gemeinde, weight in weights.items():
                acc += weight * m.powertrain_probabilities("klein", "03158", gemeinde)
            return acc / sum(weights.values())

        tilted, untilted = kreis_mean(model), kreis_mean(off)
        # In PERCENTAGE POINTS of the all-car fleet, not relative to the share.
        assert abs(tilted[idx["bev"]] - untilted[idx["bev"]]) < 0.005
        assert abs(tilted[idx["phev"]] - untilted[idx["phev"]]) < 0.005

    def test_flag_off_reproduces_the_pre_317_tilt(self):
        """Explicit OFF path: byte-identical to the combined-share-only tilt."""
        off = self._model(composition_on=False)
        idx = {p: i for i, p in enumerate(self.POWERTRAINS)}

        bev_town = off.powertrain_probabilities("klein", "03158", "BEVTOWN")
        phev_town = off.powertrain_probabilities("klein", "03158", "PHEVTOWN")

        np.testing.assert_allclose(bev_town, phev_town)

    def test_gemeinde_without_composition_falls_back_to_unit_factor(self):
        """The 8 known Gemeinden keep today's behaviour, counted separately."""
        model = self._model()
        model.gemeinde_private_electric_share[("03158", "NOCOMP")] = {
            fs.COMBINED_ELECTRIC_KEY: 0.10}
        off = self._model(composition_on=False)

        with_comp = model.powertrain_probabilities("klein", "03158", "NOCOMP")
        without = off.powertrain_probabilities("klein", "03158", "BEVTOWN")

        np.testing.assert_allclose(with_comp, without)
        assert model._gemeinde_composition_fallback == 1
        assert model._gemeinde_composition_primary == 0

    def test_composition_counters_report_primary_hits(self):
        model = self._model()
        model.powertrain_probabilities("klein", "03158", "BEVTOWN")
        model.powertrain_probabilities("klein", "03158", "PHEVTOWN")

        assert model._gemeinde_composition_primary == 2
        assert model._gemeinde_composition_fallback == 0

    def test_composition_rate_is_logged(self, caplog):
        model = self._model()
        model.powertrain_probabilities("klein", "03158", "BEVTOWN")
        with caplog.at_level(logging.INFO, logger=fs.logger.name):
            model.log_fallback_rate("residents")

        assert "composition" in caplog.text.lower()

    def test_per_powertrain_source_takes_precedence_over_composition(self):
        """A future KBA edition with real BEV/PHEV columns must not double-tilt.

        When the per-powertrain path fires, the composition factor is already
        contained in the source's own split and must NOT be applied again.
        """
        model = self._model()
        idx = {p: i for i, p in enumerate(self.POWERTRAINS)}
        # Give BEVTOWN a real per-powertrain split identical to the Kreis one:
        # the tilt must then be a no-op on the electric mass.
        model.kreis_private_electric_share["03158"] = {
            "bev": 0.06, "phev": 0.04, fs.COMBINED_ELECTRIC_KEY: 0.10}
        model.gemeinde_private_electric_share[("03158", "BEVTOWN")] = {
            "bev": 0.06, "phev": 0.04, fs.COMBINED_ELECTRIC_KEY: 0.10}

        tilted = model.powertrain_probabilities("klein", "03158", "BEVTOWN")
        assert tilted[idx["bev"]] == pytest.approx(0.075, rel=1e-9)
        assert tilted[idx["phev"]] == pytest.approx(0.025, rel=1e-9)

    def test_half_restored_source_does_not_apply_half_the_composition(self):
        """A source restoring only ONE electric column must disable composition.

        Otherwise bev would take the per-powertrain path while phev took the
        combined one, applying half the composition pair: neither coherent
        structure nor a preserved electric total. Unreachable with the current
        source, guarded because the failure would be silent.
        """
        model = self._model()
        idx = {p: i for i, p in enumerate(self.POWERTRAINS)}
        # Only BEV gets a real per-powertrain share; PHEV keeps the combined one.
        model.kreis_private_electric_share["03158"] = {
            "bev": 0.075, fs.COMBINED_ELECTRIC_KEY: 0.10}
        model.gemeinde_private_electric_share[("03158", "BEVTOWN")] = {
            "bev": 0.075, fs.COMBINED_ELECTRIC_KEY: 0.10}

        tilted = model.powertrain_probabilities("klein", "03158", "BEVTOWN")

        assert tilted[idx["bev"]] == pytest.approx(0.075, rel=1e-9)
        assert tilted[idx["phev"]] == pytest.approx(0.025, rel=1e-9)
        assert model._gemeinde_composition_fallback == 1
        assert model._gemeinde_composition_primary == 0


# ---------------------------------------------------------------------------
# The committed reference table (data-gated, but asserted when present)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not FZ2717_CSV.exists(),
                    reason="kba_gemeinde_private_bev.csv absent (local-only data)")
def test_committed_fz2717_coverage_is_105_of_113():
    """Pins the measured data situation recorded in ADR-0124 / issue #317.

    Not a target to hit but a fact to notice: if a KBA refresh changes these
    counts, the ADR's named fallback list is stale and must be revisited.
    """
    df = pd.read_csv(FZ2717_CSV, dtype={"kreis_ags5": str})
    assert len(df) == 113

    per_gemeinde, _ = fs._gemeinde_electric_composition(df)
    assert len(per_gemeinde) == 105

    covered = {gemeinde for (_, gemeinde) in per_gemeinde}
    excluded = {fs.normalize_gemeinde_name(g) for g in df["gemeinde"]} - covered
    # Six have a suppressed PHEV count, two (Wolfsburg, Hedeper) a suppressed
    # BEV count. Dorstadt and Roklum are NOT here: their zero PHEV count is a
    # measurement, so they carry a usable (floored) composition.
    assert excluded == {
        "WOLFSBURG", "HEDEPER", "BEIERSTEDT", "JERXHEIM", "QUERENHORST",
        "DAHLUM", "UEHRDE", "WINNIGSTEDT",
    }


@pytest.mark.skipif(not FZ2717_CSV.exists(),
                    reason="kba_gemeinde_private_bev.csv absent (local-only data)")
def test_committed_fz2717_factors_average_to_one_per_kreis():
    """The invariance holds on the REAL table, not only on fixtures."""
    df = pd.read_csv(FZ2717_CSV, dtype={"kreis_ags5": str})
    per_gemeinde, _ = fs._gemeinde_electric_composition(df)

    weights = {}
    for _, row in df.iterrows():
        bev = pd.to_numeric(row["private_bev"], errors="coerce")
        phev = pd.to_numeric(row["private_phev"], errors="coerce")
        key = (str(row["kreis_ags5"]), fs.normalize_gemeinde_name(row["gemeinde"]))
        if key in per_gemeinde:
            weights[key] = float(bev) + float(phev)

    for kreis in {k for (k, _) in per_gemeinde}:
        for powertrain in ("bev", "phev"):
            mean = _weighted_mean(per_gemeinde, weights, kreis, powertrain)
            assert mean == pytest.approx(1.0, abs=1e-9), (
                f"Kreis {kreis} {powertrain} factors average to {mean}, not 1.0"
            )


# ---------------------------------------------------------------------------
# Aggregate neutrality on the REALISED population (issue #317 review finding)
# ---------------------------------------------------------------------------

class TestCompositionAggregateNeutrality:
    """``_neutralise_composition_aggregate`` on populations the FZ weights do not match.

    The composition factors are normalised so their mean is 1.0 weighted by each
    Gemeinde's FZ 27.17 ELECTRIC STOCK, but the per-Kreis rake targets the
    unweighted mean of the ACTUAL cars' pmfs. Nothing makes a synthetic
    population's cars-per-Gemeinde follow the FZ stock, and the rake preserves
    whatever aggregate it is handed (ADR-0085) -- so the correction has to run on
    the realised rows.
    """

    POWERTRAINS = ["petrol", "diesel", "bev", "phev", "hybrid", "gas", "other", "hydrogen"]
    IDX = {p: i for i, p in enumerate(POWERTRAINS)}

    def _pmf(self, bev: float, phev: float) -> np.ndarray:
        vec = np.zeros(len(self.POWERTRAINS))
        vec[self.IDX["petrol"]] = 1.0 - bev - phev
        vec[self.IDX["bev"]] = bev
        vec[self.IDX["phev"]] = phev
        return vec

    def _apply_composition(self, bev: float, phev: float,
                           factors: dict) -> np.ndarray:
        """Mirror what _apply_gemeinde_tilt does: factors + electric-total rescale."""
        f_bev, f_phev = factors["bev"], factors["phev"]
        denominator = bev * f_bev + phev * f_phev
        scale = (bev + phev) / denominator
        return self._pmf(bev * f_bev * scale, phev * f_phev * scale)

    def _bev_share_of_electric(self, pmfs: list) -> float:
        bev = sum(float(p[self.IDX["bev"]]) for p in pmfs)
        phev = sum(float(p[self.IDX["phev"]]) for p in pmfs)
        return bev / (bev + phev)

    def test_skewed_population_aggregate_is_restored(self):
        """The finding itself: cars concentrated where the factors are extreme.

        Two Gemeinden whose factors average to 1.0 under EQUAL weights, but the
        population puts 9 cars in the BEV-heavy one and 1 in the PHEV-heavy one.
        Without the correction the Kreis BEV aggregate rises; with it, it is
        exactly the untilted value.
        """
        base_bev, base_phev = 0.06, 0.04
        bev_heavy = {"bev": 1.2, "phev": 0.7}
        phev_heavy = {"bev": 0.8, "phev": 1.3}

        pmfs, kreise, factors = [], [], []
        for _ in range(9):
            pmfs.append(self._apply_composition(base_bev, base_phev, bev_heavy))
            kreise.append("03151")
            factors.append(bev_heavy)
        pmfs.append(self._apply_composition(base_bev, base_phev, phev_heavy))
        kreise.append("03151")
        factors.append(phev_heavy)

        untilted = base_bev / (base_bev + base_phev)
        assert self._bev_share_of_electric(pmfs) > untilted + 1e-6, (
            "fixture must actually be skewed, otherwise it proves nothing"
        )

        deltas, clipped = fs._neutralise_composition_aggregate(
            pmfs, kreise, factors, self.IDX)

        assert clipped == 0
        assert "03151" in deltas
        assert self._bev_share_of_electric(pmfs) == pytest.approx(untilted, abs=1e-12)

    def test_electric_total_per_car_is_untouched(self):
        """The correction must not reintroduce the level coupling it sits next to."""
        base_bev, base_phev = 0.06, 0.04
        factors_a = {"bev": 1.25, "phev": 0.6}
        pmfs = [self._apply_composition(base_bev, base_phev, factors_a) for _ in range(5)]
        pmfs.append(self._pmf(base_bev, base_phev))
        kreise = ["03151"] * 6
        factors = [factors_a] * 5 + [None]
        before = [float(p[self.IDX["bev"]] + p[self.IDX["phev"]]) for p in pmfs]

        fs._neutralise_composition_aggregate(pmfs, kreise, factors, self.IDX)

        after = [float(p[self.IDX["bev"]] + p[self.IDX["phev"]]) for p in pmfs]
        for electric_before, electric_after in zip(before, after):
            assert electric_after == pytest.approx(electric_before, abs=1e-15)

    def test_within_kreis_ordering_is_preserved(self):
        """A constant shift must not reorder Gemeinden: the structure is the point."""
        base_bev, base_phev = 0.06, 0.04
        bev_heavy = {"bev": 1.3, "phev": 0.5}
        phev_heavy = {"bev": 0.7, "phev": 1.5}
        pmfs = [self._apply_composition(base_bev, base_phev, bev_heavy)] * 3 + [
            self._apply_composition(base_bev, base_phev, phev_heavy)]
        pmfs = [p.copy() for p in pmfs]
        kreise = ["03151"] * 4
        factors = [bev_heavy] * 3 + [phev_heavy]

        fs._neutralise_composition_aggregate(pmfs, kreise, factors, self.IDX)

        bev_shares = [float(p[self.IDX["bev"]] / (p[self.IDX["bev"]] + p[self.IDX["phev"]]))
                      for p in pmfs]
        assert bev_shares[0] > bev_shares[3], "the BEV-heavy Gemeinde must stay BEV-heavier"

    def test_kreise_are_corrected_independently(self):
        """One Kreis's skew must never leak into another's aggregate."""
        base_bev, base_phev = 0.06, 0.04
        skew = {"bev": 1.4, "phev": 0.4}
        pmfs = [self._apply_composition(base_bev, base_phev, skew) for _ in range(4)]
        pmfs += [self._pmf(base_bev, base_phev) for _ in range(4)]
        kreise = ["03151"] * 4 + ["03158"] * 4
        factors = [skew] * 4 + [None] * 4

        deltas, _ = fs._neutralise_composition_aggregate(pmfs, kreise, factors, self.IDX)

        untilted = base_bev / (base_bev + base_phev)
        assert self._bev_share_of_electric(pmfs[:4]) == pytest.approx(untilted, abs=1e-12)
        assert self._bev_share_of_electric(pmfs[4:]) == pytest.approx(untilted, abs=1e-12)
        assert "03158" not in deltas, "a Kreis with unit factors needs no shift"

    def test_matched_population_needs_no_shift(self):
        """When the population DOES match the factors' weighting, delta is zero."""
        base_bev, base_phev = 0.075, 0.025
        # Factors that average to 1.0 under equal weights, one car each.
        a = {"bev": 1.2, "phev": 1.0 - (0.075 * 0.2) / 0.025}
        b = {"bev": 0.8, "phev": 1.0 + (0.075 * 0.2) / 0.025}
        pmfs = [self._apply_composition(base_bev, base_phev, a),
                self._apply_composition(base_bev, base_phev, b)]
        kreise = ["03151"] * 2
        factors = [a, b]
        untilted = base_bev / (base_bev + base_phev)

        fs._neutralise_composition_aggregate(pmfs, kreise, factors, self.IDX)

        assert self._bev_share_of_electric(pmfs) == pytest.approx(untilted, abs=1e-12)

    def test_cars_without_electric_mass_are_skipped(self):
        """A pmf with no electric mass must neither divide by zero nor be changed."""
        pmfs = [self._pmf(0.0, 0.0), self._apply_composition(0.06, 0.04, {"bev": 1.3, "phev": 0.55})]
        kreise = ["03151"] * 2
        factors = [None, {"bev": 1.3, "phev": 0.55}]

        fs._neutralise_composition_aggregate(pmfs, kreise, factors, self.IDX)

        assert float(pmfs[0][self.IDX["bev"]]) == 0.0
        assert float(pmfs[0][self.IDX["phev"]]) == 0.0
