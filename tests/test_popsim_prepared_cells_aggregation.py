"""Tests for prepared_cells.add_aggregated_controls (multi-column census aggregation).

Step 1 of TDD for the Tier-2 building_type control.  This exercises the new
helper that sums multiple census source columns into a single derived marginal
column (e.g. the 6 Ein-/Zweifamilienhaus source columns -> one
``building_type_ein_zweifamilienhaus`` column).
"""

from __future__ import annotations

import logging

import pandas as pd
import pytest

from braunschweig.popsim import prepared_cells


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_cells_with_10_sources() -> pd.DataFrame:
    """Synthetic cells frame with the 10 Gebaeudetyp source columns + geo keys."""
    return pd.DataFrame({
        "ZENSUS100m": ["A", "B", "C"],
        "ZENSUS1km": ["KM1", "KM1", "KM2"],
        # Ein-/Zweifamilienhaus sources (6 columns)
        "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": [10.0, 5.0, 0.0],
        "EFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": [2.0, 3.0, 1.0],
        "EFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": [1.0, 0.0, 4.0],
        "Freist_ZFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": [0.0, 1.0, 2.0],
        "ZFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": [3.0, 2.0, 0.0],
        "ZFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": [1.0, 0.0, 1.0],
        # Mehrfamilienhaus sources (3 columns)
        "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": [20.0, 10.0, 5.0],
        "MFH_7bis12Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": [5.0, 3.0, 2.0],
        "MFH_13undmehrWohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": [2.0, 1.0, 0.0],
        # Sonstiges (1 column)
        "AndererGebaeudetyp_Wohnung_Gebaeudetyp_Groesse_100m_Gitter": [3.0, 2.0, 1.0],
    })


# ---------------------------------------------------------------------------
# 1. add_aggregated_controls: correct sums (all source cols present)
# ---------------------------------------------------------------------------

def test_add_aggregated_controls_sums_ein_zweifamilienhaus():
    """The ein_zweifamilienhaus derived column is the row-sum of its 6 sources."""
    cells = _make_cells_with_10_sources()
    aggregation_map = {
        "building_type_ein_zweifamilienhaus": (
            "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "EFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "EFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "Freist_ZFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "ZFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "ZFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
        ),
    }
    result = prepared_cells.add_aggregated_controls(cells, aggregation_map)

    # Row 0: 10+2+1+0+3+1 = 17
    assert result.loc[0, "building_type_ein_zweifamilienhaus"] == pytest.approx(17.0)
    # Row 1: 5+3+0+1+2+0 = 11
    assert result.loc[1, "building_type_ein_zweifamilienhaus"] == pytest.approx(11.0)
    # Row 2: 0+1+4+2+0+1 = 8
    assert result.loc[2, "building_type_ein_zweifamilienhaus"] == pytest.approx(8.0)


def test_add_aggregated_controls_sums_mehrfamilienhaus():
    """The mehrfamilienhaus derived column is the row-sum of its 3 sources."""
    cells = _make_cells_with_10_sources()
    aggregation_map = {
        "building_type_mehrfamilienhaus": (
            "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "MFH_7bis12Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "MFH_13undmehrWohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
        ),
    }
    result = prepared_cells.add_aggregated_controls(cells, aggregation_map)

    # Row 0: 20+5+2 = 27
    assert result.loc[0, "building_type_mehrfamilienhaus"] == pytest.approx(27.0)
    # Row 1: 10+3+1 = 14
    assert result.loc[1, "building_type_mehrfamilienhaus"] == pytest.approx(14.0)
    # Row 2: 5+2+0 = 7
    assert result.loc[2, "building_type_mehrfamilienhaus"] == pytest.approx(7.0)


def test_add_aggregated_controls_sums_sonstiges():
    """The sonstiges derived column is the row-sum of its single source (identity)."""
    cells = _make_cells_with_10_sources()
    aggregation_map = {
        "building_type_sonstiges": (
            "AndererGebaeudetyp_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
        ),
    }
    result = prepared_cells.add_aggregated_controls(cells, aggregation_map)
    # Single-source: sum == identity of the source column.
    pd.testing.assert_series_equal(
        result["building_type_sonstiges"],
        cells["AndererGebaeudetyp_Wohnung_Gebaeudetyp_Groesse_100m_Gitter"],
        check_names=False,
    )


def test_add_aggregated_controls_all_three_at_once():
    """All three building_type derived columns produced in one call."""
    cells = _make_cells_with_10_sources()
    aggregation_map = {
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
    result = prepared_cells.add_aggregated_controls(cells, aggregation_map)

    for derived in (
        "building_type_ein_zweifamilienhaus",
        "building_type_mehrfamilienhaus",
        "building_type_sonstiges",
    ):
        assert derived in result.columns, f"Derived column {derived!r} not found"

    # Original columns must be preserved.
    for src_col in cells.columns:
        assert src_col in result.columns, f"Source column {src_col!r} was dropped"


# ---------------------------------------------------------------------------
# 2. add_aggregated_controls: missing source column -> logged warning + partial sum
# ---------------------------------------------------------------------------

def test_add_aggregated_controls_missing_source_logs_warning_and_partial_sum(caplog):
    """A missing source column is warned about; the partial sum uses what is present."""
    cells = _make_cells_with_10_sources().drop(
        columns=["EFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter"]
    )
    aggregation_map = {
        "building_type_ein_zweifamilienhaus": (
            "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "EFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",  # MISSING
            "EFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "Freist_ZFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "ZFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "ZFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
        ),
    }
    with caplog.at_level(logging.WARNING):
        result = prepared_cells.add_aggregated_controls(cells, aggregation_map)

    # Must log at WARNING mentioning the missing column.
    warns = [r for r in caplog.records if "EFH_DHH" in r.message]
    assert warns, "No WARNING logged for the missing source column"
    assert warns[0].levelno == logging.WARNING

    # Partial sum: row 0 -> 10+1+0+3+1 = 15 (EFH_DHH=2 excluded)
    assert result.loc[0, "building_type_ein_zweifamilienhaus"] == pytest.approx(15.0)


# ---------------------------------------------------------------------------
# 2b. add_aggregated_controls: ALL sources missing -> raise (issue #149)
# ---------------------------------------------------------------------------

def test_add_aggregated_controls_all_sources_missing_raises():
    """When EVERY source column of a derived control is absent, raise instead of
    silently emitting a constant-0 target (no-silent-fallback rule, issue #149).

    A renamed/removed Gebaeudetyp column in a re-prepared parquet would otherwise
    yield a permanently-zero control that PopulationSim balances to 0 unnoticed.
    """
    cells = _make_cells_with_10_sources()
    aggregation_map = {
        "building_type_ghost": (
            "COLUMN_THAT_DOES_NOT_EXIST_1",
            "COLUMN_THAT_DOES_NOT_EXIST_2",
        ),
    }
    with pytest.raises(ValueError, match="building_type_ghost"):
        prepared_cells.add_aggregated_controls(cells, aggregation_map)


# ---------------------------------------------------------------------------
# 2c. add_aggregated_controls: NaN in a present source is observable (issue #150)
# ---------------------------------------------------------------------------

def test_add_aggregated_controls_nan_in_source_is_logged(caplog):
    """A NaN (e.g. Zensus-suppressed) cell in a present source column is counted and
    logged; the sum still treats it as 0 (skipna) but the suppression is observable
    per the fallback-transparency rule (issue #150)."""
    cells = _make_cells_with_10_sources()
    cells.loc[0, "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter"] = float("nan")
    aggregation_map = {
        "building_type_mehrfamilienhaus": (
            "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "MFH_7bis12Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
            "MFH_13undmehrWohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
        ),
    }
    with caplog.at_level(logging.INFO):
        result = prepared_cells.add_aggregated_controls(cells, aggregation_map)

    # A NaN-count log for this derived column must appear (observable suppression).
    nan_logs = [r for r in caplog.records if "NaN" in r.message
                and "building_type_mehrfamilienhaus" in r.message]
    assert nan_logs, "No NaN-count log emitted for the suppressed source cell"

    # Behaviour preserved: NaN treated as 0 -> row 0 = 0(nan)+5+2 = 7.
    assert result.loc[0, "building_type_mehrfamilienhaus"] == pytest.approx(7.0)
    # Row 1 unaffected: 10+3+1 = 14.
    assert result.loc[1, "building_type_mehrfamilienhaus"] == pytest.approx(14.0)


# ---------------------------------------------------------------------------
# 3. Single-source identity: name already in cells -> must be identity (no copy overhead)
# ---------------------------------------------------------------------------

def test_add_aggregated_controls_single_source_is_identity():
    """Single-source aggregation (name in cells) produces identical values."""
    cells = pd.DataFrame({
        "ZENSUS100m": ["X"],
        "some_existing_col": [42.0],
    })
    aggregation_map = {
        "some_existing_col": ("some_existing_col",),
    }
    result = prepared_cells.add_aggregated_controls(cells, aggregation_map)
    assert result.loc[0, "some_existing_col"] == pytest.approx(42.0)


# ---------------------------------------------------------------------------
# 4. Empty aggregation_map: returns frame unchanged (tier0-only guard)
# ---------------------------------------------------------------------------

def test_add_aggregated_controls_empty_map_returns_unchanged():
    """An empty aggregation_map must return the frame byte-identical."""
    cells = _make_cells_with_10_sources()
    result = prepared_cells.add_aggregated_controls(cells, {})
    pd.testing.assert_frame_equal(result, cells)
