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
