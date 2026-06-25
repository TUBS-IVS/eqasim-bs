"""Tests for the REPLACE-based work-candidate builder in braunschweig.locations.work.

Covers:
- ON path: work candidates come ENTIRELY from gpkg buildings with potential_work > 0
  (no area*floors fallback, no ALKIS join).
- OFF path: legacy ALKIS area*floors candidate set (not tested here at unit level
  because it requires the synpp context; the OFF byte-identity is covered by the
  configure/execute integration).
"""
import geopandas as gpd
from shapely.geometry import Polygon
from braunschweig.locations.work import build_work_candidates_from_potentials


def _buildings():
    a = Polygon([(0, 0), (0, 2), (2, 2), (2, 0)])        # centroid (1, 1)
    b = Polygon([(10, 10), (10, 12), (12, 12), (12, 10)])
    return gpd.GeoDataFrame(
        {"building_id": [0, 1, 2],
         "potential_work": [42.0, 0.0, 7.0],             # building 1 has no work -> dropped
         "commune_id": ["03101000", "03101000", "03102000"]},
        geometry=[a, b, a], crs="EPSG:25832",
    )


def test_build_work_candidates_keeps_only_positive_potential():
    out = build_work_candidates_from_potentials(_buildings())
    assert len(out) == 2                                   # building 1 (0 potential) dropped
    assert set(out["employees"]) == {42.0, 7.0}
    assert (out["fake"] == False).all()
    assert out.geometry.geom_type.eq("Point").all()        # centroid points
    assert out["iris_id"].tolist() == out["commune_id"].tolist()


def test_build_work_candidates_employees_equals_potential_work():
    """employees column must be exactly the potential_work value, not area*floors."""
    out = build_work_candidates_from_potentials(_buildings())
    by_commune = out.groupby("commune_id")["employees"].sum()
    assert by_commune["03101000"] == 42.0
    assert by_commune["03102000"] == 7.0


def test_build_work_candidates_geometry_is_centroid():
    """Each row's geometry must be the centroid Point of the input footprint polygon."""
    a = Polygon([(0, 0), (0, 4), (4, 4), (4, 0)])         # centroid (2, 2)
    gdf = gpd.GeoDataFrame(
        {"building_id": [0], "potential_work": [10.0], "commune_id": ["03101000"]},
        geometry=[a], crs="EPSG:25832",
    )
    out = build_work_candidates_from_potentials(gdf)
    assert len(out) == 1
    pt = out.geometry.iloc[0]
    assert abs(pt.x - 2.0) < 1e-9
    assert abs(pt.y - 2.0) < 1e-9


def test_build_work_candidates_zero_potential_dropped():
    """Buildings with potential_work == 0 must be excluded."""
    a = Polygon([(0, 0), (0, 2), (2, 2), (2, 0)])
    gdf = gpd.GeoDataFrame(
        {"building_id": [0, 1], "potential_work": [0.0, 0.0], "commune_id": ["03101000", "03101000"]},
        geometry=[a, a], crs="EPSG:25832",
    )
    out = build_work_candidates_from_potentials(gdf)
    assert len(out) == 0


def test_build_work_candidates_output_schema():
    """Output GeoDataFrame must carry exactly: employees, fake, commune_id, iris_id, geometry."""
    out = build_work_candidates_from_potentials(_buildings())
    required = {"employees", "fake", "commune_id", "iris_id"}
    assert required.issubset(set(out.columns))
    assert out.crs is not None
