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
# assemble_batch_folder: Tier-3 KREIS controls (optional branch)
# ---------------------------------------------------------------------------

def _kreis_batch_inputs():
    cells_subset = pd.DataFrame({
        "ZENSUS100m": ["c1", "c2", "c3"],
        "ZENSUS1km":  ["p", "p", "q"],
        "STAAT": 1, "WELT": 1,
        "RegionalSchlussel_ARS": ["031010000000", "031010000000", "031530000000"],
        "POP_TOTAL_100m_adj": [10.0, 10.0, 5.0],
        "POP": [1.0, 2.0, 3.0],
    })
    controls_df = pd.DataFrame(
        {"target": ["POP_ZENSUS100m_target"], "geography": ["ZENSUS100m"],
         "seed_table": ["persons"], "importance": [1000],
         "control_field": ["POP_ZENSUS100m"], "expression": ["(persons.P_GEW > 0)"]}
    )
    seed_hh = pd.DataFrame({"H_ID": [1], "H_GEW": [2.0], "STAAT": [1]})
    seed_p = pd.DataFrame({"H_ID": [1], "P_ID": [1], "STAAT": [1]})
    return cells_subset, ["POP"], controls_df, seed_hh, seed_p


def test_assemble_batch_folder_writes_kreis_controls_when_given(tmp_path):
    cells_subset, base_cols, controls_df, seed_hh, seed_p = _kreis_batch_inputs()
    kreis_table = pd.DataFrame({"ARS_kreis": ["03101", "03153"], "E11": [100.0, 200.0]})
    written = mid.assemble_batch_folder(
        tmp_path / "b", cells_subset, base_cols, controls_df, seed_hh, seed_p,
        settings_yaml="x: 1\n", logging_yaml="version: 1\n",
        kreis_table=kreis_table, kreis_controls_map={"employed": ("E11",)},
    )
    kreis_csv = tmp_path / "b" / "data" / "control_totals_KREIS.csv"
    assert kreis_csv.is_file()
    assert "control_totals_KREIS.csv" in written
    df = pd.read_csv(kreis_csv, dtype={"KREIS": str})  # preserve the zero-padded 5-digit Kreis key
    assert list(df["KREIS"]) == ["03101", "03153"]   # crosswalk Kreise, deduped+sorted
    assert list(df["employed"]) == [100.0, 200.0]


def test_assemble_batch_folder_apportions_kreis_by_pop_share(tmp_path):
    # A Kreis split across batches: this batch holds a POP share of the Kreis, so its
    # KREIS marginal target is the full marginal * (batch Kreis pop / full Kreis pop).
    # cells_subset (this batch) holds parent "p" of Kreis 03101 with POP_TOTAL 20;
    # the FULL Kreis 03101 pop is 50 -> weight 0.4 -> employed_KREIS = 100*0.4 = 40.
    cells_subset, base_cols, controls_df, seed_hh, seed_p = _kreis_batch_inputs()
    kreis_table = pd.DataFrame({"ARS_kreis": ["03101", "03153"], "E11": [100.0, 200.0]})
    written = mid.assemble_batch_folder(
        tmp_path / "ba", cells_subset, base_cols, controls_df, seed_hh, seed_p,
        settings_yaml="x: 1\n", logging_yaml="version: 1\n",
        kreis_table=kreis_table, kreis_controls_map={"employed": ("E11",)},
        kreis_total_pop={"03101": 50.0, "03153": 5.0},
    )
    df = pd.read_csv(tmp_path / "ba" / "data" / "control_totals_KREIS.csv", dtype={"KREIS": str})
    by = df.set_index("KREIS")["employed"]
    # 03101: cells_subset pop = 10+10 = 20; full = 50 -> 0.4 -> 100*0.4 = 40
    assert by["03101"] == pytest.approx(40.0)
    # 03153: cells_subset pop = 5; full = 5 -> 1.0 -> 200*1.0 = 200
    assert by["03153"] == pytest.approx(200.0)


def test_assemble_batch_folder_kreis_total_pop_none_is_full_marginal(tmp_path):
    # kreis_total_pop=None -> legacy full marginal (no apportionment), unchanged.
    cells_subset, base_cols, controls_df, seed_hh, seed_p = _kreis_batch_inputs()
    kreis_table = pd.DataFrame({"ARS_kreis": ["03101", "03153"], "E11": [100.0, 200.0]})
    mid.assemble_batch_folder(
        tmp_path / "bn", cells_subset, base_cols, controls_df, seed_hh, seed_p,
        settings_yaml="x: 1\n", logging_yaml="version: 1\n",
        kreis_table=kreis_table, kreis_controls_map={"employed": ("E11",)},
        kreis_total_pop=None,
    )
    df = pd.read_csv(tmp_path / "bn" / "data" / "control_totals_KREIS.csv", dtype={"KREIS": str})
    assert df.set_index("KREIS")["employed"].tolist() == [100.0, 200.0]


def test_kreis_apportionment_cross_batch_sum_invariant(tmp_path):
    # The core invariant: a single Kreis split across two batches by population
    # (60% in batch A, 40% in batch B). Each batch's apportioned employed_KREIS is
    # its share of the full marginal, and A + B == the full Kreis marginal.
    # One Kreis (03101), two 1km parents pA / pB; pA pop = 60, pB pop = 40 -> total 100.
    kreis_table = pd.DataFrame({"ARS_kreis": ["03101"], "E11": [1000.0]})
    full_pop = {"03101": 100.0}

    def _batch_cells(parent, pop):
        return pd.DataFrame({
            "ZENSUS100m": [f"{parent}_c1"],
            "ZENSUS1km": [parent],
            "STAAT": 1, "WELT": 1,
            "RegionalSchlussel_ARS": ["031010000000"],
            "POP_TOTAL_100m_adj": [pop],
            "POP": [pop],
        })

    controls_df = pd.DataFrame(
        {"target": ["POP_ZENSUS100m_target"], "geography": ["ZENSUS100m"],
         "seed_table": ["persons"], "importance": [1000],
         "control_field": ["POP_ZENSUS100m"], "expression": ["(persons.P_GEW > 0)"]}
    )
    seed_hh = pd.DataFrame({"H_ID": [1], "H_GEW": [2.0], "STAAT": [1]})
    seed_p = pd.DataFrame({"H_ID": [1], "P_ID": [1], "STAAT": [1]})

    def _emp(folder):
        df = pd.read_csv(Path(folder) / "data" / "control_totals_KREIS.csv", dtype={"KREIS": str})
        return df.set_index("KREIS")["employed"]["03101"]

    mid.assemble_batch_folder(
        tmp_path / "A", _batch_cells("pA", 60.0), ["POP"], controls_df, seed_hh, seed_p,
        settings_yaml="x: 1\n", logging_yaml="version: 1\n",
        kreis_table=kreis_table, kreis_controls_map={"employed": ("E11",)},
        kreis_total_pop=full_pop,
    )
    mid.assemble_batch_folder(
        tmp_path / "B", _batch_cells("pB", 40.0), ["POP"], controls_df, seed_hh, seed_p,
        settings_yaml="x: 1\n", logging_yaml="version: 1\n",
        kreis_table=kreis_table, kreis_controls_map={"employed": ("E11",)},
        kreis_total_pop=full_pop,
    )
    emp_a, emp_b = _emp(tmp_path / "A"), _emp(tmp_path / "B")
    assert emp_a == pytest.approx(600.0)   # 1000 * 60/100
    assert emp_b == pytest.approx(400.0)   # 1000 * 40/100
    assert emp_a + emp_b == pytest.approx(1000.0)   # cross-batch sum == full marginal


def test_run_popsim_mid_tier3_apportions_kreis_across_batches(tmp_path):
    # End-to-end through run_popsim_mid: one Kreis spread over two 1km parents,
    # forced into two batches (max_cells=1). The merged-across-batches employed_KREIS
    # target must equal the FULL Kreis marginal (apportionment sums to 1).
    cells = pd.DataFrame({
        "ZENSUS100m": ["pA_c1", "pB_c1"],
        "ZENSUS1km": ["pA", "pB"],
        "STAAT": 1, "WELT": 1,
        "RegionalSchlussel_ARS": ["031010000000", "031010000000"],
        "POP_TOTAL_100m_adj": [60.0, 40.0],
        "POP": [60.0, 40.0],
    })
    controls_df = pd.DataFrame(
        {"target": ["POP_ZENSUS100m_target"], "geography": ["ZENSUS100m"],
         "seed_table": ["persons"], "importance": [1000],
         "control_field": ["POP_ZENSUS100m"], "expression": ["(persons.P_GEW > 0)"]}
    )
    seed_hh = pd.DataFrame({"H_ID": [1], "H_GEW": [2.0], "STAAT": [1]})
    seed_p = pd.DataFrame({"H_ID": [1], "P_ID": [1], "STAAT": [1]})
    kreis_table = pd.DataFrame({"ARS_kreis": ["03101"], "E11": [1000.0]})

    captured: list[float] = []

    def fake_run_one(folder):
        from braunschweig.popsim import batch as b
        kpath = Path(folder) / "data" / "control_totals_KREIS.csv"
        df = pd.read_csv(kpath, dtype={"KREIS": str})
        captured.append(float(df.set_index("KREIS")["employed"]["03101"]))
        xwalk = pd.read_csv(Path(folder) / "data" / "geo_cross_walk.csv", dtype=str)
        out_dir = Path(folder) / "output"
        out_dir.mkdir(parents=True, exist_ok=True)
        xwalk.assign(H_ID=1).to_csv(out_dir / "final_expanded_household_ids.csv", index=False)
        return b.BatchResult(str(folder), "succeeded", "ok", 0.0)

    mid.run_popsim_mid(
        cells, ["POP"], controls_df, seed_hh, seed_p,
        work_dir=tmp_path, settings_yaml="x: 1\n", logging_yaml="version: 1\n",
        max_cells=1, run_one=fake_run_one, num_workers=1,
        kreis_table=kreis_table, kreis_controls_map={"employed": ("E11",)},
    )
    assert len(captured) == 2  # two batches (one per 1km parent)
    assert sum(captured) == pytest.approx(1000.0)  # apportioned targets sum to full marginal
    assert sorted(captured) == pytest.approx([400.0, 600.0])


def test_assemble_batch_folder_omits_kreis_without_table(tmp_path):
    # No kreis_table -> tier0-2 path: no KREIS control file (byte-identical baseline).
    cells_subset, base_cols, controls_df, seed_hh, seed_p = _kreis_batch_inputs()
    mid.assemble_batch_folder(
        tmp_path / "b0", cells_subset, base_cols, controls_df, seed_hh, seed_p,
        settings_yaml="x: 1\n", logging_yaml="version: 1\n",
    )
    assert not (tmp_path / "b0" / "data" / "control_totals_KREIS.csv").exists()


def test_merge_kreis_control_tables_joins_topics_on_ars():
    # The three cleancensus kreis_* topic tables (erwerb/schul/berufl) merge into one
    # table keyed by ARS_kreis carrying all STP source columns; duplicate label cols
    # (Name) collapse to one.
    erwerb = pd.DataFrame({"ARS_kreis": ["03101", "03102"], "Name": ["A", "B"],
                           "ERWERBSTAT_KURZ_STP__11": [10.0, 20.0]})
    schul = pd.DataFrame({"ARS_kreis": ["03101", "03102"], "Name": ["A", "B"],
                          "SCHULABS_STP__21": [1.0, 2.0]})
    berufl = pd.DataFrame({"ARS_kreis": ["03101", "03102"], "Name": ["A", "B"],
                           "BERUFABS_AUSF_STP__2": [3.0, 4.0]})
    merged = mid.merge_kreis_control_tables([erwerb, schul, berufl])
    assert list(merged["ARS_kreis"]) == ["03101", "03102"]
    for col in ["ERWERBSTAT_KURZ_STP__11", "SCHULABS_STP__21", "BERUFABS_AUSF_STP__2"]:
        assert col in merged.columns
    assert list(merged.columns).count("Name") == 1


# ---------------------------------------------------------------------------
# synpp stage contract
# ---------------------------------------------------------------------------

def test_popsim_stage_exposes_synpp_contract():
    from braunschweig.popsim import stage
    assert callable(stage.configure)
    assert callable(stage.execute)


# ---------------------------------------------------------------------------
# load_control_cells: RegioStaR7 read from the parquet (graceful when absent)
# ---------------------------------------------------------------------------

def _write_cells_parquet(tmp_path, *, with_regiostar7: bool):
    """Tiny prepared-cells parquet: grid id first column + control base + ARS."""
    data = {
        "GITTER_ID_100m": [
            "CRS3035RES100mN2689000E4337000",
            "CRS3035RES100mN2689100E4337000",
        ],
        "POP": [3.0, 5.0],
        "POP_TOTAL_100m_adj": [3.0, 5.0],
        "RegionalSchlussel_ARS": ["031010000000", "031010000000"],
    }
    if with_regiostar7:
        data["RegioStaR7"] = [71, 72]
    path = tmp_path / "cells.parquet"
    pd.DataFrame(data).to_parquet(path)
    return path


def test_load_control_cells_reads_regiostar7_when_present(tmp_path):
    path = _write_cells_parquet(tmp_path, with_regiostar7=True)
    cells = mid.load_control_cells(path, ["POP"])
    assert "RegioStaR7" in cells.columns
    assert sorted(cells["RegioStaR7"].tolist()) == [71, 72]


def test_load_control_cells_without_regiostar7_logs_info(tmp_path, caplog):
    """Older cell parquets without RegioStaR7 must keep working (info-logged)."""
    import logging

    path = _write_cells_parquet(tmp_path, with_regiostar7=False)
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.mid"):
        cells = mid.load_control_cells(path, ["POP"])
    assert "RegioStaR7" not in cells.columns
    assert "POP" in cells.columns  # the load itself succeeded
    assert any("RegioStaR7" in record.getMessage() for record in caplog.records)


def test_load_mid_attributes_reads_needed_columns(tmp_path):
    # Phase 4A: RegioStaR7 is now part of MID_HOUSEHOLD_ATTR_COLS so the fixture
    # must include it; load_mid_attributes uses usecols=list(MID_HOUSEHOLD_ATTR_COLS).
    # hhgr_gr / alter_gr1 are the conditioning columns for grouped item-nonresponse
    # imputation (bugfix wave), so the fixtures must include them.
    # H_GR / H_GEW and P_GEW / kernwo joined the attribute usecols so the
    # member-completed frames can serve BOTH expansion and the PopulationSim seed.
    # anzpedrad / H_ANZPED joined MID_HOUSEHOLD_ATTR_COLS 2026-07-08 (bikes-incl-pedelec
    # construct + verified e-bike column), so the fixture must include them too.
    (tmp_path / "MiD2023_Haushalte.csv").write_text(
        "H_ID,oek_status,hheink_gr1,H_ANZAUTO,H_ANZRAD,anzpedrad,H_ANZPED,RegioStaR7,hhgr_gr,H_GR,H_GEW,H_MIETE,haustyp\n"
        "1,3,4,1,2,2,0,72,2,2,1.0,1,1\n",
        encoding="utf-8",
    )
    # P_BKAT (Berufskategorie) is required for map_socioprofessional_class (bug D4 fix).
    (tmp_path / "MiD2023_Personen.csv").write_text(
        "H_ID,P_ID,HP_ALTER,HP_SEX,P_TAET,P_FSCHEIN,P_FKARTE,P_BKAT,alter_gr1,P_GEW,kernwo\n1,1,40,1,1,1,3,1,5,1.0,1\n",
        encoding="utf-8",
    )
    households, persons = mid.load_mid_attributes(tmp_path)
    assert {"oek_status", "hheink_gr1", "H_ANZAUTO", "H_ANZRAD", "anzpedrad", "H_ANZPED",
            "RegioStaR7", "hhgr_gr", "H_GR", "H_GEW"} <= set(households.columns)
    assert {"P_TAET", "P_FSCHEIN", "P_FKARTE", "HP_ALTER", "HP_SEX", "P_BKAT", "alter_gr1",
            "P_GEW", "kernwo"} <= set(persons.columns)


# ---------------------------------------------------------------------------
# Change B: drop_invalid_households
# ---------------------------------------------------------------------------

def test_drop_invalid_households_removes_sentinel_and_null():
    """H_ID=0 and H_ID=null households (and their persons) are removed; valid
    households are untouched; returned counts are correct."""
    households = pd.DataFrame({
        "H_ID": [0, 1, None, 2],         # H_ID=0 + None are sentinels
        "H_GR": [3, 2, 1, 1],
    })
    persons = pd.DataFrame({
        "H_ID": [0, 0, 0, 1, 1, None, 2],
        "P_ID": [1, 2, 3, 1, 2, 1, 1],
    })

    hh_out, p_out, n_hh, n_p = mid.drop_invalid_households(households, persons)

    # Valid households 1 and 2 remain
    assert set(hh_out["H_ID"].tolist()) == {1, 2}, (
        f"Expected only H_ID {{1, 2}}, got {set(hh_out['H_ID'].tolist())}"
    )
    # Valid persons (H_ID=1 x2, H_ID=2 x1) remain
    assert set(hh_out["H_ID"].tolist()) == {1, 2}
    assert len(p_out) == 3
    assert set(p_out["H_ID"].tolist()) == {1, 2}

    # Counts: 2 bad households (H_ID=0 and None), 4 bad persons (3 from H_ID=0, 1 from None)
    assert n_hh == 2, f"Expected 2 bad households, got {n_hh}"
    assert n_p == 4, f"Expected 4 bad persons, got {n_p}"


def test_drop_invalid_households_noop_when_all_valid():
    """When all H_IDs are valid (non-zero, non-null) the frames are returned unchanged."""
    households = pd.DataFrame({"H_ID": [1, 2, 3], "H_GR": [1, 2, 3]})
    persons = pd.DataFrame({"H_ID": [1, 2, 3], "P_ID": [1, 1, 1]})

    hh_out, p_out, n_hh, n_p = mid.drop_invalid_households(households, persons)

    assert n_hh == 0 and n_p == 0
    assert len(hh_out) == 3 and len(p_out) == 3

