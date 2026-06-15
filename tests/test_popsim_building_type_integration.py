"""Integration test: building_type multi-column aggregation flows through build_control_totals.

Step 4 of TDD.  Verifies that when tier2 building_type controls are active, the
stage/build path:
1. loads the 10 source columns from the cells parquet,
2. aggregates them into the 3 derived ``building_type_*`` columns via
   ``prepared_cells.add_aggregated_controls``,
3. passes the derived columns as base_cols to ``mid.build_control_totals``, and
4. the resulting ``control_totals["ZENSUS100m"]`` contains the
   ``building_type_*_ZENSUS100m`` columns whose per-1km sums equal the summed
   source columns.

Uses fully synthetic data (no real parquet needed).
"""

from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.popsim import prepared_cells
from braunschweig.popsim import mid as midmod


# ---------------------------------------------------------------------------
# Synthetic cells with the 10 source columns
# ---------------------------------------------------------------------------

def _make_cells() -> pd.DataFrame:
    """Four 100m cells in two 1km parents."""
    return pd.DataFrame({
        "ZENSUS100m": ["C1", "C2", "C3", "C4"],
        "ZENSUS1km":  ["K1", "K1", "K2", "K2"],
        # Ein-/Zweifamilienhaus sources (6)
        "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter":    [10.0,  5.0,  8.0, 3.0],
        "EFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter":     [2.0,  3.0,  1.0, 0.0],
        "EFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": [1.0, 0.0, 4.0, 2.0],
        "Freist_ZFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter":  [0.0,  1.0,  2.0, 1.0],
        "ZFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter":     [3.0,  2.0,  0.0, 1.0],
        "ZFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": [1.0, 0.0, 1.0, 0.0],
        # Mehrfamilienhaus sources (3)
        "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter":  [20.0, 10.0, 5.0, 4.0],
        "MFH_7bis12Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": [ 5.0,  3.0, 2.0, 1.0],
        "MFH_13undmehrWohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": [2.0, 1.0, 0.0, 0.0],
        # Sonstiges (1)
        "AndererGebaeudetyp_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": [3.0, 2.0, 1.0, 0.0],
    })


_AGGREGATION_MAP = {
    "building_type_ein_zweifamilienhaus": (
        "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
        "EFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
        "EFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
        "Freist_ZFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
        "ZFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
        "ZFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
    ),
    "building_type_mehrfamilienhaus": (
        "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
        "MFH_7bis12Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
        "MFH_13undmehrWohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
    ),
    "building_type_sonstiges": (
        "AndererGebaeudetyp_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
    ),
}

_BASE_COLS = list(_AGGREGATION_MAP.keys())  # the 3 derived column names


def _build_totals(cells: pd.DataFrame):
    """Run add_aggregated_controls then build_control_totals and return the dict."""
    cells_with_derived = prepared_cells.add_aggregated_controls(cells, _AGGREGATION_MAP)
    geo_crosswalk = cells_with_derived[["ZENSUS100m", "ZENSUS1km"]].copy()
    return midmod.build_control_totals(
        cells_with_derived,
        geo_crosswalk,
        _BASE_COLS,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_build_control_totals_contains_building_type_columns() -> None:
    """The ZENSUS100m control totals table contains the derived building_type columns."""
    cells = _make_cells()
    totals = _build_totals(cells)
    df_100m = totals["ZENSUS100m"]
    for name in _BASE_COLS:
        assert f"{name}_ZENSUS100m" in df_100m.columns, (
            f"{name}_ZENSUS100m missing from control_totals ZENSUS100m frame"
        )


def test_build_control_totals_1km_sums_match_source_sums() -> None:
    """Per-1km sums of building_type_*_ZENSUS1km == sum of the source columns."""
    cells = _make_cells()
    # Pre-compute expected 1km sums from the raw source columns.
    expected_efh_k1 = (
        (10 + 2 + 1 + 0 + 3 + 1) +   # C1
        (5  + 3 + 0 + 1 + 2 + 0)      # C2
    )  # = 17 + 11 = 28
    expected_efh_k2 = (
        (8  + 1 + 4 + 2 + 0 + 1) +   # C3
        (3  + 0 + 2 + 1 + 1 + 0)      # C4
    )  # = 16 + 7 = 23

    totals = _build_totals(cells)
    df_1km = totals["ZENSUS1km"].set_index("ZENSUS1km")

    # Integer-rounded totals must match (build_control_totals integerises within parents).
    assert int(df_1km.loc["K1", "building_type_ein_zweifamilienhaus_ZENSUS1km"]) == expected_efh_k1
    assert int(df_1km.loc["K2", "building_type_ein_zweifamilienhaus_ZENSUS1km"]) == expected_efh_k2


def test_build_control_totals_100m_values_are_integer() -> None:
    """After integerization, the 100m building_type values must be whole numbers."""
    cells = _make_cells()
    totals = _build_totals(cells)
    df_100m = totals["ZENSUS100m"]
    for col in [f"{n}_ZENSUS100m" for n in _BASE_COLS]:
        vals = df_100m[col]
        assert (vals == vals.round(0)).all(), (
            f"Non-integer values found in {col}: {vals[vals != vals.round(0)].tolist()}"
        )


def test_build_control_totals_empty_aggregation_map_is_byte_identical() -> None:
    """With an empty aggregation_map, add_aggregated_controls returns the frame unchanged.

    This is the tier0-only baseline guard at the unit level:
    when no building_type controls are active, the cells frame is not altered.
    """
    cells = _make_cells()
    result = prepared_cells.add_aggregated_controls(cells, {})
    pd.testing.assert_frame_equal(result, cells)
