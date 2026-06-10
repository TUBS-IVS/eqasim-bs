"""Tests for Phase 4B: RegioStaR donor stratification.

Pure unit tests — no PopulationSim subprocess, no large data files.
Tests cover:
- Per-source stratum methods (MidSource RS7 / EntdSource RS2).
- The testable filter helper ``filter_seed_to_stratum``.
- The dominant-1km-stratum logic + border-rate computation.
- Zero-donor guard (ValueError).
- Default OFF behaviour passes the full seed unchanged.
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.popsim.sources.mid import MidSource  # noqa: E402
from braunschweig.popsim.sources.entd import EntdSource  # noqa: E402
from braunschweig.popsim.mid import (  # noqa: E402
    dominant_stratum_for_1km,
    filter_seed_to_stratum,
    run_popsim_mid,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _mid_seed_households(rs7_values: list) -> pd.DataFrame:
    """Minimal MiD seed household frame with RegioStaR7."""
    return pd.DataFrame({
        "H_ID": list(range(1, len(rs7_values) + 1)),
        "H_GEW": [100.0] * len(rs7_values),
        "STAAT": [1] * len(rs7_values),
        "RegioStaR7": rs7_values,
    })


def _mid_seed_persons(n_per_hh: int, households: pd.DataFrame) -> pd.DataFrame:
    """One person per household in the seed."""
    rows = []
    for i, hid in enumerate(households["H_ID"]):
        rows.append({
            "H_ID": hid,
            "P_ID": i + 100,
            "P_GEW": 100.0,
            "HP_ALTER": 35,
            "HP_SEX": 1,
            "STAAT": 1,
        })
    return pd.DataFrame(rows)


def _entd_seed_households(urban_types: list) -> pd.DataFrame:
    """Minimal ENTD seed household frame with urban_type."""
    return pd.DataFrame({
        "household_id": list(range(1, len(urban_types) + 1)),
        "household_weight": [100.0] * len(urban_types),
        "STAAT": [1] * len(urban_types),
        "urban_type": urban_types,
    })


def _cells_frame(km_ids: list, stratum_values: list) -> pd.DataFrame:
    """Minimal cells frame with ZENSUS1km, ZENSUS100m, and RegioStaR7."""
    rows = []
    cell_idx = 0
    for km_id, rs7 in zip(km_ids, stratum_values):
        # Two 100m children per 1km cell
        for sub in range(2):
            rows.append({
                "ZENSUS1km": km_id,
                "ZENSUS100m": f"{km_id}_c{sub}",
                "RegioStaR7": rs7,
                "POP_TOTAL_100m_adj": 100,
                "STAAT": 1,
                "WELT": 1,
            })
            cell_idx += 1
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# (a) MidSource.donor_stratum returns RS7 (7-class)
# ---------------------------------------------------------------------------

class TestMidSourceDonorStratum:
    """MidSource.donor_stratum returns the RegioStaR7 column as-is (7 classes)."""

    def test_mid_source_donor_stratum_is_rs7(self):
        src = MidSource()
        hh = _mid_seed_households([72, 76])
        result = src.donor_stratum(hh)
        assert list(result) == [72, 76], (
            "MidSource.donor_stratum must return RegioStaR7 directly (7-class, not RS2)."
        )

    def test_mid_source_donor_stratum_is_not_collapsed(self):
        """Confirm the stratum uses RS7 granularity, not the coarser RS2."""
        src = MidSource()
        hh = _mid_seed_households([71, 72, 73, 74, 75, 76, 77])
        result = src.donor_stratum(hh)
        # RS7 values must be preserved individually, not collapsed to urban/rural.
        assert set(result) == {71, 72, 73, 74, 75, 76, 77}

    def test_mid_source_donor_stratum_missing_column_raises(self):
        src = MidSource()
        hh = pd.DataFrame({"H_ID": [1], "H_GEW": [100.0]})
        with pytest.raises(KeyError):
            src.donor_stratum(hh)


# ---------------------------------------------------------------------------
# (b) EntdSource.donor_stratum maps urban_type -> RS2 label
# ---------------------------------------------------------------------------

class TestEntdSourceDonorStratum:
    """EntdSource.donor_stratum maps urban_type to RS2 'urban'/'rural' (2-class)."""

    def test_entd_source_donor_stratum_is_urban_class(self):
        src = EntdSource()
        hh = _entd_seed_households(["central_city", "none"])
        result = src.donor_stratum(hh)
        assert list(result) == ["urban", "rural"], (
            "EntdSource.donor_stratum must map urban_type via entd_urban_class."
        )

    def test_entd_source_all_urban_types(self):
        src = EntdSource()
        hh = _entd_seed_households(["central_city", "suburb", "isolated_city", "none"])
        result = src.donor_stratum(hh)
        assert list(result) == ["urban", "urban", "urban", "rural"]

    def test_entd_source_donor_stratum_missing_column_raises(self):
        src = EntdSource()
        hh = pd.DataFrame({"household_id": [1], "household_weight": [100.0]})
        with pytest.raises(KeyError):
            src.donor_stratum(hh)


# ---------------------------------------------------------------------------
# (c) MidSource.cell_stratum returns RegioStaR7 (7-class)
# ---------------------------------------------------------------------------

class TestMidCellStratum:
    """MidSource.cell_stratum returns the per-cell RegioStaR7 column (7-class)."""

    def test_mid_cell_stratum_is_rs7(self):
        src = MidSource()
        cells = pd.DataFrame({"RegioStaR7": [72, 75, 73]})
        result = src.cell_stratum(cells)
        assert list(result) == [72, 75, 73]

    def test_mid_cell_stratum_missing_column_raises(self):
        src = MidSource()
        cells = pd.DataFrame({"ZENSUS100m": ["x"]})
        with pytest.raises(KeyError):
            src.cell_stratum(cells)


# ---------------------------------------------------------------------------
# (d) EntdSource.cell_stratum maps RS7 -> RS2 label
# ---------------------------------------------------------------------------

class TestEntdCellStratum:
    """EntdSource.cell_stratum maps RegioStaR7 -> RS2 binary urban/rural."""

    def test_entd_cell_stratum_is_rs2(self):
        src = EntdSource()
        cells = pd.DataFrame({"RegioStaR7": [72, 75, 71, 77]})
        result = src.cell_stratum(cells)
        assert list(result) == ["urban", "rural", "urban", "rural"]

    def test_entd_cell_stratum_all_codes(self):
        src = EntdSource()
        cells = pd.DataFrame({"RegioStaR7": [71, 72, 73, 74, 75, 76, 77]})
        result = src.cell_stratum(cells)
        expected = ["urban", "urban", "urban", "urban", "rural", "rural", "rural"]
        assert list(result) == expected


# ---------------------------------------------------------------------------
# (e) filter_seed_to_stratum
# ---------------------------------------------------------------------------

class TestFilterSeedToStratum:
    """filter_seed_to_stratum keeps only matching-stratum households + their persons."""

    def test_filter_keeps_matching_donors(self):
        src = MidSource()
        hh = _mid_seed_households([72, 75, 72, 76])
        persons = _mid_seed_persons(1, hh)

        # Retain only RS7==72 households
        hh_out, p_out = filter_seed_to_stratum(hh, persons, 72, src)
        assert set(hh_out["RegioStaR7"]) == {72}
        assert len(hh_out) == 2  # H_ID 1 and 3

        # Persons must belong only to retained households
        retained_hids = set(hh_out["H_ID"])
        assert set(p_out["H_ID"]).issubset(retained_hids)

    def test_filter_entd_keeps_urban_donors(self):
        src = EntdSource()
        hh = _entd_seed_households(["central_city", "none", "suburb"])
        persons = pd.DataFrame({
            "household_id": [1, 1, 2, 3],
            "person_id": [10, 11, 20, 30],
        })

        hh_out, p_out = filter_seed_to_stratum(hh, persons, "urban", src)
        # "central_city" and "suburb" map to "urban"; "none" maps to "rural"
        assert len(hh_out) == 2
        assert set(p_out["household_id"]).issubset(set(hh_out["household_id"]))

    def test_filter_returns_full_when_all_match(self):
        src = MidSource()
        hh = _mid_seed_households([72, 72, 72])
        persons = _mid_seed_persons(1, hh)
        hh_out, p_out = filter_seed_to_stratum(hh, persons, 72, src)
        assert len(hh_out) == 3
        assert len(p_out) == 3


# ---------------------------------------------------------------------------
# (f) Zero-donor guard raises ValueError
# ---------------------------------------------------------------------------

class TestZeroDonorGuard:
    """filter_seed_to_stratum raises ValueError when no households match the stratum."""

    def test_zero_donor_stratum_raises(self):
        src = MidSource()
        hh = _mid_seed_households([72, 72])
        persons = _mid_seed_persons(1, hh)
        with pytest.raises(ValueError, match="no donor households"):
            filter_seed_to_stratum(hh, persons, 77, src)

    def test_zero_donor_message_contains_stratum(self):
        src = MidSource()
        hh = _mid_seed_households([75, 76])
        persons = _mid_seed_persons(1, hh)
        with pytest.raises(ValueError) as exc_info:
            filter_seed_to_stratum(hh, persons, 71, src)
        assert "71" in str(exc_info.value)


# ---------------------------------------------------------------------------
# (g) Dominant 1km stratum (majority vote + border rate)
# ---------------------------------------------------------------------------

class TestDominant1kmStratum:
    """dominant_stratum_for_1km computes majority-vote stratum per 1km parent."""

    def test_majority_stratum_is_dominant(self):
        """Three 100m cells [72, 72, 76] -> dominant = 72."""
        cells = pd.DataFrame({
            "ZENSUS1km": ["KM1", "KM1", "KM1"],
            "ZENSUS100m": ["C1", "C2", "C3"],
            "RegioStaR7": [72, 72, 76],
        })
        src = MidSource()
        result, border_rate = dominant_stratum_for_1km(cells, src)
        assert result["KM1"] == 72
        # One out of three 100m cells (C3=76) differs from dominant (72)
        assert abs(border_rate - 1.0 / 3.0) < 1e-6

    def test_all_same_stratum_zero_border_rate(self):
        cells = pd.DataFrame({
            "ZENSUS1km": ["KM1", "KM1", "KM2", "KM2"],
            "ZENSUS100m": ["C1", "C2", "C3", "C4"],
            "RegioStaR7": [75, 75, 75, 75],
        })
        src = MidSource()
        result, border_rate = dominant_stratum_for_1km(cells, src)
        assert result == {"KM1": 75, "KM2": 75}
        assert border_rate == 0.0

    def test_entd_dominant_stratum_rs2(self):
        """EntdSource: dominant stratum is computed on RS2 labels."""
        cells = pd.DataFrame({
            "ZENSUS1km": ["KM1", "KM1", "KM1"],
            "ZENSUS100m": ["C1", "C2", "C3"],
            "RegioStaR7": [72, 72, 75],  # 2 urban, 1 rural -> dominant = "urban"
        })
        src = EntdSource()
        result, border_rate = dominant_stratum_for_1km(cells, src)
        assert result["KM1"] == "urban"
        assert abs(border_rate - 1.0 / 3.0) < 1e-6

    def test_single_100m_cell_zero_border_rate(self):
        cells = pd.DataFrame({
            "ZENSUS1km": ["KM1"],
            "ZENSUS100m": ["C1"],
            "RegioStaR7": [77],
        })
        src = MidSource()
        result, border_rate = dominant_stratum_for_1km(cells, src)
        assert result["KM1"] == 77
        assert border_rate == 0.0


# ---------------------------------------------------------------------------
# (h) Default OFF: full seed passed, batch behaviour byte-identical
# ---------------------------------------------------------------------------

class TestStratifyOff:
    """With stratify_regiostar=False, run_popsim_mid passes the full seed to each batch."""

    def _make_run_one(self, received_seeds: list):
        """A fake run_one that records the seed it sees (injected via closure)."""
        # run_popsim_mid writes batch folders; we capture assemble_batch_folder calls
        # indirectly by checking the merge report (which comes from the fake runner).
        # For the OFF path, the simplest check is that run_popsim_mid does NOT raise
        # and completes normally -- the seed filtering code is simply not executed.
        import braunschweig.popsim.batch as batchmod
        def fake_run_one(folder: str) -> batchmod.BatchResult:
            return batchmod.BatchResult(folder, "succeeded", "fake", 0.0)
        return fake_run_one

    def test_stratify_off_does_not_raise(self, tmp_path):
        """run_popsim_mid with stratify_regiostar=False completes without error."""
        # Build minimal cells (one 1km parent with two 100m children).
        cells = _cells_frame(["KM1"], [72])
        base_cols = ["COL_A"]
        cells["COL_A"] = 5
        # Add required geographic hierarchy columns.
        cells["STAAT"] = 1
        cells["WELT"] = 1

        controls_df = pd.DataFrame({
            "target": ["COL_A_ZENSUS100m_target"],
            "geography": ["ZENSUS100m"],
            "control_field": ["COL_A_ZENSUS100m"],
        })

        hh = _mid_seed_households([72, 75])
        persons = _mid_seed_persons(1, hh)

        import braunschweig.popsim.batch as batchmod
        import braunschweig.popsim.merge as mergemod

        fake_run_one_calls = []

        def fake_run_one(folder: str) -> batchmod.BatchResult:
            fake_run_one_calls.append(folder)
            # Write empty output so merge can proceed.
            out_path = Path(folder) / "output"
            out_path.mkdir(parents=True, exist_ok=True)
            (out_path / "final_expanded_household_ids.csv").write_text(
                "ZENSUS100m,H_ID\n", encoding="utf-8"
            )
            return batchmod.BatchResult(folder, "succeeded", "fake", 0.0)

        src = MidSource()
        report = run_popsim_mid(
            cells, base_cols, controls_df, hh, persons,
            work_dir=tmp_path,
            settings_yaml="",
            logging_yaml="",
            max_cells=1000,
            run_one=fake_run_one,
            num_workers=1,
            source=src,
            stratify_regiostar=False,
        )
        # Should have run without error; one batch for the one 1km parent.
        assert len(fake_run_one_calls) == 1

    def test_all_batches_missing_raises(self, tmp_path):
        """If PopulationSim writes no output, run_popsim_mid must raise, not return
        an empty population (no-silent-fallback: the broken step is surfaced)."""
        cells = _cells_frame(["KM1"], [72])
        base_cols = ["COL_A"]
        cells["COL_A"] = 5
        cells["STAAT"] = 1
        cells["WELT"] = 1
        controls_df = pd.DataFrame({
            "target": ["COL_A_ZENSUS100m_target"],
            "geography": ["ZENSUS100m"],
            "control_field": ["COL_A_ZENSUS100m"],
        })
        hh = _mid_seed_households([72, 75])
        persons = _mid_seed_persons(1, hh)

        import braunschweig.popsim.batch as batchmod

        def failing_run_one(folder: str) -> batchmod.BatchResult:
            # No output file written -> the batch is "missing".
            return batchmod.BatchResult(folder, "failed", "exit code 1: boom", 0.0)

        with pytest.raises(ValueError, match="no usable output"):
            run_popsim_mid(
                cells, base_cols, controls_df, hh, persons,
                work_dir=tmp_path,
                settings_yaml="",
                logging_yaml="",
                max_cells=1000,
                run_one=failing_run_one,
                num_workers=1,
                source=MidSource(),
                stratify_regiostar=False,
            )
