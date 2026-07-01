import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point, Polygon
from braunschweig.gravity.taz_margins import (
    assign_census_commune,
    build_dest_attraction_per_taz,
)


def _taz():
    # TAZ stage commune_id is 8-digit AGS (Phase-1 contract). T1,T2 in BS
    # (kreisfrei, AGS 03101000 == census ARS 031010000000). T3 is the mismatch
    # case: the RVB gpkg carries AGS 03153006 for a Goslar Gemeinde, but the
    # census keys it as ARS 031530016006 (different Gemeinde-suffix vintage).
    t1 = Polygon([(0, 0), (0, 10), (10, 10), (10, 0)])
    t2 = Polygon([(10, 0), (10, 10), (20, 10), (20, 0)])
    t3 = Polygon([(100, 0), (100, 10), (110, 10), (110, 0)])
    return gpd.GeoDataFrame(
        {"taz_id": ["T1", "T2", "T3"],
         "commune_id": ["03101000", "03101000", "03153006"],
         "kreis": ["03101", "03101", "03153"]},
        geometry=[t1, t2, t3], crs="EPSG:25832",
    )


def _municipalities():
    # data.spatial.municipalities contract: commune_id is the 12-digit ARS used
    # by the census / employees, with a polygon per Gemeinde. BS polygon spans
    # both T1 and T2; the Goslar polygon spans T3. The Goslar ARS deliberately
    # differs from the gpkg AGS so the geometry-based join (not a code crosswalk)
    # is what places T3 -- this is the e2e root-cause regression.
    bs = Polygon([(0, 0), (0, 10), (20, 10), (20, 0)])
    gos = Polygon([(100, 0), (100, 10), (110, 10), (110, 0)])
    return gpd.GeoDataFrame(
        {"commune_id": ["031010000000", "031530016006"]},
        geometry=[bs, gos], crs="EPSG:25832",
    )


def _buildings():
    # potential_work per building; commune_id is AGS-8 (building_potentials
    # contract) and is used ONLY for the Kreis prefix (identical in AGS-8/ARS-12).
    return gpd.GeoDataFrame(
        {"potential_work": [30.0, 10.0, 20.0],
         "commune_id": ["03101000", "03101000", "03153006"]},
        geometry=[Point(5, 5), Point(15, 5), Point(105, 5)], crs="EPSG:25832",
    )


def test_assign_census_commune_geometric_pip_and_nearest_fallback():
    # A point inside the BS polygon -> its census ARS; a point outside every
    # polygon -> nearest-commune fallback (logged, never silent).
    pts = gpd.GeoDataFrame(
        {"pid": [0, 1]},
        geometry=[Point(5, 5), Point(130, 5)], crs="EPSG:25832",
    )
    out, primary, fallback = assign_census_commune(pts, _municipalities(), id_column="pid")
    m = out.set_index("pid")["commune_ars"]
    assert m[0] == "031010000000"      # within BS polygon
    assert m[1] == "031530016006"      # outside both -> nearest (Goslar)
    assert primary == 1 and fallback == 1


def test_dest_margin_splits_and_preserves_commune_total():
    emp = pd.DataFrame({"commune_id": ["031010000000"], "weight": [100.0]})  # ARS-12
    df, primary, fallback = build_dest_attraction_per_taz(
        _buildings().iloc[:2], emp, _taz(), _municipalities())
    out = df.set_index("taz_id")["attraction"]
    assert abs(out["T1"] - 75.0) < 1e-9     # 30/40 * 100
    assert abs(out["T2"] - 25.0) < 1e-9     # 10/40 * 100
    assert abs(df.groupby("commune_id")["attraction"].sum().loc["031010000000"] - 100.0) < 1e-9


def test_dest_margin_assigns_census_commune_by_geometry_not_code():
    # The crux: the gpkg AGS 03153006 != census ARS 031530016006, yet T3 must
    # receive Goslar's 50 employees because T3 is placed in the Goslar commune
    # by LOCATION (municipalities polygon), with NO AGS->ARS crosswalk.
    emp = pd.DataFrame({"commune_id": ["031010000000", "031530016006"],
                        "weight": [100.0, 50.0]})
    df, _, _ = build_dest_attraction_per_taz(_buildings(), emp, _taz(), _municipalities())
    out = df.set_index("taz_id")
    assert out.loc["T3", "commune_id"] == "031530016006"   # census ARS, by geometry
    assert abs(out.loc["T3", "attraction"] - 50.0) < 1e-9
    # conservation per census commune
    by_commune = df.groupby("commune_id")["attraction"].sum()
    assert abs(by_commune["031010000000"] - 100.0) < 1e-9
    assert abs(by_commune["031530016006"] - 50.0) < 1e-9


def test_dest_margin_zero_potential_splits_uniformly():
    b = gpd.GeoDataFrame(
        {"potential_work": [0.0, 0.0], "commune_id": ["03101000", "03101000"]},
        geometry=[Point(5, 5), Point(15, 5)], crs="EPSG:25832")
    emp = pd.DataFrame({"commune_id": ["031010000000"], "weight": [100.0]})
    df, _, _ = build_dest_attraction_per_taz(b, emp, _taz(), _municipalities())
    out = df.set_index("taz_id")["attraction"]
    assert abs(out["T1"] - 50.0) < 1e-9 and abs(out["T2"] - 50.0) < 1e-9


def test_dest_margin_raises_when_employer_commune_missing_from_taz():
    # An employer commune with employees but no TAZ area -> its mass would be
    # silently lost, so the build must raise (M4, no silent fallback).
    emp = pd.DataFrame({"commune_id": ["031010000000", "035400000000"],
                        "weight": [100.0, 50.0]})
    with pytest.raises(ValueError, match="employees but no TAZ"):
        build_dest_attraction_per_taz(_buildings().iloc[:2], emp, _taz(), _municipalities())
