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
