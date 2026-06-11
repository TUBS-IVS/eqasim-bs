"""Tests for the Zensus 2022 INSPIRE grid cell handling (Phase 2).

The 100 m -> 1 km nesting is the spatial backbone for batching PopulationSim
runs, so it is exercised rigorously on small synthetic grids (no real data).
"""

from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.popsim import cells


def _toy_grid():
    """A clean 2-parent nesting plus one orphan child.

    Parent A (N2689000E4337000): children at +000/+100/+200 north, pop 1/2/3 -> 6.
    Parent B (N2690000E4341000): children at +000/+100, pop 4/5 -> 9.
    Orphan (parent N9999000E4337000 absent from the 1 km table): pop 3.
    """
    df_100m = pd.DataFrame(
        {
            "GITTER_ID_100m": [
                "CRS3035RES100mN2689000E4337000",
                "CRS3035RES100mN2689100E4337000",
                "CRS3035RES100mN2689200E4337000",
                "CRS3035RES100mN2690000E4341000",
                "CRS3035RES100mN2690100E4341000",
                "CRS3035RES100mN9999000E4337000",  # orphan
            ],
            "GITTER_ID_1km": [
                "CRS3035RES1000mN2689000E4337000",
                "CRS3035RES1000mN2689000E4337000",
                "CRS3035RES1000mN2689000E4337000",
                "CRS3035RES1000mN2690000E4341000",
                "CRS3035RES1000mN2690000E4341000",
                "CRS3035RES1000mN9999000E4337000",  # parent absent below
            ],
            "POP_TOTAL_100m": [1.0, 2.0, 3.0, 4.0, 5.0, 3.0],
        }
    )
    df_1km = pd.DataFrame(
        {
            "GITTER_ID_1km": [
                "CRS3035RES1000mN2689000E4337000",
                "CRS3035RES1000mN2690000E4341000",
            ],
            "POP_TOTAL_1km": [6.0, 9.0],
        }
    )
    return df_100m, df_1km


# ---------------------------------------------------------------------------
# parse_inspire_id
# ---------------------------------------------------------------------------

def test_parse_inspire_id_100m():
    res, north, east = cells.parse_inspire_id("CRS3035RES100mN2689100E4337000")
    assert res == 100
    assert north == 2689100
    assert east == 4337000


def test_parse_inspire_id_1km():
    res, north, east = cells.parse_inspire_id("CRS3035RES1000mN2689000E4337000")
    assert res == 1000
    assert north == 2689000
    assert east == 4337000


def test_parse_inspire_id_rejects_malformed():
    with pytest.raises(ValueError):
        cells.parse_inspire_id("not-a-grid-id")


# ---------------------------------------------------------------------------
# derive_1km_parent_id
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "child, parent",
    [
        ("CRS3035RES100mN2689100E4337000", "CRS3035RES1000mN2689000E4337000"),
        ("CRS3035RES100mN2690800E4341200", "CRS3035RES1000mN2690000E4341000"),
        ("CRS3035RES100mN2691900E4340800", "CRS3035RES1000mN2691000E4340000"),
        # SW corner of a 1 km cell maps to itself (floor is idempotent).
        ("CRS3035RES100mN2689000E4337000", "CRS3035RES1000mN2689000E4337000"),
    ],
)
def test_derive_1km_parent_id(child, parent):
    assert cells.derive_1km_parent_id(child) == parent


def test_derive_1km_parent_rejects_non_100m():
    with pytest.raises(ValueError):
        cells.derive_1km_parent_id("CRS3035RES1000mN2689000E4337000")


# ---------------------------------------------------------------------------
# check_nesting_consistency
# ---------------------------------------------------------------------------

def test_nesting_clean_grid_is_consistent():
    df_100m, df_1km = _toy_grid()
    report = cells.check_nesting_consistency(df_100m, df_1km)
    assert report.n_100m_cells == 6
    assert report.n_1km_parents_present == 2
    assert report.max_children_per_parent == 3
    assert report.parents_over_limit == []
    # Parents A and B reconcile exactly (6 and 9); orphan excluded.
    assert report.max_abs_pop_diff == pytest.approx(0.0, abs=1e-9)
    assert report.is_consistent is True


def test_nesting_detects_orphans_with_rate():
    df_100m, df_1km = _toy_grid()
    report = cells.check_nesting_consistency(df_100m, df_1km)
    assert report.n_orphan_cells == 1
    assert report.orphan_rate == pytest.approx(1 / 6)
    # Orphans are reported, not a consistency failure (they are handled).
    assert report.is_consistent is True


def test_nesting_flags_reconciliation_mismatch():
    df_100m, df_1km = _toy_grid()
    # Break parent B's 1 km population so the children no longer sum to it.
    df_1km.loc[df_1km["GITTER_ID_1km"].str.contains("N2690000E4341000"), "POP_TOTAL_1km"] = 99.0
    report = cells.check_nesting_consistency(df_100m, df_1km)
    assert report.max_abs_pop_diff == pytest.approx(90.0)  # |9 - 99|
    assert report.is_consistent is False


def test_nesting_flags_parents_over_child_limit():
    df_100m, df_1km = _toy_grid()
    # With an injected low limit, parent A (3 children) violates it.
    report = cells.check_nesting_consistency(df_100m, df_1km, max_children=2)
    assert "CRS3035RES1000mN2689000E4337000" in report.parents_over_limit
    assert report.is_consistent is False


def test_nesting_raises_when_explicit_parent_disagrees_with_derived():
    df_100m, df_1km = _toy_grid()
    # Corrupt the explicit parent of the first child.
    df_100m.loc[0, "GITTER_ID_1km"] = "CRS3035RES1000mN1111000E2222000"
    with pytest.raises(ValueError, match="explicit .* derived"):
        cells.check_nesting_consistency(df_100m, df_1km)
