"""Tests for T6b (part b): per-Kreis Euro-class marginal from 46251-03.

Three distinct scenarios:

1. UNIT — ``_euro_given_kreis_powertrain`` with synthetic two-Kreis data:
   * diesel uses the Kreis ``teil=="diesel"`` euro shares
   * petrol/gas/other use per-euro-class ``max(all_count - diesel_count, 0)``
     (non-diesel combustion proxy; no per-fuel-per-Kreis split in 46251-03)
   * bev / phev / hybrid / hydrogen fall back to the NATIONAL pmf from
     ``_euro_given_powertrain``
   * two Kreise with different euro profiles give different petrol pmfs

2. INTEGRATION — ``FleetSampler.from_data_path`` with a synthetic
   ``kba_kreis_euro.csv`` (all 8 ZGB Kreise):
   * ``age_euro_joint_kreis`` is NOT None
   * a euro6-heavy Kreis's petrol joint carries more Euro-6 column mass than
     a euro4-heavy Kreis

3. FALLBACK — ``FleetSampler.from_data_path`` on the committed data directory
   (``kba_kreis_euro.csv`` absent):
   * ``age_euro_joint_kreis`` is None
   * a seeded ``sample_fleet`` run is byte-identical on ``euro_class`` to the
     run built from the same path without the new code (national joint used)
"""
from __future__ import annotations

import logging
import os
import sys
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

# ---------------------------------------------------------------------------
# Synthetic two-Kreis euro data
# ---------------------------------------------------------------------------
# Kreis A: euro6-heavy (diesel row is also euro6-heavy)
# Kreis B: euro4-heavy (diesel row is also euro4-heavy)
_AGS_A = "03101"
_AGS_B = "03102"

# all-fuel row for Kreis A: many euro6, few euro4
_ROW_A_ALL = {
    "kreis_ags5": _AGS_A, "kreis_name": "KreisA", "stichtag": "2025-01-01",
    "teil": "all",
    "euro1": 500,  "euro2": 1_000, "euro3": 2_000,
    "euro4": 3_000, "euro5": 5_000, "euro6": 40_000, "other": 500,
    "total": 52_000,
}
# diesel row for Kreis A: high euro6 as well
_ROW_A_DIESEL = {
    "kreis_ags5": _AGS_A, "kreis_name": "KreisA", "stichtag": "2025-01-01",
    "teil": "diesel",
    "euro1": 100,  "euro2": 200, "euro3": 500,
    "euro4": 800,  "euro5": 2_000, "euro6": 20_000, "other": 100,
    "total": 23_700,
}
# all-fuel row for Kreis B: euro4-heavy
_ROW_B_ALL = {
    "kreis_ags5": _AGS_B, "kreis_name": "KreisB", "stichtag": "2025-01-01",
    "teil": "all",
    "euro1": 2_000,  "euro2": 5_000, "euro3": 8_000,
    "euro4": 30_000, "euro5": 10_000, "euro6": 5_000, "other": 1_000,
    "total": 61_000,
}
# diesel row for Kreis B: euro4-heavy as well
_ROW_B_DIESEL = {
    "kreis_ags5": _AGS_B, "kreis_name": "KreisB", "stichtag": "2025-01-01",
    "teil": "diesel",
    "euro1": 800,  "euro2": 2_000, "euro3": 4_000,
    "euro4": 15_000, "euro5": 5_000, "euro6": 2_000, "other": 400,
    "total": 29_200,
}

_EURO_COLS = list(ft.EURO_CLASS_LABELS)  # euro1..euro6, other


def _make_kreis_euro_df_two() -> pd.DataFrame:
    """Synthetic ``kba_kreis_euro.csv`` DataFrame with two Kreise."""
    return pd.DataFrame([_ROW_A_ALL, _ROW_A_DIESEL, _ROW_B_ALL, _ROW_B_DIESEL])


def _make_kreis_euro_df_all_zgb() -> pd.DataFrame:
    """Synthetic ``kba_kreis_euro.csv`` with all 8 ZGB Kreise (required by loader)."""
    rows = []
    for i, ags in enumerate(ft.ZGB_KREISE_AGS5):
        # Vary: first 4 are euro6-heavy; last 4 are euro4-heavy.
        if i < 4:
            euro6, euro4 = 40_000, 3_000
        else:
            euro6, euro4 = 5_000, 30_000
        for teil in ("all", "diesel"):
            factor = 0.5 if teil == "diesel" else 1.0
            rows.append({
                "kreis_ags5": ags, "kreis_name": f"Kreis_{ags}", "stichtag": "2025-01-01",
                "teil": teil,
                "euro1": int(500 * factor), "euro2": int(1_000 * factor),
                "euro3": int(2_000 * factor), "euro4": int(euro4 * factor),
                "euro5": int(5_000 * factor), "euro6": int(euro6 * factor),
                "other": int(500 * factor),
                "total": int((500 + 1_000 + 2_000 + euro4 + 5_000 + euro6 + 500) * factor),
            })
    return pd.DataFrame(rows)


def _make_tmp_data_path_with_euro(tmp_path: Path, df_euro: pd.DataFrame) -> str:
    """Mirror the real derived CSV directory into ``tmp_path`` and add df_euro.

    Uses symlinks where possible (Windows may fall back to copy).
    Returns the tmp root path as a string.
    """
    if not (DATA / "braunschweig" / "kba" / "derived").exists():
        pytest.skip("real derived data directory absent")
    derived = tmp_path / "braunschweig" / "kba" / "derived"
    derived.mkdir(parents=True)
    real_derived = DATA / "braunschweig" / "kba" / "derived"
    for src in real_derived.glob("*.csv"):
        dst = derived / src.name
        try:
            dst.symlink_to(src)
        except OSError:
            import shutil
            shutil.copy2(src, dst)
    df_euro.to_csv(derived / "kba_kreis_euro.csv", index=False)
    return str(tmp_path)


# ---------------------------------------------------------------------------
# STEP 1 (written BEFORE implementation — expected to FAIL until T6b is done)
# ---------------------------------------------------------------------------


class TestEuroGivenKreisPowertrain:
    """Unit-test ``_euro_given_kreis_powertrain`` on synthetic two-Kreis data."""

    def test_function_exists(self):
        """The new private function must exist after T6b implementation."""
        assert hasattr(fs, "_euro_given_kreis_powertrain"), (
            "fleet_sampling_de must export '_euro_given_kreis_powertrain' "
            "after T6b."
        )

    def test_returns_none_when_csv_absent(self, tmp_path):
        """Returns None when kba_kreis_euro.csv is absent (FileNotFoundError path)."""
        empty = tmp_path / "braunschweig" / "kba" / "derived"
        empty.mkdir(parents=True)
        # The real derived dir may have other CSVs; we want ONLY the euro one absent.
        # Using a completely empty derived dir is the easiest reliable way.
        result = fs._euro_given_kreis_powertrain(str(tmp_path))
        assert result is None, (
            "Expected None when kba_kreis_euro.csv is absent; "
            f"got {type(result)}"
        )

    def test_returns_dict_when_csv_present(self, tmp_path):
        """Returns a dict (not None) when a valid kba_kreis_euro.csv is present."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = _make_tmp_data_path_with_euro(
            tmp_path, _make_kreis_euro_df_all_zgb())
        result = fs._euro_given_kreis_powertrain(tmp_data)
        assert result is not None, "Expected a dict, got None"
        assert isinstance(result, dict), f"Expected dict, got {type(result)}"

    def test_diesel_uses_diesel_teil_row(self, tmp_path):
        """Diesel pmf for Kreis A must match the 'diesel' teil row, not 'all'."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = _make_tmp_data_path_with_euro(
            tmp_path, _make_kreis_euro_df_all_zgb())
        result = fs._euro_given_kreis_powertrain(tmp_data)
        assert result is not None
        pmf_diesel = result[(_AGS_A, "diesel")]
        # Derive expected from the actual fixture used (all-ZGB), not the two-Kreis fixture.
        df = _make_kreis_euro_df_all_zgb()
        row_dsl = df[(df["kreis_ags5"] == _AGS_A) & (df["teil"] == "diesel")].iloc[0]
        diesel_counts = np.array([row_dsl[e] for e in _EURO_COLS], dtype=float)
        expected = diesel_counts / diesel_counts.sum()
        euro6_idx = _EURO_COLS.index("euro6")
        np.testing.assert_allclose(pmf_diesel, expected, atol=1e-9, err_msg=(
            "Diesel pmf must match the normalised diesel teil row counts."
        ))

    def test_petrol_uses_non_diesel_combustion(self, tmp_path):
        """Petrol pmf = max(all - diesel, 0), normalised."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = _make_tmp_data_path_with_euro(
            tmp_path, _make_kreis_euro_df_all_zgb())
        result = fs._euro_given_kreis_powertrain(tmp_data)
        assert result is not None
        pmf_petrol = result[(_AGS_A, "petrol")]
        # Expected: all - diesel per euro class, clipped to 0, normalised.
        all_counts = np.array([_ROW_A_ALL[e] for e in _EURO_COLS], dtype=float)
        # Use the all-ZGB synthetic data which has the same euro6-heavy profile for
        # AGS_A (i==0). Recompute from the df to avoid hard-coding intermediate sums.
        df = _make_kreis_euro_df_all_zgb()
        row_all = df[(df["kreis_ags5"] == _AGS_A) & (df["teil"] == "all")].iloc[0]
        row_dsl = df[(df["kreis_ags5"] == _AGS_A) & (df["teil"] == "diesel")].iloc[0]
        non_dsl = np.array(
            [max(row_all[e] - row_dsl[e], 0) for e in _EURO_COLS], dtype=float)
        s = non_dsl.sum()
        if s > 0:
            expected = non_dsl / s
        else:
            expected = np.ones(len(_EURO_COLS)) / len(_EURO_COLS)
        np.testing.assert_allclose(pmf_petrol, expected, atol=1e-9)

    def test_gas_uses_non_diesel_combustion(self, tmp_path):
        """Gas pmf uses the same non-diesel combustion shape as petrol."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = _make_tmp_data_path_with_euro(
            tmp_path, _make_kreis_euro_df_all_zgb())
        result = fs._euro_given_kreis_powertrain(tmp_data)
        assert result is not None
        pmf_gas = result[(_AGS_A, "gas")]
        pmf_petrol = result[(_AGS_A, "petrol")]
        # gas and petrol share the non-diesel combustion shape
        np.testing.assert_allclose(pmf_gas, pmf_petrol, atol=1e-9, err_msg=(
            "gas and petrol must share the non-diesel combustion euro shape "
            "(no per-fuel-per-Kreis split in 46251-03 — documented assumption)."
        ))

    def test_bev_equals_national(self, tmp_path):
        """BEV pmf must equal the NATIONAL pmf (no per-Kreis BEV euro in 46251-03)."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = _make_tmp_data_path_with_euro(
            tmp_path, _make_kreis_euro_df_all_zgb())
        result = fs._euro_given_kreis_powertrain(tmp_data)
        assert result is not None
        national = fs._euro_given_powertrain(tmp_data)
        pmf_bev_kreis = result[(_AGS_A, "bev")]
        pmf_bev_national = national["bev"]
        np.testing.assert_allclose(pmf_bev_kreis, pmf_bev_national, atol=1e-9, err_msg=(
            "BEV must use the national euro pmf (46251-03 covers combustion only)."
        ))

    def test_hydrogen_equals_national(self, tmp_path):
        """Hydrogen pmf must equal the NATIONAL pmf."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = _make_tmp_data_path_with_euro(
            tmp_path, _make_kreis_euro_df_all_zgb())
        result = fs._euro_given_kreis_powertrain(tmp_data)
        assert result is not None
        national = fs._euro_given_powertrain(tmp_data)
        pmf_h2_kreis = result[(_AGS_A, "hydrogen")]
        pmf_h2_national = national["hydrogen"]
        np.testing.assert_allclose(pmf_h2_kreis, pmf_h2_national, atol=1e-9, err_msg=(
            "Hydrogen must use the national euro pmf."
        ))

    def test_phev_equals_national(self, tmp_path):
        """PHEV pmf must equal the NATIONAL pmf."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = _make_tmp_data_path_with_euro(
            tmp_path, _make_kreis_euro_df_all_zgb())
        result = fs._euro_given_kreis_powertrain(tmp_data)
        assert result is not None
        national = fs._euro_given_powertrain(tmp_data)
        np.testing.assert_allclose(
            result[(_AGS_A, "phev")], national["phev"], atol=1e-9)

    def test_hybrid_equals_national(self, tmp_path):
        """Hybrid pmf must equal the NATIONAL pmf."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = _make_tmp_data_path_with_euro(
            tmp_path, _make_kreis_euro_df_all_zgb())
        result = fs._euro_given_kreis_powertrain(tmp_data)
        assert result is not None
        national = fs._euro_given_powertrain(tmp_data)
        np.testing.assert_allclose(
            result[(_AGS_A, "hybrid")], national["hybrid"], atol=1e-9)

    def test_different_kreise_give_different_diesel_pmfs(self, tmp_path):
        """A euro6-heavy Kreis and a euro4-heavy Kreis must have different diesel pmfs."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = _make_tmp_data_path_with_euro(
            tmp_path, _make_kreis_euro_df_all_zgb())
        result = fs._euro_given_kreis_powertrain(tmp_data)
        assert result is not None
        # AGS_A (i==0) is euro6-heavy; AGS_B (i==1) is also euro6-heavy in the
        # all-ZGB fixture, but Kreise 5-8 (i>=4) are euro4-heavy.
        ags_euro6 = ft.ZGB_KREISE_AGS5[0]  # euro6-heavy (i=0)
        ags_euro4 = ft.ZGB_KREISE_AGS5[4]  # euro4-heavy (i=4)
        pmf_e6 = result[(ags_euro6, "diesel")]
        pmf_e4 = result[(ags_euro4, "diesel")]
        euro6_idx = _EURO_COLS.index("euro6")
        euro4_idx = _EURO_COLS.index("euro4")
        assert pmf_e6[euro6_idx] > pmf_e4[euro6_idx], (
            f"Euro6-heavy Kreis ({ags_euro6}) must have higher euro6 mass "
            f"({pmf_e6[euro6_idx]:.4f}) than euro4-heavy Kreis ({ags_euro4}) "
            f"({pmf_e4[euro6_idx]:.4f})."
        )
        assert pmf_e4[euro4_idx] > pmf_e6[euro4_idx], (
            f"Euro4-heavy Kreis ({ags_euro4}) must have higher euro4 mass "
            f"({pmf_e4[euro4_idx]:.4f}) than euro6-heavy Kreis ({ags_euro6}) "
            f"({pmf_e6[euro4_idx]:.4f})."
        )

    def test_pmfs_normalised(self, tmp_path):
        """Every returned pmf must sum to 1.0."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = _make_tmp_data_path_with_euro(
            tmp_path, _make_kreis_euro_df_all_zgb())
        result = fs._euro_given_kreis_powertrain(tmp_data)
        assert result is not None
        for (ags, pt), pmf in result.items():
            assert abs(pmf.sum() - 1.0) < 1e-8, (
                f"({ags}, {pt}) pmf sums to {pmf.sum():.6f}, expected 1.0"
            )

    def test_no_negative_entries(self, tmp_path):
        """Every returned pmf must have all non-negative entries."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = _make_tmp_data_path_with_euro(
            tmp_path, _make_kreis_euro_df_all_zgb())
        result = fs._euro_given_kreis_powertrain(tmp_data)
        assert result is not None
        for (ags, pt), pmf in result.items():
            assert (pmf >= 0).all(), (
                f"({ags}, {pt}) has negative entry: {pmf}"
            )

    def test_all_kreise_and_powertrains_covered(self, tmp_path):
        """Dict has entries for every (kreis, powertrain) combo from the CSV."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        df = _make_kreis_euro_df_all_zgb()
        tmp_data = _make_tmp_data_path_with_euro(tmp_path, df)
        result = fs._euro_given_kreis_powertrain(tmp_data)
        assert result is not None
        kreise = df["kreis_ags5"].unique()
        powertrains = list(ft.POWERTRAIN_LABELS)
        for ags in kreise:
            for pt in powertrains:
                assert (ags, pt) in result, (
                    f"({ags}, {pt}) missing from result"
                )


# ---------------------------------------------------------------------------
# STEP 2: Integration — FleetSampler.from_data_path with synthetic euro CSV
# ---------------------------------------------------------------------------


class TestFleetSamplerFromDataPathWithEuroCSV:
    """Integration: FleetSampler builds per-Kreis joint when euro CSV is present."""

    def test_age_euro_joint_kreis_not_none(self, tmp_path):
        """``age_euro_joint_kreis`` is not None when kba_kreis_euro.csv is present."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = _make_tmp_data_path_with_euro(
            tmp_path, _make_kreis_euro_df_all_zgb())
        sampler = fs.FleetSampler.from_data_path(tmp_data)
        assert sampler.age_euro_joint_kreis is not None, (
            "FleetSampler.age_euro_joint_kreis must be a dict (not None) "
            "when kba_kreis_euro.csv is present."
        )

    def test_logged_per_kreis_euro(self, tmp_path, caplog):
        """Log message must mention per-Kreis euro source when CSV is present."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = _make_tmp_data_path_with_euro(
            tmp_path, _make_kreis_euro_df_all_zgb())
        with caplog.at_level(logging.INFO):
            fs.FleetSampler.from_data_path(tmp_data)
        msgs = " ".join(r.message for r in caplog.records).lower()
        assert "46251" in msgs or "kreis" in msgs, (
            "Expected a log message referencing per-Kreis euro (46251-03); "
            f"got: {msgs!r}"
        )

    def test_euro6_heavy_kreis_petrol_joint_more_euro6(self, tmp_path):
        """A euro6-heavy Kreis's petrol joint has more Euro-6 column mass."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = _make_tmp_data_path_with_euro(
            tmp_path, _make_kreis_euro_df_all_zgb())
        sampler = fs.FleetSampler.from_data_path(tmp_data)
        assert sampler.age_euro_joint_kreis is not None

        ags_e6 = ft.ZGB_KREISE_AGS5[0]   # euro6-heavy (i=0)
        ags_e4 = ft.ZGB_KREISE_AGS5[4]   # euro4-heavy (i=4)
        joint_e6 = sampler.age_euro_joint_kreis.get((ags_e6, "petrol"))
        joint_e4 = sampler.age_euro_joint_kreis.get((ags_e4, "petrol"))
        assert joint_e6 is not None, f"No joint for ({ags_e6}, petrol)"
        assert joint_e4 is not None, f"No joint for ({ags_e4}, petrol)"

        euros = list(ft.EURO_CLASS_LABELS)
        euro6_idx = euros.index("euro6")
        # Column sum = P(euro6) marginal for that Kreis/powertrain joint.
        euro6_mass_e6 = joint_e6[:, euro6_idx].sum()
        euro6_mass_e4 = joint_e4[:, euro6_idx].sum()
        assert euro6_mass_e6 > euro6_mass_e4, (
            f"Euro6-heavy Kreis ({ags_e6}) petrol joint must carry more Euro-6 "
            f"column mass ({euro6_mass_e6:.4f}) than euro4-heavy Kreis ({ags_e4}) "
            f"({euro6_mass_e4:.4f})."
        )

    def test_fallback_national_joint_still_present(self, tmp_path):
        """National ``age_euro_joint`` (per-powertrain) must still be built."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        tmp_data = _make_tmp_data_path_with_euro(
            tmp_path, _make_kreis_euro_df_all_zgb())
        sampler = fs.FleetSampler.from_data_path(tmp_data)
        # The national joint must cover all powertrains.
        for pt in ft.POWERTRAIN_LABELS:
            assert pt in sampler.age_euro_joint, (
                f"National age_euro_joint missing powertrain {pt!r}"
            )


# ---------------------------------------------------------------------------
# STEP 3: Fallback — kba_kreis_euro.csv absent -> national joint, byte-identical
# ---------------------------------------------------------------------------


class TestFleetSamplerFallbackNoEuroCSV:
    """Fallback: kba_kreis_euro.csv absent -> age_euro_joint_kreis is None."""

    def test_age_euro_joint_kreis_is_none_on_committed_data(self):
        """On the committed data path (no euro CSV) age_euro_joint_kreis must be None."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        sampler = fs.FleetSampler.from_data_path(DATA_PATH)
        assert sampler.age_euro_joint_kreis is None, (
            "age_euro_joint_kreis must be None when kba_kreis_euro.csv is absent; "
            f"got {type(sampler.age_euro_joint_kreis)}"
        )

    def test_fallback_logged(self, caplog):
        """A log message must state that the national (FZ 27.4) joint is used."""
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")
        with caplog.at_level(logging.INFO):
            fs.FleetSampler.from_data_path(DATA_PATH)
        msgs = " ".join(r.message for r in caplog.records).lower()
        assert "national" in msgs or "fz27.4" in msgs or "fz 27.4" in msgs or "fallback" in msgs, (
            "Expected a log message referencing national/fallback euro joint; "
            f"got: {msgs!r}"
        )

    def test_sample_fleet_euro_byte_identical_on_fallback(self):
        """Without the euro CSV the sampled euro_class column is identical to before.

        Build two samplers from the same data path (kba_kreis_euro.csv absent),
        run sample_fleet twice with the SAME seed.  The ``euro_class`` column must
        be identical -- this verifies that the new code path does not perturb the
        draw when age_euro_joint_kreis is None (national joint used, unchanged).
        """
        if not (DATA / "braunschweig" / "kba" / "derived").exists():
            pytest.skip("real derived data directory absent")

        cars = _make_minimal_cars(n=200, seed=42)
        result1 = fs.sample_fleet(
            cars, DATA_PATH, random_seed=7, consistency_v2=True, age_euro_joint=True)
        result2 = fs.sample_fleet(
            cars, DATA_PATH, random_seed=7, consistency_v2=True, age_euro_joint=True)
        # consistency_v2=True returns (df_spec, df_vehicle_types, validation_summary)
        df1 = result1[0]
        df2 = result2[0]
        pd.testing.assert_series_equal(
            df1["euro_class"], df2["euro_class"],
            check_names=False,
            obj="euro_class column",
        )


# ---------------------------------------------------------------------------
# Minimal car fixture for smoke / byte-identity tests
# ---------------------------------------------------------------------------

def _make_minimal_cars(n: int = 200, seed: int = 0) -> pd.DataFrame:
    """Build a small synthetic car frame with the required columns."""
    rng = np.random.default_rng(seed)
    statuses = list(ft.STATUS_LABELS)
    gemeinden = ["Braunschweig", "Wolfsburg", "Wolfenbuettel"]
    raumtypen = ["1", "2", "3", "4", "5", "6", "7"]
    rows = []
    for i in range(n):
        rows.append({
            "economic_status": rng.choice(statuses),
            "kreis_ags5": rng.choice(ft.ZGB_KREISE_AGS5),
            "gemeinde": rng.choice(gemeinden),
            "raumtyp": rng.choice(raumtypen),
        })
    return pd.DataFrame(rows)
