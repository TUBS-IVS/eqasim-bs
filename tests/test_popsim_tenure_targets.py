"""Owner households are limited to the dwellings the Zensus owner quote covers (issue #657).

The cells below are hand-made; the expected values are computed by hand in the comments.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import control_spec
from braunschweig.popsim.tenure_targets import limit_owner_households_to_dwellings

OWNER = "owner_households"
RENTER = "renter_households"
HOUSEHOLDS = "household_total"
DWELLINGS = "dwellings"

KWARGS = dict(
    owner_col=OWNER, renter_col=RENTER, household_total_col=HOUSEHOLDS, dwelling_col=DWELLINGS,
)


def _cells() -> pd.DataFrame:
    """Four cells: dormitory, home, ordinary, small suppressed (in this row order)."""
    return pd.DataFrame({
        "cell": ["dormitory", "home", "ordinary", "small"],
        HOUSEHOLDS: [118.0, 98.0, 10.0, 3.0],
        OWNER: [118.0, 98.0, 6.0, 3.0],
        RENTER: [0.0, 0.0, 4.0, 0.0],
        DWELLINGS: [0.0, 3.0, 12.0, 0.0],
        "untouched": [1, 2, 3, 4],
    }).set_index("cell")


def test_owner_dwelling_column_is_the_rooms_table():
    assert control_spec.OWNER_QUOTE_DWELLING_COLUMN == (
        "Insgesamt_Wohnungen_Wohnungen_nach_Zahl_der_Raeume_100m_Gitter_adj")


def test_dormitory_cell_becomes_renters():
    # 118 households, no dwelling: 118 - 0 >= 10, owner = 118 * min(118, 0) / 118 = 0.
    result, _ = limit_owner_households_to_dwellings(_cells(), **KWARGS)
    assert result.loc["dormitory", OWNER] == 0.0
    assert result.loc["dormitory", RENTER] == 118.0


def test_home_cell_keeps_its_dwellings():
    # 98 households, 3 dwellings: 98 - 3 >= 10, owner = 98 * 3 / 98 = 3, renter = 95.
    result, _ = limit_owner_households_to_dwellings(_cells(), **KWARGS)
    assert result.loc["home", OWNER] == pytest.approx(3.0)
    assert result.loc["home", RENTER] == pytest.approx(95.0)


def test_ordinary_cell_is_unchanged():
    # 10 households, 12 dwellings: 10 - 12 < 10 (more dwellings than households).
    result, _ = limit_owner_households_to_dwellings(_cells(), **KWARGS)
    assert result.loc["ordinary", OWNER] == 6.0
    assert result.loc["ordinary", RENTER] == 4.0


def test_small_suppressed_cell_is_unchanged():
    # 3 households, no dwelling: 3 - 0 < 10, below the threshold, so the cell is left alone.
    result, _ = limit_owner_households_to_dwellings(_cells(), **KWARGS)
    assert result.loc["small", OWNER] == 3.0
    assert result.loc["small", RENTER] == 0.0


def test_report_counts():
    _, report = limit_owner_households_to_dwellings(_cells(), **KWARGS)
    # Dormitory moves 118 owners, home moves 98 - 3 = 95: 213 in total.
    assert report == {
        "cells_seen": 4,
        "cells_changed": 2,
        "households_moved_owner_to_renter": 213.0,
        "cells_with_nan_dwelling_count": 0,
        "cells_with_nan_tenure_count": 0,
        "cells_with_owner_above_household_total": 0,
        "min_households_outside": 10.0,
    }


@pytest.mark.parametrize(
    ("households", "owner", "dwellings", "expected_owner", "expected_renter"),
    [
        # owner 200 > 100 households: share clipped at 1; min(100, 0) = 0 dwellings -> owner 0.
        (100.0, 200.0, 0.0, 0.0, 100.0),
        # owner 120 > 100 households: share clipped at 1; min(100, 50) = 50 -> owner 50.
        (100.0, 120.0, 50.0, 50.0, 50.0),
    ],
)
def test_owner_above_household_total_is_clipped_and_reported(
        households, owner, dwellings, expected_owner, expected_renter):
    cells = pd.DataFrame({
        HOUSEHOLDS: [households], OWNER: [owner], RENTER: [0.0], DWELLINGS: [dwellings]})
    result, report = limit_owner_households_to_dwellings(cells, **KWARGS)
    assert result[OWNER].iloc[0] == pytest.approx(expected_owner)
    assert result[RENTER].iloc[0] == pytest.approx(expected_renter)
    assert report["cells_with_owner_above_household_total"] == 1
    assert report["cells_changed"] == 1


@pytest.mark.parametrize("column", [OWNER, RENTER])
def test_nan_tenure_count_is_reported_and_unchanged_cell_keeps_nan(column):
    cells = _cells()
    cells.loc["ordinary", column] = np.nan  # unchanged cell: the NaN must survive
    cells.loc["small", column] = np.nan
    result, report = limit_owner_households_to_dwellings(cells, **KWARGS)
    assert report["cells_with_nan_tenure_count"] == 2
    assert result.loc[["ordinary", "small"], column].isna().all()


def test_owner_plus_renter_stays_the_household_total_and_nothing_else_changes():
    cells = _cells()
    before = cells.copy()
    result, _ = limit_owner_households_to_dwellings(cells, **KWARGS)
    assert (result[OWNER] + result[RENTER]).tolist() == pytest.approx(
        (before[OWNER] + before[RENTER]).tolist())
    assert result[[HOUSEHOLDS, DWELLINGS, "untouched"]].equals(
        before[[HOUSEHOLDS, DWELLINGS, "untouched"]])
    assert result[OWNER].dtype == np.float64 and result[RENTER].dtype == np.float64
    # The input frame is not mutated.
    pd.testing.assert_frame_equal(cells, before)


def test_nan_dwelling_count_is_read_as_zero_and_reported():
    cells = _cells()
    cells.loc["dormitory", DWELLINGS] = np.nan
    result, report = limit_owner_households_to_dwellings(cells, **KWARGS)
    assert result.loc["dormitory", OWNER] == 0.0
    assert result.loc["dormitory", RENTER] == 118.0
    assert report["cells_with_nan_dwelling_count"] == 1


def test_threshold_is_configurable():
    # With a threshold of 3 the small cell (3 households, 0 dwellings) becomes renters.
    result, report = limit_owner_households_to_dwellings(
        _cells(), min_households_outside=3.0, **KWARGS)
    assert result.loc["small", OWNER] == 0.0
    assert result.loc["small", RENTER] == 3.0
    assert report["min_households_outside"] == 3.0
    assert report["cells_changed"] == 3


@pytest.mark.parametrize("threshold", [0.0, -1.0])
def test_non_positive_threshold_raises(threshold):
    with pytest.raises(ValueError, match="min_households_outside"):
        limit_owner_households_to_dwellings(_cells(), min_households_outside=threshold, **KWARGS)


def test_missing_column_is_named():
    with pytest.raises(KeyError, match=DWELLINGS):
        limit_owner_households_to_dwellings(_cells().drop(columns=[DWELLINGS]), **KWARGS)


@pytest.mark.parametrize("column", [HOUSEHOLDS, OWNER, RENTER, DWELLINGS])
def test_negative_count_raises(column):
    cells = _cells()
    cells.loc["home", column] = -1.0
    with pytest.raises(ValueError, match="negative"):
        limit_owner_households_to_dwellings(cells, **KWARGS)
