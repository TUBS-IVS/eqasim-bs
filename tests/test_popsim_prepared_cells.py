"""Tests for loading the prepared Zensus cell parquet (Phase 5b).

Ports the deterministic column preparation from the popsimprep notebook Step 2:
``clean_col_name`` normalisation, loading the prepared 100 m parquet, and
selecting the per-cell control-target table that feeds
``braunschweig.popsim.folders.build_control_totals``. Uses tiny synthetic
parquets only.
"""

from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.popsim import prepared_cells


# ---------------------------------------------------------------------------
# clean_col_name (faithful port)
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "raw, expected",
    [
        ("M_AGE_0-9", "M_AGE_0_9"),                 # dash -> underscore
        ("POP_TOTAL_100m-Gitter", "POP_TOTAL_100m_Gitter"),
        ("Insgesamt Haushalte Groesse", "InsgesamtHaushalteGroesse"),  # spaces dropped
        ("a.b,c d-e", "abcd_e"),                     # ". , space" dropped, "-" -> "_"
        ("Bevoelkerung", "Bevoelkerung"),
    ],
)
def test_clean_col_name(raw, expected):
    assert prepared_cells.clean_col_name(raw) == expected


def test_clean_col_name_transliterates_umlauts():
    # unidecode: oe-umlaut -> o, ss-ligature -> ss.
    assert prepared_cells.clean_col_name("Größe") == "Grosse"


# ---------------------------------------------------------------------------
# load_prepared_cells
# ---------------------------------------------------------------------------

def _toy_parquet(tmp_path):
    df = pd.DataFrame(
        {
            "GITTER_ID_100m": [
                "CRS3035RES100mN2689000E4337000",
                "CRS3035RES100mN2689100E4337000",
                "CRS3035RES100mN2690000E4341000",
            ],
            "POP_TOTAL_100m-Gitter": [1.0, 2.0, 5.0],
            "M_AGE_0-9": [0.0, 1.0, 2.0],
        }
    )
    path = tmp_path / "cells.parquet"
    df.to_parquet(path)
    return path


def test_load_prepared_cells_cleans_and_adds_geographies(tmp_path):
    cells = prepared_cells.load_prepared_cells(_toy_parquet(tmp_path))
    # Columns are cleaned; the id column is renamed to ZENSUS100m.
    assert "ZENSUS100m" in cells.columns
    assert "POP_TOTAL_100m_Gitter" in cells.columns
    assert "M_AGE_0_9" in cells.columns
    # Nested geographies added.
    for geo in ("ZENSUS1km", "STAAT", "WELT"):
        assert geo in cells.columns
    assert (cells["STAAT"] == 1).all()
    # 1 km parent derived from the 100 m id.
    row = cells.set_index("ZENSUS100m").loc["CRS3035RES100mN2689100E4337000"]
    assert row["ZENSUS1km"] == "CRS3035RES1000mN2689000E4337000"


# ---------------------------------------------------------------------------
# select_per_cell_targets
# ---------------------------------------------------------------------------

def test_select_per_cell_targets_returns_cell_id_plus_targets(tmp_path):
    cells = prepared_cells.load_prepared_cells(_toy_parquet(tmp_path))
    targets = prepared_cells.select_per_cell_targets(
        cells, ["POP_TOTAL_100m_Gitter", "M_AGE_0_9"]
    )
    assert list(targets.columns) == ["ZENSUS100m", "POP_TOTAL_100m_Gitter", "M_AGE_0_9"]
    assert len(targets) == 3


def test_select_per_cell_targets_fills_na_with_zero(tmp_path):
    cells = prepared_cells.load_prepared_cells(_toy_parquet(tmp_path))
    cells.loc[0, "M_AGE_0_9"] = None
    targets = prepared_cells.select_per_cell_targets(cells, ["M_AGE_0_9"])
    assert targets.loc[0, "M_AGE_0_9"] == 0


def test_select_per_cell_targets_unknown_column_raises(tmp_path):
    cells = prepared_cells.load_prepared_cells(_toy_parquet(tmp_path))
    with pytest.raises(ValueError, match="not present"):
        prepared_cells.select_per_cell_targets(cells, ["does_not_exist"])


# ---------------------------------------------------------------------------
# add_age_sex_band_aggregates (single-year -> banded _agg, matching the controls)
# ---------------------------------------------------------------------------

def test_add_age_sex_band_aggregates_sums_bands():
    df = pd.DataFrame({f"M_AGE_{i}": [float(i)] for i in range(101)})
    for i in range(101):
        df[f"F_AGE_{i}"] = [1.0]
    out = prepared_cells.add_age_sex_band_aggregates(df)
    # M_AGE values are i, so band 0-9 = 0+1+...+9 = 45; 80+ = sum(80..100).
    assert out["M_AGE_0_9_agg"][0] == sum(range(10))
    assert out["M_AGE_10_19_agg"][0] == sum(range(10, 20))
    assert out["M_AGE_80_plus_agg"][0] == sum(range(80, 101))
    # F all ones: band 0-9 has 10 years -> 10; 80+ has 80..100 -> 21.
    assert out["F_AGE_0_9_agg"][0] == 10
    assert out["F_AGE_80_plus_agg"][0] == 21
    # All nine bands x both sexes are created (the control_field set).
    for label in ["0_9", "10_19", "20_29", "30_39", "40_49",
                  "50_59", "60_69", "70_79", "80_plus"]:
        assert f"M_AGE_{label}_agg" in out.columns
        assert f"F_AGE_{label}_agg" in out.columns


def test_add_age_sex_band_aggregates_handles_missing_single_years():
    df = pd.DataFrame({"M_AGE_0": [2.0], "M_AGE_5": [3.0], "F_AGE_0": [1.0]})
    out = prepared_cells.add_age_sex_band_aggregates(df)
    assert out["M_AGE_0_9_agg"][0] == 5.0   # 2 + 3
    assert out["F_AGE_0_9_agg"][0] == 1.0
    assert out["M_AGE_10_19_agg"][0] == 0.0  # no source columns -> 0
