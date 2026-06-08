"""Tests for the popsim_mid orchestration helpers (Phase 5e).

Small, focused functions that fold the validated smoke logic into the package:
reading the control base columns, the notebook-faithful (per-geography suffixed,
hierarchically integerized) control totals, the ZGB spatial filter, and the
targeted cell load. Pure logic on tiny synthetic data.
"""

from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.popsim import folders
from braunschweig.popsim import mid


# ---------------------------------------------------------------------------
# control_base_columns
# ---------------------------------------------------------------------------

def _controls_df():
    return pd.DataFrame(
        {
            "target": ["A_ZENSUS100m_target", "B_ZENSUS100m_target", "A_ZENSUS1km_target"],
            "geography": ["ZENSUS100m", "ZENSUS100m", "ZENSUS1km"],
            "control_field": ["A_ZENSUS100m", "B_ZENSUS100m", "A_ZENSUS1km"],
        }
    )


def test_control_base_columns_strips_geography_suffix():
    assert mid.control_base_columns(_controls_df(), "ZENSUS100m") == ["A", "B"]
    assert mid.control_base_columns(_controls_df(), "ZENSUS1km") == ["A"]


def test_control_base_columns_deduplicates_preserving_order():
    df = pd.DataFrame(
        {"geography": ["ZENSUS100m"] * 3,
         "control_field": ["B_ZENSUS100m", "A_ZENSUS100m", "B_ZENSUS100m"]}
    )
    assert mid.control_base_columns(df, "ZENSUS100m") == ["B", "A"]


# ---------------------------------------------------------------------------
# build_control_totals (per-geography suffixed, hierarchically consistent)
# ---------------------------------------------------------------------------

def _targets_and_xwalk():
    df_100m = pd.DataFrame(
        {
            "GITTER_ID_100m": [
                "CRS3035RES100mN2689000E4337000",
                "CRS3035RES100mN2689100E4337000",
                "CRS3035RES100mN2690000E4341000",
            ],
            "GITTER_ID_1km": [
                "CRS3035RES1000mN2689000E4337000",
                "CRS3035RES1000mN2689000E4337000",
                "CRS3035RES1000mN2690000E4341000",
            ],
        }
    )
    xwalk = folders.build_geo_crosswalk(df_100m)
    targets = pd.DataFrame(
        {
            "ZENSUS100m": df_100m["GITTER_ID_100m"].to_numpy(),
            "POP": [1.4, 2.6, 5.0],  # parent A -> 4, parent B -> 5
        }
    )
    return targets, xwalk


def test_build_control_totals_suffixes_per_geography():
    targets, xwalk = _targets_and_xwalk()
    totals = mid.build_control_totals(targets, xwalk, ["POP"])
    assert "POP_ZENSUS100m" in totals["ZENSUS100m"].columns
    assert "POP_ZENSUS1km" in totals["ZENSUS1km"].columns
    assert set(totals) == {"ZENSUS100m", "ZENSUS1km", "STAAT", "WELT"}


def test_build_control_totals_hierarchically_consistent():
    targets, xwalk = _targets_and_xwalk()
    totals = mid.build_control_totals(targets, xwalk, ["POP"])
    df100 = totals["ZENSUS100m"]
    merged = df100.merge(xwalk[["ZENSUS100m", "ZENSUS1km"]], on="ZENSUS100m")
    recon = merged.groupby("ZENSUS1km")["POP_ZENSUS100m"].sum()
    df1km = totals["ZENSUS1km"].set_index("ZENSUS1km")["POP_ZENSUS1km"]
    for parent, value in df1km.items():
        assert recon[parent] == value
    # STAAT / WELT carry only the geography key, no controls (notebook + spec).
    assert list(totals["STAAT"].columns) == ["STAAT", "WELT"]
    assert totals["STAAT"].loc[0, "STAAT"] == 1
    assert list(totals["WELT"].columns) == ["WELT"]
    assert totals["WELT"].loc[0, "WELT"] == 1


# ---------------------------------------------------------------------------
# filter_zgb_cells
# ---------------------------------------------------------------------------

def test_filter_zgb_cells_by_kreis_ars5():
    cells = pd.DataFrame(
        {
            "ZENSUS100m": ["a", "b", "c"],
            "RegionalSchlussel_ARS": ["031010000000", "031530000000", "099990000000"],
        }
    )
    out = mid.filter_zgb_cells(cells, ["03101", "03153"])
    assert sorted(out["ZENSUS100m"]) == ["a", "b"]


def test_filter_zgb_cells_missing_column_raises():
    with pytest.raises(ValueError, match="ARS"):
        mid.filter_zgb_cells(pd.DataFrame({"ZENSUS100m": ["a"]}), ["03101"])
