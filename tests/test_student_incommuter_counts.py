import geopandas as gpd
import pandas as pd
from shapely.geometry import Point, Polygon
from braunschweig.data.education import student_incommuter_counts as sic


def _municipalities():
    # Two ZGB communes as unit squares far apart (ARS-12).
    a = Polygon([(0, 0), (100, 0), (100, 100), (0, 100)])
    b = Polygon([(1000, 0), (1100, 0), (1100, 100), (1000, 100)])
    return gpd.GeoDataFrame(
        {"commune_id": ["031010000000", "031580000000"]},
        geometry=[a, b], crs="EPSG:25832")


def _facilities():
    # Two local facilities in commune 03101, one in 03158, one surrounding (outside).
    return gpd.GeoDataFrame(
        {"location_id": ["uni_loc_0", "uni_loc_1", "uni_loc_2", "uni_sur_TU"],
         "capacity": [6000.0, 4000.0, 3000.0, 20000.0]},
        geometry=[Point(10, 10), Point(20, 20), Point(1050, 50), Point(9e6, 9e6)],
        crs="EPSG:25832")


def test_facility_communes_maps_only_local():
    out = sic.facility_communes(_facilities(), _municipalities())
    m = dict(zip(out["location_id"], out["commune_ars5"]))
    assert m == {"uni_loc_0": "03101", "uni_loc_1": "03101", "uni_loc_2": "03158"}


def test_compute_counts_enrollment_minus_residents():
    resident = pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "commune_id": ["", "", "", ""],
        "location_id": ["uni_loc_0", "uni_loc_0", "uni_loc_2", "uni_sur_TU"],
        "geometry": [None, None, None, None],
    })
    # sampling_rate 0.5: enrollment 03101 = (6000+4000)*0.5 = 5000; residents = 2.
    out = sic.compute_incommuter_counts(_facilities(), _municipalities(), resident, 0.5)
    row = out.set_index("commune_ars5")
    assert row.loc["03101", "enrollment_scaled"] == 5000
    assert row.loc["03101", "residents"] == 2
    assert row.loc["03101", "in_commuters"] == 4998
    # 03158: enrollment 3000*0.5=1500, residents 1 -> 1499
    assert row.loc["03158", "in_commuters"] == 1499


def test_negative_count_floored_to_zero():
    resident = pd.DataFrame({
        "person_id": range(9000),
        "commune_id": [""] * 9000,
        "location_id": ["uni_loc_0"] * 9000,
        "geometry": [None] * 9000,
    })
    out = sic.compute_incommuter_counts(_facilities(), _municipalities(), resident, 0.5)
    assert out.set_index("commune_ars5").loc["03101", "in_commuters"] == 0
