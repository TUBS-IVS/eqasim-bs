"""Tests for braunschweig.analysis.population_validation.fleet_evaluation.

Two test groups:
1. Data-absent safety: None / empty / missing-columns frames return {}.
2. Happy-path: a synthetic fleet with correct columns produces fleet_overview.png
   + fleet_summary.csv with zero contradictions.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "eqasim-data" / "data"
sys.path.insert(0, str(REPO))

DATA_PATH = str(DATA)

from braunschweig.analysis.population_validation import fleet_evaluation as FE  # noqa: E402


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Mapping: powertrain -> a fuel_detail string that is CONSISTENT (no contradiction).
_CONSISTENT_FUEL: dict[str, str] = {
    "petrol": "Benzin",
    "diesel": "Diesel",
    "bev": "Elektro",
    "phev": "Benzin/Elektro Plug-in",
    "hybrid": "Benzin/Elektro",
    "gas": "Benzin/Autogas",
    "hydrogen": "Wasserstoff/Elektro",
}

_STATUSES = ("very_low", "low", "medium", "high", "very_high")
_SEGMENTS = ("mini", "small", "compact", "midsize", "large", "suv")
_POWERTRAINS = ("petrol", "diesel", "bev", "phev")
_BRANDS = ("VW", "BMW", "Mercedes", "Audi", "Ford", "Toyota",
           "Opel", "Renault", "Skoda", "Peugeot",
           "Hyundai", "Kia", "Seat", "Nissan", "Honda")


def _make_fleet(n: int = 500, seed: int = 42, include_mode: bool = True) -> pd.DataFrame:
    """Build a small but realistic synthetic fleet frame for testing.

    All fuel_detail values are consistent with the respective powertrain so the
    expected contradiction count is 0.
    """
    rng = np.random.default_rng(seed)
    n_status = len(_STATUSES)
    n_pt = len(_POWERTRAINS)
    n_seg = len(_SEGMENTS)
    n_brands = len(_BRANDS)

    rows = []
    for i in range(n):
        status_idx = i % n_status
        pt = _POWERTRAINS[i % n_pt]
        seg = _SEGMENTS[i % n_seg]
        brand = _BRANDS[i % n_brands]
        # Age varies with status: very_low -> older, very_high -> newer.
        base_age = float(15 - status_idx * 2 + rng.uniform(-1, 1))
        base_age = max(0.5, base_age)

        row = {
            "brand": brand,
            "model": f"{brand} Model{i % 5}",
            "powertrain": pt,
            "age": base_age,
            "economic_status": _STATUSES[status_idx],
            "segment": seg,
            "fuel_detail": _CONSISTENT_FUEL[pt],
            "kreis_ags5": "03101",
            "engine_power_kw": float(80 + (i % 10) * 5),
            "displacement_ccm": float(1400 + (i % 6) * 200),
        }
        if include_mode:
            row["mode"] = "car"
        rows.append(row)
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 1. Data-absent safety
# ---------------------------------------------------------------------------

class TestDataAbsentSafe:

    def test_none_returns_empty(self, tmp_path):
        result = FE.build_fleet_evaluation(None, tmp_path, DATA_PATH)
        assert result == {}

    def test_empty_df_returns_empty(self, tmp_path):
        result = FE.build_fleet_evaluation(pd.DataFrame(), tmp_path, DATA_PATH)
        assert result == {}

    def test_missing_required_columns_returns_empty(self, tmp_path):
        # Frame that has some but not all required columns.
        df = pd.DataFrame({
            "brand": ["VW"],
            "powertrain": ["petrol"],
            # Missing: age, economic_status, segment, fuel_detail
        })
        result = FE.build_fleet_evaluation(df, tmp_path, DATA_PATH)
        assert result == {}

    def test_all_required_missing_returns_empty(self, tmp_path):
        df = pd.DataFrame({"some_col": [1, 2, 3]})
        result = FE.build_fleet_evaluation(df, tmp_path, DATA_PATH)
        assert result == {}

    def test_no_files_written_when_absent(self, tmp_path):
        FE.build_fleet_evaluation(None, tmp_path, DATA_PATH)
        assert not (tmp_path / "fleet_overview.png").exists()
        assert not (tmp_path / "fleet_summary.csv").exists()

    def test_empty_mode_filter_returns_empty(self, tmp_path):
        """All vehicles are car_passenger -> mode filter removes all rows."""
        df = _make_fleet(n=100)
        df["mode"] = "car_passenger"   # override all rows
        result = FE.build_fleet_evaluation(df, tmp_path, DATA_PATH)
        assert result == {}


# ---------------------------------------------------------------------------
# 2. Happy-path: synthetic fleet with zero contradictions
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def fleet_result(tmp_path_factory):
    """Run build_fleet_evaluation once for the module; return (result, out_dir)."""
    out = tmp_path_factory.mktemp("fleet_eval_happy")
    df = _make_fleet(n=600)
    result = FE.build_fleet_evaluation(df, out, DATA_PATH)
    return result, out


class TestHappyPath:

    def test_returns_nonempty_dict(self, fleet_result):
        result, _ = fleet_result
        assert isinstance(result, dict)
        assert len(result) > 0

    def test_overview_png_exists_and_nonzero(self, fleet_result):
        result, out = fleet_result
        assert "fleet_overview.png" in result
        png_path = out / "fleet_overview.png"
        assert png_path.exists(), "fleet_overview.png not written"
        assert png_path.stat().st_size > 0, "fleet_overview.png is empty"

    def test_summary_csv_exists_and_nonzero(self, fleet_result):
        result, out = fleet_result
        assert "fleet_summary.csv" in result
        csv_path = out / "fleet_summary.csv"
        assert csv_path.exists(), "fleet_summary.csv not written"
        assert csv_path.stat().st_size > 0, "fleet_summary.csv is empty"

    def test_summary_csv_has_expected_columns(self, fleet_result):
        _, out = fleet_result
        df = pd.read_csv(out / "fleet_summary.csv")
        assert "section" in df.columns
        assert "key" in df.columns
        assert "value" in df.columns

    def test_summary_csv_has_brand_section(self, fleet_result):
        _, out = fleet_result
        df = pd.read_csv(out / "fleet_summary.csv")
        assert "brand" in df["section"].values

    def test_summary_csv_has_powertrain_section(self, fleet_result):
        _, out = fleet_result
        df = pd.read_csv(out / "fleet_summary.csv")
        assert "powertrain" in df["section"].values

    def test_summary_csv_has_consistency_section(self, fleet_result):
        _, out = fleet_result
        df = pd.read_csv(out / "fleet_summary.csv")
        assert "consistency" in df["section"].values

    def test_summary_csv_has_age_status_section(self, fleet_result):
        _, out = fleet_result
        df = pd.read_csv(out / "fleet_summary.csv")
        assert "age_status" in df["section"].values

    def test_zero_contradictions_in_consistent_fleet(self, fleet_result):
        """All fuel_detail values are consistent -> contradiction count must be 0."""
        _, out = fleet_result
        df = pd.read_csv(out / "fleet_summary.csv")
        consistency_rows = df[df["section"] == "consistency"]
        contradiction_row = consistency_rows[consistency_rows["key"] == "contradiction_count"]
        assert len(contradiction_row) == 1, "contradiction_count row missing from summary CSV"
        assert int(contradiction_row["value"].iloc[0]) == 0

    def test_contradictions_counter_direct(self, tmp_path):
        """Unit-test _count_contradictions directly: consistent fleet -> 0."""
        df = _make_fleet(n=200)
        assert FE._count_contradictions(df) == 0

    def test_contradictions_counter_detects_bad_rows(self, tmp_path):
        """A diesel car with 'Benzin' fuel_detail is a contradiction."""
        df = _make_fleet(n=100)
        # Inject one contradiction: diesel car with Benzin label.
        df_bad = df.copy()
        diesel_idx = df_bad[df_bad["powertrain"] == "diesel"].index
        if len(diesel_idx) > 0:
            df_bad.loc[diesel_idx[0], "fuel_detail"] = "Benzin"
            assert FE._count_contradictions(df_bad) >= 1

    def test_mode_filter_only_keeps_cars(self, tmp_path):
        """With mode column present, non-car rows are filtered out."""
        df = _make_fleet(n=200)
        # Add some non-car rows.
        extra = df.iloc[:20].copy()
        extra["mode"] = "car_passenger"
        combined = pd.concat([df, extra], ignore_index=True)
        result = FE.build_fleet_evaluation(combined, tmp_path, DATA_PATH)
        # Result should still be non-empty (car rows remain).
        assert result != {}
        assert (tmp_path / "fleet_overview.png").exists()

    def test_result_paths_match_actual_files(self, fleet_result):
        """Paths in the returned dict must exist on disk."""
        result, _ = fleet_result
        for name, path_str in result.items():
            assert Path(path_str).exists(), f"result path for {name!r} does not exist: {path_str}"

    def test_fleet_without_mode_column(self, tmp_path):
        """Frame without 'mode' column: all rows used (no filter applied)."""
        df = _make_fleet(n=200, include_mode=False)
        assert "mode" not in df.columns
        result = FE.build_fleet_evaluation(df, tmp_path, DATA_PATH)
        assert result != {}
        assert (tmp_path / "fleet_overview.png").exists()
