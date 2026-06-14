"""Unit tests for the pure analysis functions in scripts/gate_income_tilt.py.

These tests verify the gate's measurement code on small synthetic frames,
independently of any pipeline run or local data. They must pass in CI.

Coverage:
  - income_rent_correlation: random income -> ~0, proportional income -> positive r
  - per_kreis_mean: correct exclusion of NaN rows, correct grouping
  - owner_renter_ratio: routing, NaN tenure column fallback
  - summarize_run: smoke test on synthetic frames
  - off_on_deltas: delta arithmetic, per-Kreis mean preservation detection
"""
from __future__ import annotations

import sys
import pathlib

import numpy as np
import pandas as pd
import pytest

# Make the repo root importable so the scripts/ module can be found.
_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# Import the pure analysis functions from scripts/gate_income_tilt.py.
# We do a direct module import (not as a package) because scripts/ is not
# a Python package (no __init__.py). This matches how popsim_mid_smoke.py is tested.
import importlib.util as _ilu
_gate_spec = _ilu.spec_from_file_location(
    "gate_income_tilt",
    str(_REPO_ROOT / "scripts" / "gate_income_tilt.py"),
)
_gate_mod = _ilu.module_from_spec(_gate_spec)  # type: ignore[arg-type]
_gate_spec.loader.exec_module(_gate_mod)  # type: ignore[union-attr]

income_rent_correlation = _gate_mod.income_rent_correlation
per_kreis_mean = _gate_mod.per_kreis_mean
owner_renter_ratio = _gate_mod.owner_renter_ratio
summarize_run = _gate_mod.summarize_run
off_on_deltas = _gate_mod.off_on_deltas
decide_gate = _gate_mod.decide_gate


# ---------------------------------------------------------------------------
# Fixtures: small synthetic frames
# ---------------------------------------------------------------------------

def _make_persons(incomes: list[float], cell_ids: list[str], kreise: list[str],
                  tenure: list[str] | None = None) -> pd.DataFrame:
    n = len(incomes)
    df = pd.DataFrame({
        "household_income_eur": incomes,
        "ZENSUS100m": cell_ids,
        "departement_id": kreise,
    })
    if tenure is not None:
        df["housing_tenure"] = tenure
    return df


def _make_cells(cell_ids: list[str], rents: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "ZENSUS100m": cell_ids,
        "durchschnMieteQM": rents,
    })


# ---------------------------------------------------------------------------
# income_rent_correlation
# ---------------------------------------------------------------------------

class TestIncomeRentCorrelation:
    def test_random_income_correlation_near_zero(self) -> None:
        """Random income has ~0 correlation with structured rent."""
        rng = np.random.default_rng(42)
        n = 200
        cell_ids = [f"c{i}" for i in range(n)]
        # Structured rent
        rents = list(rng.uniform(5, 20, n))
        # Random income, no signal
        incomes = list(rng.uniform(1000, 5000, n))
        kreise = ["03101"] * n
        persons = _make_persons(incomes, cell_ids, kreise)
        cells = _make_cells(cell_ids, rents)
        result = income_rent_correlation(persons, cells)
        # With n=200 random values, |r| should be well below 0.3
        assert abs(result["pearson"]) < 0.3, (
            f"random income should give near-zero Pearson, got {result['pearson']:.4f}"
        )
        assert abs(result["spearman"]) < 0.3, (
            f"random income should give near-zero Spearman, got {result['spearman']:.4f}"
        )
        assert result["n_valid"] == n

    def test_proportional_income_high_positive_correlation(self) -> None:
        """Income built proportional to rent -> strong positive correlation."""
        n = 100
        rents = [float(5 + i * 0.15) for i in range(n)]
        incomes = [r * 200 + 500 for r in rents]  # income proportional to rent
        cell_ids = [f"c{i}" for i in range(n)]
        kreise = ["03101"] * n
        persons = _make_persons(incomes, cell_ids, kreise)
        cells = _make_cells(cell_ids, rents)
        result = income_rent_correlation(persons, cells)
        assert result["pearson"] > 0.95, (
            f"proportional income should give Pearson > 0.95, got {result['pearson']:.4f}"
        )
        assert result["spearman"] > 0.95, (
            f"proportional income should give Spearman > 0.95, got {result['spearman']:.4f}"
        )

    def test_nan_income_excluded(self) -> None:
        """NaN income rows are excluded from correlation (n_valid decreases)."""
        persons = _make_persons(
            [1000.0, 2000.0, float("nan"), 3000.0],
            ["c0", "c1", "c2", "c3"],
            ["03101"] * 4,
        )
        cells = _make_cells(["c0", "c1", "c2", "c3"], [5.0, 10.0, 8.0, 15.0])
        result = income_rent_correlation(persons, cells)
        assert result["n_valid"] == 3, f"expected 3 valid rows (1 NaN excluded), got {result['n_valid']}"

    def test_missing_rent_cell_excluded(self) -> None:
        """Persons in cells with no rent data are excluded from correlation."""
        persons = _make_persons(
            [1000.0, 2000.0, 3000.0],
            ["c0", "c1", "c_no_data"],  # c_no_data not in cells frame
            ["03101"] * 3,
        )
        cells = _make_cells(["c0", "c1"], [5.0, 10.0])
        result = income_rent_correlation(persons, cells)
        # c_no_data left-join gives NaN rent -> excluded
        assert result["n_valid"] == 2, f"expected 2 valid rows, got {result['n_valid']}"

    def test_too_few_valid_rows_returns_nan(self) -> None:
        """< 3 valid rows -> all results NaN (scipy correlation undefined)."""
        persons = _make_persons([1000.0], ["c0"], ["03101"])
        cells = _make_cells(["c0"], [8.0])
        result = income_rent_correlation(persons, cells)
        assert np.isnan(result["pearson"]), "expected NaN pearson for n<3"
        assert np.isnan(result["spearman"]), "expected NaN spearman for n<3"
        assert result["n_valid"] == 1

    def test_anti_correlated_returns_negative(self) -> None:
        """Income inversely proportional to rent -> negative correlation."""
        n = 50
        rents = [float(5 + i * 0.3) for i in range(n)]
        incomes = [5000 - r * 150 for r in rents]  # income anti-proportional to rent
        cell_ids = [f"c{i}" for i in range(n)]
        kreise = ["03101"] * n
        persons = _make_persons(incomes, cell_ids, kreise)
        cells = _make_cells(cell_ids, rents)
        result = income_rent_correlation(persons, cells)
        assert result["pearson"] < -0.9, (
            f"anti-proportional income should give Pearson < -0.9, got {result['pearson']:.4f}"
        )


# ---------------------------------------------------------------------------
# per_kreis_mean
# ---------------------------------------------------------------------------

class TestPerKreisMean:
    def test_single_kreis_correct_mean(self) -> None:
        persons = _make_persons([1000.0, 2000.0, 3000.0], ["c0", "c1", "c2"], ["03101"] * 3)
        km = per_kreis_mean(persons)
        assert abs(km["03101"] - 2000.0) < 1e-9

    def test_two_kreise_independent(self) -> None:
        persons = _make_persons(
            [1000.0, 2000.0, 4000.0, 6000.0],
            ["c0", "c1", "c2", "c3"],
            ["03101", "03101", "03102", "03102"],
        )
        km = per_kreis_mean(persons)
        assert abs(km["03101"] - 1500.0) < 1e-9
        assert abs(km["03102"] - 5000.0) < 1e-9

    def test_nan_excluded_from_mean(self) -> None:
        persons = _make_persons(
            [1000.0, float("nan"), 3000.0],
            ["c0", "c1", "c2"],
            ["03101", "03101", "03101"],
        )
        km = per_kreis_mean(persons)
        # Mean of [1000, 3000] = 2000 (NaN excluded)
        assert abs(km["03101"] - 2000.0) < 1e-9

    def test_all_nan_kreis_returns_empty(self) -> None:
        persons = _make_persons(
            [float("nan"), float("nan")],
            ["c0", "c1"],
            ["03101", "03101"],
        )
        km = per_kreis_mean(persons)
        assert "03101" not in km, "All-NaN Kreis should not appear in per_kreis_mean"


# ---------------------------------------------------------------------------
# owner_renter_ratio
# ---------------------------------------------------------------------------

class TestOwnerRenterRatio:
    def test_owner_higher_than_renter(self) -> None:
        persons = _make_persons(
            [3000.0, 3000.0, 1500.0, 1500.0],
            ["c0", "c1", "c2", "c3"],
            ["03101"] * 4,
            tenure=["owner", "owner", "renter", "renter"],
        )
        result = owner_renter_ratio(persons)
        assert result["owner_mean"] == pytest.approx(3000.0)
        assert result["renter_mean"] == pytest.approx(1500.0)
        assert result["owner_renter_ratio"] == pytest.approx(2.0)

    def test_missing_tenure_column_returns_nan(self) -> None:
        persons = _make_persons([1000.0, 2000.0], ["c0", "c1"], ["03101"] * 2)
        result = owner_renter_ratio(persons)
        assert np.isnan(result["owner_mean"])
        assert np.isnan(result["renter_mean"])
        assert np.isnan(result["owner_renter_ratio"])

    def test_unknown_tenure_reported_separately(self) -> None:
        persons = _make_persons(
            [3000.0, 1500.0, 2000.0],
            ["c0", "c1", "c2"],
            ["03101"] * 3,
            tenure=["owner", "renter", "unknown"],
        )
        result = owner_renter_ratio(persons)
        assert result["unknown_mean"] == pytest.approx(2000.0)

    def test_zero_renter_mean_returns_nan_ratio(self) -> None:
        persons = _make_persons(
            [3000.0, 0.0],
            ["c0", "c1"],
            ["03101"] * 2,
            tenure=["owner", "renter"],
        )
        result = owner_renter_ratio(persons)
        assert np.isnan(result["owner_renter_ratio"]) or np.isinf(result["owner_renter_ratio"])


# ---------------------------------------------------------------------------
# off_on_deltas
# ---------------------------------------------------------------------------

class TestOffOnDeltas:
    def _make_summary(self, pearson: float, spearman: float, km: dict,
                      owner: float = 2500.0, renter: float = 1500.0) -> dict:
        return {
            "pearson": pearson,
            "pearson_pvalue": 0.001,
            "spearman": spearman,
            "spearman_pvalue": 0.001,
            "n_valid_persons": 10000,
            "per_kreis_mean": km,
            "owner_mean_eur": owner,
            "renter_mean_eur": renter,
            "unknown_mean_eur": 2000.0,
            "owner_renter_ratio": owner / renter,
            "n_persons": 10000,
            "global_mean_income_eur": 2000.0,
        }

    def test_delta_pearson_computed_correctly(self) -> None:
        off = self._make_summary(0.10, 0.12, {"03101": 2000.0})
        on  = self._make_summary(0.25, 0.28, {"03101": 2000.0})
        deltas = off_on_deltas(off, on)
        assert deltas["delta_pearson"] == pytest.approx(0.15)
        assert deltas["delta_spearman"] == pytest.approx(0.16)

    def test_kreis_mean_preserved_when_identical(self) -> None:
        """Same per-Kreis mean OFF vs ON -> preserved=True."""
        off = self._make_summary(0.10, 0.12, {"03101": 2000.0, "03102": 3000.0})
        on  = self._make_summary(0.25, 0.28, {"03101": 2000.0, "03102": 3000.0})
        deltas = off_on_deltas(off, on)
        assert deltas["per_kreis_mean_preserved"] is True
        assert deltas["per_kreis_mean_max_abs_dev_eur"] == pytest.approx(0.0)

    def test_kreis_mean_large_deviation_not_preserved(self) -> None:
        """Large per-Kreis mean deviation -> preserved=False."""
        off = self._make_summary(0.10, 0.12, {"03101": 2000.0})
        on  = self._make_summary(0.25, 0.28, {"03101": 4000.0})  # 2x higher
        deltas = off_on_deltas(off, on)
        assert deltas["per_kreis_mean_preserved"] is False

    def test_negative_delta_reflected(self) -> None:
        """ON correlation lower than OFF -> negative delta."""
        off = self._make_summary(0.30, 0.35, {"03101": 2000.0})
        on  = self._make_summary(0.10, 0.12, {"03101": 2000.0})
        deltas = off_on_deltas(off, on)
        assert deltas["delta_pearson"] < 0
        assert deltas["delta_spearman"] < 0

    def test_nan_pearson_off_gives_nan_delta(self) -> None:
        """If OFF pearson is NaN, delta must be NaN."""
        off = self._make_summary(float("nan"), float("nan"), {"03101": 2000.0})
        on  = self._make_summary(0.25, 0.28, {"03101": 2000.0})
        deltas = off_on_deltas(off, on)
        assert np.isnan(deltas["delta_pearson"])
        assert np.isnan(deltas["delta_spearman"])

    def test_owner_renter_ratio_delta(self) -> None:
        """Owner/renter ratio should increase when tilt is ON (tilt spreads incomes)."""
        off = self._make_summary(0.10, 0.12, {"03101": 2000.0}, owner=2000.0, renter=2000.0)
        on  = self._make_summary(0.25, 0.28, {"03101": 2000.0}, owner=2500.0, renter=1700.0)
        deltas = off_on_deltas(off, on)
        assert deltas["delta_owner_renter_ratio"] > 0


# ---------------------------------------------------------------------------
# summarize_run (smoke test: returns expected keys)
# ---------------------------------------------------------------------------

class TestSummarizeRun:
    def test_smoke_all_keys_present(self) -> None:
        """summarize_run must return all expected KPI keys."""
        persons = _make_persons(
            [1000.0, 2000.0, 3000.0, 1500.0],
            ["c0", "c1", "c2", "c3"],
            ["03101", "03101", "03101", "03101"],
            tenure=["owner", "renter", "owner", "renter"],
        )
        cells = _make_cells(["c0", "c1", "c2", "c3"], [6.0, 9.0, 12.0, 7.0])
        summary = summarize_run(persons, cells)
        for key in ("pearson", "spearman", "n_valid_persons", "per_kreis_mean",
                    "owner_mean_eur", "renter_mean_eur", "owner_renter_ratio",
                    "n_persons", "global_mean_income_eur"):
            assert key in summary, f"Expected key {key!r} in summarize_run output"

    def test_smoke_tilt_diag_forwarded(self) -> None:
        """If tilt_diag is passed, its keys appear in the summary."""
        persons = _make_persons([2000.0, 3000.0], ["c0", "c1"], ["03101"] * 2)
        cells = _make_cells(["c0", "c1"], [7.0, 12.0])
        diag = {"clipped_fraction": 0.05, "max_effective_dev": 0.28,
                "beta_clip": 0.30, "kreis_mean_preserved": True}
        summary = summarize_run(persons, cells, tilt_diag=diag)
        assert summary["clipped_fraction"] == pytest.approx(0.05)
        assert summary["kreis_mean_preserved"] is True

    def test_without_tenure_column_no_crash(self) -> None:
        """No housing_tenure column -> summarize_run must not crash (returns NaN for tenure KPIs)."""
        persons = _make_persons([1000.0, 2000.0], ["c0", "c1"], ["03101"] * 2)
        cells = _make_cells(["c0", "c1"], [6.0, 9.0])
        summary = summarize_run(persons, cells)
        assert np.isnan(summary["owner_renter_ratio"]), (
            "Without tenure column, owner_renter_ratio must be NaN"
        )


# ---------------------------------------------------------------------------
# decide_gate (pure decision function — regression-tests for critical bug)
# ---------------------------------------------------------------------------

class TestDecideGate:
    """Tests for the decide_gate() pure function.

    These tests verify the gate decision logic in isolation from any pipeline run
    or data load. Case (b) is the regression test for the critical bug where the
    gate ALWAYS returned FLIP because a missing tilt_diag defaulted clipped_fraction
    to 1.0 (worst case), causing clipped_ok = (1.0 < 0.5) = False.
    """

    def _make_off(self, spearman: float = 0.00, km: dict | None = None) -> dict:
        """Minimal OFF summary for decide_gate."""
        return {
            "pearson": -0.006,
            "spearman": spearman,
            "per_kreis_mean": km or {"03101": 4633.0},
            "owner_renter_ratio": 1.2448,
        }

    def _make_on(
        self,
        spearman: float = 0.028,
        km: dict | None = None,
        clipped_fraction: float | None = 0.12,
        kreis_mean_preserved: bool | None = True,
    ) -> dict:
        """Minimal ON summary for decide_gate."""
        s: dict = {
            "pearson": 0.026,
            "spearman": spearman,
            "per_kreis_mean": km or {"03101": 4633.0},
            "owner_renter_ratio": 1.3090,
        }
        if clipped_fraction is not None:
            s["clipped_fraction"] = clipped_fraction
        if kreis_mean_preserved is not None:
            s["kreis_mean_preserved"] = kreis_mean_preserved
        return s

    def _make_deltas(
        self, delta_spearman: float = 0.028, cross_preserved: bool = True
    ) -> dict:
        """Minimal deltas dict for decide_gate."""
        return {
            "delta_pearson": 0.032,
            "delta_spearman": delta_spearman,
            "delta_owner_renter_ratio": 0.064,
            "per_kreis_mean_max_rel_dev": 0.0,
            "per_kreis_mean_max_abs_dev_eur": 0.0,
            "per_kreis_mean_preserved": cross_preserved,
        }

    def test_a_all_pass_returns_keep_exit0(self) -> None:
        """(a) Improve + preserved + clip 0.12 -> KEEP_DEFAULT_ON, exit 0."""
        off = self._make_off(spearman=0.00)
        on = self._make_on(spearman=0.028, clipped_fraction=0.12, kreis_mean_preserved=True)
        deltas = self._make_deltas(delta_spearman=0.028)
        rec, code = decide_gate(off, on, deltas)
        assert "KEEP_DEFAULT_ON" in rec, f"Expected KEEP, got: {rec}"
        assert code == 0

    def test_b_clipped_fraction_absent_must_not_flip(self) -> None:
        """(b) clipped_fraction ABSENT (diag missing) + improve + preserved -> KEEP.

        CRITICAL regression test: the original bug defaulted missing clipped_fraction
        to 1.0, so clipped_ok = (1.0 < 0.5) = False -> always FLIP. The fix treats
        absent clipped_fraction as OK (fail-open on unmeasured quantity).
        """
        off = self._make_off(spearman=0.00)
        # No clipped_fraction key and no kreis_mean_preserved key -> diag absent
        on: dict = {
            "pearson": 0.026,
            "spearman": 0.028,
            "per_kreis_mean": {"03101": 4633.0},
            "owner_renter_ratio": 1.3090,
            # clipped_fraction deliberately absent
            # kreis_mean_preserved deliberately absent
        }
        deltas = self._make_deltas(delta_spearman=0.028, cross_preserved=True)
        rec, code = decide_gate(off, on, deltas)
        assert "KEEP_DEFAULT_ON" in rec, (
            f"Missing clipped_fraction must NOT trigger FLIP, got: {rec}"
        )
        assert code == 0

    def test_c_mean_not_preserved_returns_flip_exit1(self) -> None:
        """(c) Per-Kreis mean NOT preserved (diag says False) -> FLIP, exit 1."""
        off = self._make_off()
        on = self._make_on(kreis_mean_preserved=False)
        deltas = self._make_deltas()
        rec, code = decide_gate(off, on, deltas)
        assert "FLIP_DEFAULT_OFF" in rec, f"Expected FLIP, got: {rec}"
        assert code == 1

    def test_d_delta_spearman_negative_returns_flip_exit1(self) -> None:
        """(d) delta_spearman = -0.05 (worsens) -> FLIP, exit 1."""
        off = self._make_off(spearman=0.10)
        on = self._make_on(spearman=0.05)
        deltas = self._make_deltas(delta_spearman=-0.05)
        rec, code = decide_gate(off, on, deltas)
        assert "FLIP_DEFAULT_OFF" in rec, f"Expected FLIP, got: {rec}"
        assert code == 1

    def test_e_all_nan_correlation_returns_flip_exit1(self) -> None:
        """(e) All-NaN correlations (both runs failed) -> FLIP, exit 1 (fail-safe)."""
        off = self._make_off(spearman=float("nan"))
        on = self._make_on(spearman=float("nan"))
        deltas = self._make_deltas(delta_spearman=float("nan"))
        rec, code = decide_gate(off, on, deltas)
        assert "FLIP_DEFAULT_OFF" in rec, f"Expected FLIP on NaN correlation, got: {rec}"
        assert code == 1

    def test_clipped_fraction_exactly_50pct_is_flip(self) -> None:
        """Clipped fraction == 0.5 is NOT OK (threshold is strictly < 0.5)."""
        off = self._make_off()
        on = self._make_on(clipped_fraction=0.5)
        deltas = self._make_deltas()
        rec, code = decide_gate(off, on, deltas)
        assert "FLIP_DEFAULT_OFF" in rec, f"cf=0.5 should FLIP, got: {rec}"
        assert code == 1

    def test_delta_spearman_at_tolerance_boundary_keeps(self) -> None:
        """delta_spearman = -0.009 (within -0.01 tolerance) -> KEEP."""
        off = self._make_off()
        on = self._make_on()
        deltas = self._make_deltas(delta_spearman=-0.009)
        rec, code = decide_gate(off, on, deltas)
        assert "KEEP_DEFAULT_ON" in rec, f"delta_spearman=-0.009 should KEEP, got: {rec}"
        assert code == 0

    def test_delta_spearman_exactly_at_tolerance_keeps(self) -> None:
        """delta_spearman = -0.01 (exactly at tolerance boundary) -> KEEP (>= -0.01)."""
        off = self._make_off()
        on = self._make_on()
        deltas = self._make_deltas(delta_spearman=-0.01)
        rec, code = decide_gate(off, on, deltas)
        assert "KEEP_DEFAULT_ON" in rec, f"delta_spearman=-0.01 should KEEP (>=), got: {rec}"
        assert code == 0

    def test_delta_spearman_just_below_tolerance_flips(self) -> None:
        """delta_spearman = -0.011 (just below -0.01 tolerance) -> FLIP."""
        off = self._make_off()
        on = self._make_on()
        deltas = self._make_deltas(delta_spearman=-0.011)
        rec, code = decide_gate(off, on, deltas)
        assert "FLIP_DEFAULT_OFF" in rec, f"delta_spearman=-0.011 should FLIP, got: {rec}"
        assert code == 1
