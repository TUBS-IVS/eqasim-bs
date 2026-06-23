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
