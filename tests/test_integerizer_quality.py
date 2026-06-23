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
# Task 3: cell_error
# ---------------------------------------------------------------------------
import pandas as pd
from braunschweig.analysis.integerizer_quality import cell_error as ce


class _Ctrl:
    name = "has_car"
    geography = "ZENSUS100m"
    family = "household"
    def expression_for(self, seed):
        return "H_ANZAUTO >= 1"


def test_realised_counts_household_control_groups_by_cell():
    syn_hh = pd.DataFrame({
        "household_id": [1, 2, 3],
        "ZENSUS100m": ["cellA", "cellA", "cellB"],
        "H_ID": [10, 11, 12],
    })
    syn_p = pd.DataFrame({"ZENSUS100m": [], "household_id": []})
    donor_hh = pd.DataFrame({"H_ID": [10, 11, 12], "H_ANZAUTO": [0, 2, 1]})
    donor_p = pd.DataFrame({"H_ID": [], "P_TAET": []})
    out = ce.realised_counts(syn_hh, syn_p, donor_hh, donor_p, [_Ctrl()])
    by = {(r.zensus100m): r.realised for r in out[out.control == "has_car"].itertuples()}
    assert by["cellA"] == 1  # only H_ID 11 (2 cars)
    assert by["cellB"] == 1  # H_ID 12 (1 car)


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
    syn_p = pd.DataFrame({"ZENSUS100m": [], "household_id": []})
    # H_ID 11 is duplicated with identical H_ANZAUTO — simulates a fan-out source
    donor_hh = pd.DataFrame({"H_ID": [10, 11, 11, 12], "H_ANZAUTO": [0, 2, 2, 1]})
    donor_p = pd.DataFrame({"H_ID": [], "P_TAET": []})
    out = ce.realised_counts(syn_hh, syn_p, donor_hh, donor_p, [_Ctrl()])
    by = {r.zensus100m: r.realised for r in out[out.control == "has_car"].itertuples()}
    # cellA: only H_ID 11 qualifies (2 cars); duplicate must not inflate to 2
    assert by["cellA"] == 1, f"expected 1 but got {by.get('cellA')} — duplicate H_ID fan-out not guarded"
    assert by["cellB"] == 1  # H_ID 12 (1 car) — unaffected


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
    })
    zones = pd.DataFrame({
        "zensus100m": ["A", "B"], "status": ["optimal", "smart_rounded"],
        "converged_false": [False, False], "batch": ["batch_000", "batch_000"],
    })
    out = build = rep.build_outputs(error_long, zones)
    ebc = out["error_by_control"].set_index(["control", "status"])
    assert ebc.loc[("c1", "optimal"), "mean_abs_error"] == 0.0
    assert ebc.loc[("c1", "smart_rounded"), "mean_abs_error"] == 1.0  # (2+0)/2
    cs = out["cell_summary"].set_index("zensus100m")
    assert cs.loc["B", "total_abs_error"] == 2
    assert bool(cs.loc["B", "is_smart_rounded"]) is True
