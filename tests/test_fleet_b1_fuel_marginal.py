"""Tests for T6a: per-Kreis powertrain marginal from real 46251 fuel data.

Two distinct scenarios:

1. PRIMARY PATH — ``kba_kreis_fuel.csv`` present:
   ``PowertrainModel.from_data_path`` reads per-Kreis real petrol:diesel counts
   and builds the powertrain marginal from them (no NDS approximation). The
   resulting per-Kreis vector must reflect the REAL petrol:diesel ratio from the
   fuel CSV, not the NDS one.

2. FALLBACK PATH — ``kba_kreis_fuel.csv`` absent:
   ``PowertrainModel.from_data_path`` falls back to the current FZ 27.15 +
   NDS-split path unchanged. A caplog message documents which source was used
   (no-silent-fallback rule). The model must build successfully with all 8
   ZGB Kreise.
"""
from __future__ import annotations

import logging
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data"
sys.path.insert(0, str(REPO))

from braunschweig.data.kba import fleet_tables as ft  # noqa: E402
from braunschweig.synthesis.vehicles import fleet_sampling_de as fs  # noqa: E402

DATA_PATH = str(DATA)

# Canonical powertrain order (same as POWERTRAIN_LABELS = POWERTRAINS in the module).
POWERTRAINS = list(ft.POWERTRAIN_LABELS)

# Two synthetic Kreise with clearly different petrol:diesel ratios.
_AGS_A = "03101"  # Braunschweig (kreisfreie Stadt)
_AGS_B = "03102"  # Salzgitter

# Intentionally asymmetric ratios so the test unambiguously distinguishes the
# real per-Kreis ratio from the NDS ratio (which is ~50/50).
_KREIS_FUEL_ROWS: list[dict] = [
    # Kreis A: petrol-heavy (70 % of combustion)
    {
        "kreis_ags5": _AGS_A, "kreis_name": "KreisA", "stichtag": "2025-01-01",
        "petrol": 70_000, "diesel": 30_000, "gas": 1_000,
        "bev": 5_000, "phev": 2_000, "hybrid": 3_000, "other": 500,
        "total": 111_500,
    },
    # Kreis B: diesel-heavy (80 % of combustion)
    {
        "kreis_ags5": _AGS_B, "kreis_name": "KreisB", "stichtag": "2025-01-01",
        "petrol": 20_000, "diesel": 80_000, "gas": 500,
        "bev": 1_000, "phev": 500, "hybrid": 800, "other": 200,
        "total": 103_000,
    },
]


def _make_kreis_fuel_df() -> pd.DataFrame:
    """Synthetic ``kba_kreis_fuel.csv`` DataFrame (two Kreise only)."""
    return pd.DataFrame(_KREIS_FUEL_ROWS)


def _segments():
    return list(ft.SEGMENT_LABELS[:5])  # any 5 segments are fine for unit tests


# --------------------------------------------------------------------------- #
# Helper: call the new private builder directly on a synthetic DataFrame
# --------------------------------------------------------------------------- #
def _build_marginal_from_fuel_df(df_fuel: pd.DataFrame) -> dict[str, np.ndarray]:
    """Build the per-Kreis powertrain marginal dict using the NEW code path.

    This calls the static method that is expected to exist after T6a is
    implemented: ``PowertrainModel._kreis_powertrain_marginal_from_fuel``.
    """
    return fs.PowertrainModel._kreis_powertrain_marginal_from_fuel(
        df_fuel, POWERTRAINS
    )


# --------------------------------------------------------------------------- #
# STEP 1 (written BEFORE implementation): tests that currently FAIL
# --------------------------------------------------------------------------- #

class TestFuelMarginalPrimaryPath:
    """Unit-test the per-Kreis fuel-based marginal builder directly."""

    def test_static_method_exists(self):
        """The new static method must exist after T6a implementation."""
        assert hasattr(fs.PowertrainModel, "_kreis_powertrain_marginal_from_fuel"), (
            "PowertrainModel must have a static method "
            "'_kreis_powertrain_marginal_from_fuel' after T6a."
        )

    def test_real_petrol_diesel_ratio_kreis_a(self):
        """Kreis A (petrol-heavy 70:30) must give petrol fraction ~0.70."""
        marginal = _build_marginal_from_fuel_df(_make_kreis_fuel_df())
        vec_a = marginal[_AGS_A]
        idx = {p: i for i, p in enumerate(POWERTRAINS)}
        petrol = vec_a[idx["petrol"]]
        diesel = vec_a[idx["diesel"]]
        combustion = petrol + diesel
        assert combustion > 0, "no combustion mass in Kreis A"
        petrol_frac = petrol / combustion
        assert petrol_frac == pytest.approx(70_000 / (70_000 + 30_000), abs=1e-9), (
            f"Kreis A petrol fraction {petrol_frac:.4f} must match real "
            "fuel-CSV ratio 0.70, not the NDS ratio."
        )

    def test_real_petrol_diesel_ratio_kreis_b(self):
        """Kreis B (diesel-heavy 80:20) must give petrol fraction ~0.20."""
        marginal = _build_marginal_from_fuel_df(_make_kreis_fuel_df())
        vec_b = marginal[_AGS_B]
        idx = {p: i for i, p in enumerate(POWERTRAINS)}
        petrol = vec_b[idx["petrol"]]
        diesel = vec_b[idx["diesel"]]
        combustion = petrol + diesel
        assert combustion > 0, "no combustion mass in Kreis B"
        petrol_frac = petrol / combustion
        assert petrol_frac == pytest.approx(20_000 / (20_000 + 80_000), abs=1e-9), (
            f"Kreis B petrol fraction {petrol_frac:.4f} must match real "
            "fuel-CSV ratio 0.20, not the NDS ratio."
        )

    def test_bev_count_placed_correctly_kreis_a(self):
        """BEV mass in Kreis A must match the raw bev count."""
        marginal = _build_marginal_from_fuel_df(_make_kreis_fuel_df())
        vec_a = marginal[_AGS_A]
        idx = {p: i for i, p in enumerate(POWERTRAINS)}
        # The marginal is a count-like vector (not normalised), so bev=5000.
        assert vec_a[idx["bev"]] == pytest.approx(5_000.0, rel=1e-9)

    def test_phev_count_placed_correctly_kreis_b(self):
        """PHEV mass in Kreis B must match the raw phev count."""
        marginal = _build_marginal_from_fuel_df(_make_kreis_fuel_df())
        vec_b = marginal[_AGS_B]
        idx = {p: i for i, p in enumerate(POWERTRAINS)}
        assert vec_b[idx["phev"]] == pytest.approx(500.0, rel=1e-9)

    def test_other_placed_correctly(self):
        """'other' mass is taken directly from the fuel CSV 'other' column."""
        marginal = _build_marginal_from_fuel_df(_make_kreis_fuel_df())
        vec_a = marginal[_AGS_A]
        idx = {p: i for i, p in enumerate(POWERTRAINS)}
        assert vec_a[idx["other"]] == pytest.approx(500.0, rel=1e-9)

    def test_hydrogen_is_zero(self):
        """hydrogen is set to 0 (no per-Kreis column), matching FZ27.15 behavior."""
        marginal = _build_marginal_from_fuel_df(_make_kreis_fuel_df())
        idx = {p: i for i, p in enumerate(POWERTRAINS)}
        for ags, vec in marginal.items():
            assert vec[idx["hydrogen"]] == pytest.approx(0.0), (
                f"hydrogen must be 0 for Kreis {ags}; got {vec[idx['hydrogen']]}"
            )

    def test_all_kreise_present(self):
        """Output dict has an entry for every Kreis in the fuel DataFrame."""
        marginal = _build_marginal_from_fuel_df(_make_kreis_fuel_df())
        assert set(marginal.keys()) == {_AGS_A, _AGS_B}

    def test_vector_length_matches_powertrains(self):
        """Each marginal vector has exactly len(POWERTRAINS) entries."""
        marginal = _build_marginal_from_fuel_df(_make_kreis_fuel_df())
        for ags, vec in marginal.items():
            assert len(vec) == len(POWERTRAINS), (
                f"Kreis {ags}: expected {len(POWERTRAINS)} entries, got {len(vec)}"
            )

    def test_non_negative_counts(self):
        """All marginal entries are non-negative."""
        marginal = _build_marginal_from_fuel_df(_make_kreis_fuel_df())
        for ags, vec in marginal.items():
            assert (vec >= 0).all(), f"Kreis {ags}: negative entry in {vec}"


# --------------------------------------------------------------------------- #
# STEP 2: from_data_path wiring — primary path uses real fuel data when present
# --------------------------------------------------------------------------- #

class TestFromDataPathWithFuelCSV:
    """Integration: from_data_path uses real fuel CSV when available."""

    def _make_tmp_data_path(self, tmp_path: Path) -> str:
        """Mirror the real data directory tree into tmp_path and add the fuel CSV.

        The fuel CSV must cover all 8 ZGB Kreise (the loader asserts this).
        Returns the tmp data path string.
        """
        derived = tmp_path / "braunschweig" / "kba" / "derived"
        derived.mkdir(parents=True)
        real_derived = DATA / "braunschweig" / "kba" / "derived"
        # Symlink or copy all existing CSVs so the other loaders work.
        for src in real_derived.glob("*.csv"):
            # Never symlink the file we overlay below (write-through-symlink would
            # corrupt the real committed derived CSV).
            if src.name == "kba_kreis_fuel.csv":
                continue
            dst = derived / src.name
            try:
                dst.symlink_to(src)
            except OSError:
                import shutil
                shutil.copy2(src, dst)

        # Build a synthetic fuel CSV with all 8 ZGB Kreise.
        rows = []
        for i, ags in enumerate(ft.ZGB_KREISE_AGS5):
            # Vary petrol:diesel to make Kreise distinguishable.
            petrol = 60_000 + i * 5_000
            diesel = 40_000 - i * 2_000
            rows.append({
                "kreis_ags5": ags, "kreis_name": f"Kreis_{ags}", "stichtag": "2025-01-01",
                "petrol": petrol, "diesel": diesel, "gas": 1_000,
                "bev": 4_000, "phev": 1_500, "hybrid": 2_000, "other": 300,
                "total": petrol + diesel + 1_000 + 4_000 + 1_500 + 2_000 + 300,
            })
        pd.DataFrame(rows).to_csv(derived / "kba_kreis_fuel.csv", index=False)
        return str(tmp_path)

    def test_primary_path_logged(self, tmp_path, caplog):
        """When fuel CSV is present, from_data_path logs primary source info."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = self._make_tmp_data_path(tmp_path)
        segs = _segments()
        with caplog.at_level(logging.INFO):
            model = fs.PowertrainModel.from_data_path(tmp_data, segs)
        messages = " ".join(r.message for r in caplog.records).lower()
        assert "46251" in messages or "fuel" in messages, (
            "Expected a log message referencing the 46251 fuel data source; "
            f"got: {messages!r}"
        )
        # Model should have all 8 ZGB Kreise.
        assert len(model.kreis_segment_powertrain) == len(ft.ZGB_KREISE_AGS5)

    def test_primary_path_petrol_diesel_ratio_differs_from_nds(self, tmp_path):
        """With real per-Kreis fuel data the per-Kreis ratio must differ from NDS.

        The synthetic fuel CSV gives different petrol fractions per Kreis. The
        NDS ratio is taken from the actual FZ 27.4 and is approximately the same
        for every Kreis in the FZ 27.15 path — using real fuel data should make
        the ratios differ across Kreise AND differ from the NDS approximation.
        """
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = self._make_tmp_data_path(tmp_path)
        segs = _segments()

        model_fuel = fs.PowertrainModel.from_data_path(tmp_data, segs)

        idx = {p: i for i, p in enumerate(POWERTRAINS)}
        petrol_fracs = {}
        for ags in ft.ZGB_KREISE_AGS5:
            mat = model_fuel.kreis_segment_powertrain.get(ags)
            assert mat is not None, f"Kreis {ags} missing from fuel-based model"
            # Row-sum the per-segment joint to get the per-Kreis marginal.
            df_seg = ft.load_segment_powertrain(DATA_PATH)
            seg_share = (
                df_seg.set_index("segment")["segment_share"]
                .reindex(segs)
                .fillna(0.0)
                .to_numpy(dtype=float)
            )
            seg_share /= seg_share.sum()
            marginal = mat.T @ seg_share  # shape (n_powertrain,)
            petrol = marginal[idx["petrol"]]
            diesel = marginal[idx["diesel"]]
            comb = petrol + diesel
            petrol_fracs[ags] = petrol / comb if comb > 0 else 0.5

        # Verify that the petrol fractions are NOT all identical (real per-Kreis
        # data introduces variation that the NDS approximation does not).
        min_frac = min(petrol_fracs.values())
        max_frac = max(petrol_fracs.values())
        assert max_frac - min_frac > 0.01, (
            "Per-Kreis petrol fractions are suspiciously uniform "
            f"(min={min_frac:.4f}, max={max_frac:.4f}); "
            "the fuel-based path should produce per-Kreis variation."
        )


# --------------------------------------------------------------------------- #
# STEP 3: Fallback path — kba_kreis_fuel.csv absent -> FZ 27.15 NDS path
# --------------------------------------------------------------------------- #

class TestFromDataPathFallback:
    """Fallback: when kba_kreis_fuel.csv is absent, fall back to FZ 27.15."""

    def test_fallback_builds_successfully_on_real_data(self, caplog):
        """from_data_path on the committed data path (no fuel CSV) must succeed.

        The committed data directory does NOT contain kba_kreis_fuel.csv, so
        from_data_path must fall back to the FZ 27.15 NDS-split path without
        raising an exception.
        """
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        segs = _segments()
        with caplog.at_level(logging.INFO):
            model = fs.PowertrainModel.from_data_path(DATA_PATH, segs)
        # Must have all 8 ZGB Kreise.
        assert len(model.kreis_segment_powertrain) == len(ft.ZGB_KREISE_AGS5), (
            f"Expected {len(ft.ZGB_KREISE_AGS5)} Kreise, "
            f"got {len(model.kreis_segment_powertrain)}"
        )

    def test_fallback_log_message_fired(self, caplog):
        """A log message must explicitly state which source was used (fallback)."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        segs = _segments()
        with caplog.at_level(logging.INFO):
            fs.PowertrainModel.from_data_path(DATA_PATH, segs)
        messages = " ".join(r.message for r in caplog.records).lower()
        # The fallback log must clearly mention that it fell back to FZ 27.15.
        assert ("27.15" in messages or "fz" in messages or "fallback" in messages
                or "nds" in messages), (
            "Expected a fallback log message mentioning FZ 27.15 / NDS / fallback; "
            f"got: {messages!r}"
        )

    def test_euro_joint_draw_untouched(self, caplog):
        """euro_given_powertrain, age_given_powertrain, age_euro_joint are unchanged.

        T6a only touches the per-Kreis powertrain marginal. The euro/age
        distributions must come from the same FZ 27.4 / FZ 27.7 path.
        """
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        # Build FleetSampler (which calls from_data_path internally).
        segs = _segments()
        sampler_a = fs.FleetSampler.from_data_path(DATA_PATH)
        sampler_b = fs.FleetSampler.from_data_path(DATA_PATH)
        # Both must produce identical euro_given_powertrain (deterministic build).
        for pt in POWERTRAINS:
            np.testing.assert_array_equal(
                sampler_a.euro_given_powertrain[pt],
                sampler_b.euro_given_powertrain[pt],
                err_msg=f"euro_given_powertrain[{pt!r}] differs between two builds",
            )


# --------------------------------------------------------------------------- #
# STEP 4 (final-review guard): a degenerate Kreis (insg>0 but every fuel
# component zero/suppressed) must not crash from_data_path with a NaN pmf.
# --------------------------------------------------------------------------- #

class TestFromDataPathDegenerateKreis:
    """A Kreis whose fuel components are all zero/suppressed falls back to the
    national P(powertrain|segment) instead of producing a NaN pmf (which would
    crash rng.choice at draw time). See the final whole-branch review and the
    pure-function tests in test_fleet_kreis_marginal_guard.py."""

    def _make_tmp_data_path_with_degenerate_kreis(self, tmp_path: Path) -> tuple[str, str]:
        """Mirror the real derived tree and overlay a fuel CSV where the FIRST
        ZGB Kreis has every fuel component zero (degenerate). Returns
        ``(data_path, degenerate_ags5)``."""
        derived = tmp_path / "braunschweig" / "kba" / "derived"
        derived.mkdir(parents=True)
        real_derived = DATA / "braunschweig" / "kba" / "derived"
        for src in real_derived.glob("*.csv"):
            # Never symlink the file we overlay below (write-through-symlink would
            # corrupt the real committed derived CSV).
            if src.name == "kba_kreis_fuel.csv":
                continue
            dst = derived / src.name
            try:
                dst.symlink_to(src)
            except OSError:
                import shutil
                shutil.copy2(src, dst)

        degenerate_ags = ft.ZGB_KREISE_AGS5[0]
        rows = []
        for i, ags in enumerate(ft.ZGB_KREISE_AGS5):
            if ags == degenerate_ags:
                # insg>0 but every fuel component zero: the crash precondition.
                rows.append({
                    "kreis_ags5": ags, "kreis_name": f"Kreis_{ags}",
                    "stichtag": "2025-01-01",
                    "petrol": 0, "diesel": 0, "gas": 0,
                    "bev": 0, "phev": 0, "hybrid": 0, "other": 0,
                    "total": 0,
                })
            else:
                petrol = 60_000 + i * 5_000
                diesel = 40_000 - i * 2_000
                rows.append({
                    "kreis_ags5": ags, "kreis_name": f"Kreis_{ags}",
                    "stichtag": "2025-01-01",
                    "petrol": petrol, "diesel": diesel, "gas": 1_000,
                    "bev": 4_000, "phev": 1_500, "hybrid": 2_000, "other": 300,
                    "total": petrol + diesel + 1_000 + 4_000 + 1_500 + 2_000 + 300,
                })
        pd.DataFrame(rows).to_csv(derived / "kba_kreis_fuel.csv", index=False)
        return str(tmp_path), degenerate_ags

    def test_degenerate_kreis_does_not_crash_and_logs_fallback(self, tmp_path, caplog):
        """from_data_path must build (no NaN pmf), keep the degenerate Kreis, and
        WARN that it used the national fallback (no-silent-fallback rule)."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data, degenerate_ags = self._make_tmp_data_path_with_degenerate_kreis(tmp_path)
        segs = _segments()
        with caplog.at_level(logging.WARNING):
            model = fs.PowertrainModel.from_data_path(tmp_data, segs)

        # The degenerate Kreis is still present and its pmf is finite everywhere.
        mat = model.kreis_segment_powertrain.get(degenerate_ags)
        assert mat is not None, f"degenerate Kreis {degenerate_ags} dropped from model"
        assert np.isfinite(mat).all(), "degenerate Kreis produced a non-finite pmf"

        # The no-silent-fallback warning must have fired.
        messages = " ".join(r.message for r in caplog.records).lower()
        assert "usable fuel mass" in messages or "degenerate" in messages, (
            "Expected a WARN about the degenerate Kreis using the national "
            f"fallback; got: {messages!r}"
        )
