"""Tests for the popsim_mid orchestration helpers (Phase 5e).

Small, focused functions that fold the validated smoke logic into the package:
reading the control base columns, the notebook-faithful (per-geography suffixed,
hierarchically integerized) control totals, the ZGB spatial filter, and the
targeted cell load. Pure logic on tiny synthetic data.
"""

from __future__ import annotations

from pathlib import Path

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


def test_build_control_totals_fills_suppressed_nan_with_zero():
    """A Zensus-suppressed (NaN) control cell must be filled with 0, not crash.

    Mirrors the real data condition (one inhabited ZGB cell carries NaN in the
    household-size control). The cell is kept (population preserved) and the
    control integerizes to 0 for that cell; the no-silent-fallback log records it.
    """
    targets, xwalk = _targets_and_xwalk()
    targets = targets.copy()
    targets.loc[0, "POP"] = float("nan")  # suppressed cell in parent A
    totals = mid.build_control_totals(targets, xwalk, ["POP"])
    df100 = totals["ZENSUS100m"].set_index("ZENSUS100m")["POP_ZENSUS100m"]
    # The suppressed cell integerizes to 0; the other cell in parent A keeps its 3.
    assert df100.iloc[0] == 0
    assert df100.notna().all()
    # Hierarchy still consistent (1km total = sum of 100m).
    merged = totals["ZENSUS100m"].merge(
        xwalk[["ZENSUS100m", "ZENSUS1km"]], on="ZENSUS100m"
    )
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


# ---------------------------------------------------------------------------
# run_popsim_mid (orchestration; PopulationSim injected)
# ---------------------------------------------------------------------------

def _orchestration_inputs():
    df_100m = pd.DataFrame(
        {
            "GITTER_ID_100m": [
                "CRS3035RES100mN2689000E4337000",
                "CRS3035RES100mN2689100E4337000",
                "CRS3035RES100mN2690000E4341000",
                "CRS3035RES100mN2691000E4342000",
            ],
            "GITTER_ID_1km": [
                "CRS3035RES1000mN2689000E4337000",
                "CRS3035RES1000mN2689000E4337000",
                "CRS3035RES1000mN2690000E4341000",
                "CRS3035RES1000mN2691000E4342000",
            ],
        }
    )
    xwalk = folders.build_geo_crosswalk(df_100m)
    cells = xwalk.copy()
    cells["POP"] = [1.0, 2.0, 3.0, 4.0]
    controls_df = pd.DataFrame(
        {"target": ["POP_ZENSUS100m_target"], "geography": ["ZENSUS100m"],
         "seed_table": ["persons"], "importance": [1000],
         "control_field": ["POP_ZENSUS100m"], "expression": ["(persons.P_GEW > 0)"]}
    )
    seed_hh = pd.DataFrame({"H_ID": [1], "H_GEW": [2.0], "STAAT": [1]})
    seed_p = pd.DataFrame({"H_ID": [1], "P_ID": [1], "STAAT": [1]})
    return cells, ["POP"], controls_df, seed_hh, seed_p


def test_run_popsim_mid_batches_runs_and_merges(tmp_path):
    cells, base_cols, controls_df, seed_hh, seed_p = _orchestration_inputs()

    def fake_run_one(folder):
        # Simulate PopulationSim: write one expanded household per 100m cell.
        from braunschweig.popsim import batch as b
        xwalk = pd.read_csv(Path(folder) / "data" / "geo_cross_walk.csv", dtype=str)
        out_dir = Path(folder) / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        xwalk.assign(H_ID=1).to_csv(out_dir / "final_expanded_household_ids.csv", index=False)
        return b.BatchResult(str(folder), "succeeded", "ok", 0.0)

    report = mid.run_popsim_mid(
        cells, base_cols, controls_df, seed_hh, seed_p,
        work_dir=tmp_path, settings_yaml="x: 1\n", logging_yaml="version: 1\n",
        max_cells=2, run_one=fake_run_one, num_workers=1,
    )
    # 3 parents, max_cells=2 -> at least 2 batches; merged covers all 4 cells.
    assert report.n_loaded >= 2
    assert report.n_cells == 4
    assert report.n_rows == 4


# ---------------------------------------------------------------------------
# synpp stage contract
# ---------------------------------------------------------------------------

def test_popsim_stage_exposes_synpp_contract():
    from braunschweig.popsim import stage
    assert callable(stage.configure)
    assert callable(stage.execute)


def test_load_mid_attributes_reads_needed_columns(tmp_path):
    # Phase 4A: RegioStaR7 is now part of MID_HOUSEHOLD_ATTR_COLS so the fixture
    # must include it; load_mid_attributes uses usecols=list(MID_HOUSEHOLD_ATTR_COLS).
    # hhgr_gr / alter_gr1 are the conditioning columns for grouped item-nonresponse
    # imputation (bugfix wave), so the fixtures must include them.
    # H_GR / H_GEW and P_GEW / kernwo joined the attribute usecols so the
    # member-completed frames can serve BOTH expansion and the PopulationSim seed.
    (tmp_path / "MiD2023_Haushalte.csv").write_text(
        "H_ID,oek_status,hheink_gr1,H_ANZAUTO,H_ANZRAD,RegioStaR7,hhgr_gr,H_GR,H_GEW\n1,3,4,1,2,72,2,2,1.0\n",
        encoding="utf-8",
    )
    # P_BKAT (Berufskategorie) is required for map_socioprofessional_class (bug D4 fix).
    (tmp_path / "MiD2023_Personen.csv").write_text(
        "H_ID,P_ID,HP_ALTER,HP_SEX,P_TAET,P_FSCHEIN,P_FKARTE,P_BKAT,alter_gr1,P_GEW,kernwo\n1,1,40,1,1,1,3,1,5,1.0,1\n",
        encoding="utf-8",
    )
    households, persons = mid.load_mid_attributes(tmp_path)
    assert {"oek_status", "hheink_gr1", "H_ANZAUTO", "H_ANZRAD", "RegioStaR7", "hhgr_gr",
            "H_GR", "H_GEW"} <= set(households.columns)
    assert {"P_TAET", "P_FSCHEIN", "P_FKARTE", "HP_ALTER", "HP_SEX", "P_BKAT", "alter_gr1",
            "P_GEW", "kernwo"} <= set(persons.columns)

