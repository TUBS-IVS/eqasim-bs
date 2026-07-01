# tests/test_commute_taz.py
import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, box
from braunschweig.calibration.commute_taz import build_taz_calibration_inputs


def _synthetic_taz():
    # Two Kreise (03101, 03151), two TAZ each; unit squares laid on a line.
    rows = [
        ("t1", "03101000", "03101", 72, box(0, 0, 1000, 1000)),
        ("t2", "03101000", "03101", 72, box(1000, 0, 2000, 1000)),
        ("t3", "03151001", "03151", 74, box(10000, 0, 11000, 1000)),
        ("t4", "03151001", "03151", 74, box(11000, 0, 12000, 1000)),
    ]
    return gpd.GeoDataFrame(
        pd.DataFrame(rows, columns=["taz_id", "commune_id", "kreis", "regiostar7", "geometry"]),
        geometry="geometry", crs="EPSG:25832",
    )


def test_build_taz_calibration_inputs_shapes_and_keys():
    df_taz = _synthetic_taz()
    df_homes = gpd.GeoDataFrame(
        {"household_id": [1, 2, 3], "commune_id": ["031010000000", "031010000000", "031510010000"]},
        geometry=[Point(500, 500), Point(1500, 500), Point(10500, 500)], crs="EPSG:25832",
    )
    df_population = pd.DataFrame({"commune_id": ["031010000000", "031510010000"], "weight": [100.0, 50.0]})
    df_employees = pd.DataFrame({"commune_id": ["031010000000", "031510010000"], "weight": [80.0, 40.0]})
    df_buildings = gpd.GeoDataFrame(
        {"commune_id": ["03101000", "03101000", "03151001"], "potential_work": [1.0, 3.0, 2.0]},
        geometry=[Point(500, 500), Point(1500, 500), Point(10500, 500)], crs="EPSG:25832",
    )
    df_municipalities = gpd.GeoDataFrame(
        {"commune_id": ["031010000000", "031510010000"]},
        geometry=[box(0, 0, 2000, 1000), box(10000, 0, 12000, 1000)], crs="EPSG:25832",
    )
    out = build_taz_calibration_inputs(
        df_taz, df_homes, df_population, df_employees, df_buildings, df_municipalities)
    assert out["zones"] == ["t1", "t2", "t3", "t4"]
    assert set(out["df_pop_taz"].columns) == {"origin_id", "population"}
    assert set(out["df_emp_taz"].columns) == {"destination_id", "employees"}
    assert set(out["df_dist_taz"].columns) == {"origin_id", "destination_id", "distance_km"}
    assert out["rs7_by_zone"] == {"t1": 72, "t2": 72, "t3": 74, "t4": 74}
    assert out["zone_to_kreis"] == {"t1": "03101", "t2": "03101", "t3": "03151", "t4": "03151"}
    # per-commune population is conserved across its TAZ
    assert abs(out["df_pop_taz"]["population"].sum() - 150.0) < 1e-6
    # employees conserved
    assert abs(out["df_emp_taz"]["employees"].sum() - 120.0) < 1e-6
    # home_taz assigns each home to a TAZ with coordinates
    assert set(out["home_taz"].columns) >= {"household_id", "taz_id", "x_m", "y_m"}
    assert len(out["home_taz"]) == 3


def test_assign_and_measure_taz_distances_and_rs7():
    from braunschweig.calibration.commute_taz import assign_and_measure_taz
    zones = ["t1", "t2", "t3", "t4"]
    # Deterministic OD: every home in t1 commutes to t2 (adjacent, ~1 km apart).
    od = np.zeros((4, 4)); od[0, 1] = 1.0; od[1, 1] = 1.0; od[2, 3] = 1.0; od[3, 3] = 1.0
    home_taz = pd.DataFrame({
        "household_id": [1, 2], "taz_id": ["t1", "t1"],
        "x_m": [500.0, 500.0], "y_m": [500.0, 500.0]})
    work_by_taz = {"t2": (np.array([[1500.0, 500.0]]), np.array([1.0]))}
    rs7_by_zone = {"t1": 72, "t2": 72, "t3": 74, "t4": 74}
    km_by_kreis, km_by_rs7, skip_rate = assign_and_measure_taz(
        od, zones, home_taz, work_by_taz, rs7_by_zone, random_seed=0)
    # home t1 (Kreis 03101 via zone_to... but here Kreis derives from taz->home commune) -> dist ~1 km
    assert skip_rate == 0.0
    assert 72 in km_by_rs7 and len(km_by_rs7[72]) == 2
    assert np.allclose(km_by_rs7[72], 1.0, atol=0.01)
