# tests/test_integerizer_quality.py
from braunschweig.analysis.integerizer_quality import cell_geometry as cg


def test_parse_cell_origin_handles_both_id_forms():
    assert cg.parse_cell_origin("CRS3035RES100mN3266700E4352500") == (4352500, 3266700)
    assert cg.parse_cell_origin("ZENSUS100m_CRS3035RES100mN3266700E4352500") == (4352500, 3266700)


def test_cells_geodataframe_builds_100m_squares_and_reprojects():
    gdf = cg.cells_geodataframe(["CRS3035RES100mN3266700E4352500"], target_epsg=3035)
    assert list(gdf.columns) == ["zensus100m", "geometry"]
    assert gdf.crs.to_epsg() == 3035
    # a 100m x 100m square -> area 10_000 m^2 in the metric native CRS
    assert abs(gdf.geometry.iloc[0].area - 10_000.0) < 1e-6
    minx, miny, maxx, maxy = gdf.geometry.iloc[0].bounds
    assert (minx, miny, maxx, maxy) == (4352500, 3266700, 4352600, 3266800)


def test_cells_geodataframe_reprojects_to_25832():
    gdf = cg.cells_geodataframe(["CRS3035RES100mN3266700E4352500"], target_epsg=25832)
    assert gdf.crs.to_epsg() == 25832


# ---------------------------------------------------------------------------
# Task 2: zone_feasibility
# ---------------------------------------------------------------------------
import os
from braunschweig.analysis.integerizer_quality import zone_feasibility as zf


def _write_log(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)


def test_classify_zones_marks_infeasible_as_smart_rounded(tmp_path):
    log = (
        "INFO - sequential_integerizing zone_id CRS3035RES100mN1E1 zone_name ZENSUS100m_CRS3035RES100mN1E1\n"
        "DEBUG - Integerizer status for backstopped ZENSUS1km_X_ZENSUS100m_CRS3035RES100mN1E1\n"
        "INFO - sequential_integerizing zone_id CRS3035RES100mN2E2 zone_name ZENSUS100m_CRS3035RES100mN2E2\n"
        "ERROR - populationsim.integerizing.wrappers - Integerizer failed for "
        "ZENSUS1km_X_ZENSUS100m_CRS3035RES100mN2E2 status INFEASIBLE. Returning smart-rounded original weights\n"
        "DEBUG - ZENSUS1km CRS3035RES1000mN0E0 converged False iter 1000\n"
    )
    _write_log(str(tmp_path / "batch_000" / "output" / "populationsim.log"), log)
    df = zf.classify_zones(str(tmp_path))
    by_id = dict(zip(df["zensus100m"], df["status"]))
    assert by_id["CRS3035RES100mN1E1"] == "optimal"
    assert by_id["CRS3035RES100mN2E2"] == "smart_rounded"
    assert df["batch"].unique().tolist() == ["batch_000"]


# ---------------------------------------------------------------------------
# Task 3: cell_error  — uses REAL CatalogControl objects (not a fake _Ctrl class)
# ---------------------------------------------------------------------------
import pandas as pd
import pytest
from braunschweig.analysis.integerizer_quality import cell_error as ce
from braunschweig.popsim import control_spec


# ---- HOUSEHOLD control with a real table-prefixed expression ----

def _make_household_control():
    """Real CatalogControl with the table-prefixed expression format used in production."""
    return control_spec.CatalogControl(
        name="has_car",
        geography="ZENSUS100m",
        seed_table=control_spec.SEED_TABLE_HOUSEHOLDS,
        importance=1,
        census_source=("x",),
        seed_expressions={"mid": "(households.H_ANZAUTO >= 1)"},
    )


def _make_person_control():
    """Real CatalogControl with a person-table-prefixed expression."""
    return control_spec.CatalogControl(
        name="adult_30plus",
        geography="ZENSUS100m",
        seed_table=control_spec.SEED_TABLE_PERSONS,
        importance=1,
        census_source=("y",),
        seed_expressions={"mid": "(persons.HP_ALTER >= 30)"},
    )


def _make_no_expr_control():
    """Real CatalogControl whose mid expression is None -> should increment n_skipped."""
    return control_spec.CatalogControl(
        name="no_mid",
        geography="ZENSUS100m",
        seed_table=control_spec.SEED_TABLE_HOUSEHOLDS,
        importance=1,
        census_source=("z",),
        seed_expressions={"mid": None},
    )


def test_realised_counts_household_control_groups_by_cell():
    """Real CatalogControl with prefixed expression must evaluate correctly per cell."""
    syn_hh = pd.DataFrame({
        "household_id": [1, 2, 3],
        "ZENSUS100m": ["cellA", "cellA", "cellB"],
        "H_ID": [10, 11, 12],
    })
    donor_hh = pd.DataFrame({"H_ID": [10, 11, 12], "H_ANZAUTO": [0, 2, 1]})
    donor_p = pd.DataFrame({"H_ID": [], "HP_ALTER": []})
    result, n_resolved, n_skipped, _resolved = ce.realised_counts(
        syn_hh, donor_hh, donor_p, [_make_household_control()])
    assert n_resolved == 1
    assert n_skipped == 0
    by = {r.zensus100m: r.realised for r in result[result.control == "has_car_ZENSUS100m"].itertuples()}
    assert by["cellA"] == 1   # only H_ID 11 (2 cars >= 1)
    assert by["cellB"] == 1   # H_ID 12 (1 car >= 1)


def test_realised_control_key_matches_target_convention(tmp_path):
    """REGRESSION (root cause of the -100%-everywhere bug): realised_counts must key
    each control the SAME way _load_targets does -- the geography-suffixed control field
    ``{name}_{geography}`` that control_totals_ZENSUS100m.csv carries. Otherwise the
    target<-realised merge in cell_error_table never matches and realised is filled 0."""
    syn_hh = pd.DataFrame({
        "household_id": [1, 2], "ZENSUS100m": ["cellA", "cellB"], "H_ID": [10, 12],
    })
    donor_hh = pd.DataFrame({"H_ID": [10, 12], "H_ANZAUTO": [2, 1]})
    donor_p = pd.DataFrame({"H_ID": [], "HP_ALTER": []})
    realised, _, _, _ = ce.realised_counts(syn_hh, donor_hh, donor_p, [_make_household_control()])
    # Target exactly as produced from a real control_totals_ZENSUS100m.csv (suffixed column).
    ct = tmp_path / "control_totals_ZENSUS100m.csv"
    ct.write_text("ZENSUS100m,has_car_ZENSUS100m\ncellA,1\ncellB,1\n", encoding="utf-8")
    target = ce._load_targets(ct)
    common = set(realised["control"]) & set(target["control"])
    assert common, (
        "realised control keys %s do not align with target keys %s"
        % (sorted(set(realised["control"])), sorted(set(target["control"])))
    )


def test_realised_counts_hh_type_control_uses_hh_type5_column():
    """HH-type (Typ_priv_HH_Familie) controls evaluate against the derived
    ``households.hh_type5`` column. cell_error_table attaches hh_type5 to the donor
    (via seed.derive_hh_type5); given that column, realised_counts must count these
    controls -- previously they were skipped (column absent) -> fabricated -100%."""
    syn_hh = pd.DataFrame({
        "household_id": [1, 2, 3], "ZENSUS100m": ["cellA", "cellA", "cellB"],
        "H_ID": [10, 11, 12],
    })
    donor_hh = pd.DataFrame({
        "H_ID": [10, 11, 12],
        "hh_type5": ["paar_ohne_kind", "alleinerziehend", "paar_ohne_kind"],
    })
    donor_p = pd.DataFrame({"H_ID": [], "HP_ALTER": []})
    ctrl = control_spec.CatalogControl(
        name="Paare_ohneKind_Typ_priv_HH_Familie_100m_Gitter", geography="ZENSUS100m",
        seed_table=control_spec.SEED_TABLE_HOUSEHOLDS, importance=1, census_source=("x",),
        seed_expressions={"mid": "(households.hh_type5 == 'paar_ohne_kind')"},
    )
    result, n_resolved, n_skipped, _resolved = ce.realised_counts(syn_hh, donor_hh, donor_p, [ctrl])
    assert n_resolved == 1 and n_skipped == 0
    key = "Paare_ohneKind_Typ_priv_HH_Familie_100m_Gitter_ZENSUS100m"
    by = {r.zensus100m: r.realised for r in result[result.control == key].itertuples()}
    assert by["cellA"] == 1   # H_ID 10 paar_ohne_kind (11 is alleinerziehend, excluded)
    assert by["cellB"] == 1   # H_ID 12 paar_ohne_kind


def test_realised_counts_person_control_joins_one_to_many():
    """Person control must join donor_persons one-to-many via H_ID and count per cell."""
    syn_hh = pd.DataFrame({
        "ZENSUS100m": ["cellA", "cellB"],
        "H_ID": [10, 11],
    })
    # H_ID 10 has 2 persons; H_ID 11 has 1 person
    donor_p = pd.DataFrame({
        "H_ID":     [10, 10, 11],
        "HP_ALTER": [35, 25, 40],
    })
    donor_hh = pd.DataFrame({"H_ID": [10, 11]})
    result, n_resolved, n_skipped, _resolved = ce.realised_counts(
        syn_hh, donor_hh, donor_p, [_make_person_control()])
    assert n_resolved == 1
    assert n_skipped == 0
    by = {r.zensus100m: r.realised for r in result[result.control == "adult_30plus_ZENSUS100m"].itertuples()}
    assert by["cellA"] == 1   # HP_ALTER 35 >= 30; 25 < 30
    assert by["cellB"] == 1   # HP_ALTER 40 >= 30


def test_realised_counts_household_donor_dedup_guards_against_fanout():
    """Duplicate H_ID rows in donor_households must NOT fan out the realised count.

    H_ID 11 appears twice in donor_households (simulating a data defect). Without
    the dedup guard the left-merge doubles the row and the realised count for cellA
    would be 2 instead of 1. The guard must emit a warning and use the unique set,
    keeping the count correct.
    """
    syn_hh = pd.DataFrame({
        "household_id": [1, 2, 3],
        "ZENSUS100m": ["cellA", "cellA", "cellB"],
        "H_ID": [10, 11, 12],
    })
    # H_ID 11 is duplicated with identical H_ANZAUTO — simulates a fan-out source
    donor_hh = pd.DataFrame({"H_ID": [10, 11, 11, 12], "H_ANZAUTO": [0, 2, 2, 1]})
    donor_p = pd.DataFrame({"H_ID": [], "HP_ALTER": []})
    result, n_resolved, n_skipped, _resolved = ce.realised_counts(
        syn_hh, donor_hh, donor_p, [_make_household_control()])
    by = {r.zensus100m: r.realised for r in result[result.control == "has_car_ZENSUS100m"].itertuples()}
    # cellA: only H_ID 11 qualifies (2 cars); duplicate must not inflate to 2
    assert by["cellA"] == 1, f"expected 1 but got {by.get('cellA')} -- duplicate H_ID fan-out not guarded"
    assert by["cellB"] == 1  # H_ID 12 (1 car) -- unaffected


def test_realised_counts_none_expr_increments_skipped():
    """A control whose expression_for('mid') is None must increment n_skipped, not n_resolved."""
    syn_hh = pd.DataFrame({"ZENSUS100m": ["cellA"], "H_ID": [10]})
    donor_hh = pd.DataFrame({"H_ID": [10], "H_ANZAUTO": [1]})
    donor_p = pd.DataFrame({"H_ID": [], "HP_ALTER": []})
    _, n_resolved, n_skipped, _resolved = ce.realised_counts(
        syn_hh, donor_hh, donor_p, [_make_no_expr_control()])
    assert n_resolved == 0
    assert n_skipped == 1


# ---------------------------------------------------------------------------
# Task 4: report
# ---------------------------------------------------------------------------
from braunschweig.analysis.integerizer_quality import report as rep


def test_build_outputs_splits_error_by_status():
    error_long = pd.DataFrame({
        "zensus100m": ["A", "A", "B", "B"],
        "control": ["c1", "c1", "c1", "c1"],
        "realised": [10, 0, 8, 0],
        "target":   [10, 0, 10, 0],
        "abs_error": [0, 0, 2, 0],
        "batch": ["batch_000"] * 4,
        "KREIS": ["03101", "03101", "03158", "03158"],
    })
    zones = pd.DataFrame({
        "zensus100m": ["A", "B"], "status": ["optimal", "smart_rounded"],
        "converged_false": [False, False], "batch": ["batch_000", "batch_000"],
    })
    out = rep.build_outputs(error_long, zones)
    ebc = out["error_by_control"].set_index(["control", "status"])
    assert ebc.loc[("c1", "optimal"), "mean_abs_error"] == 0.0
    assert ebc.loc[("c1", "smart_rounded"), "mean_abs_error"] == 1.0  # (2+0)/2
    cs = out["cell_summary"].set_index("zensus100m")
    assert cs.loc["B", "total_abs_error"] == 2
    assert bool(cs.loc["B", "is_smart_rounded"]) is True
    # error_by_kreis must be a real per-Kreis aggregation (not the batch table)
    ekk = out["error_by_kreis"]
    assert "kreis" in ekk.columns or ekk.index.name == "kreis" or "KREIS" in str(ekk.columns)
    # Both Kreise must appear
    kreis_values = set(ekk["kreis"]) if "kreis" in ekk.columns else set(ekk.index)
    assert "03101" in kreis_values
    assert "03158" in kreis_values
