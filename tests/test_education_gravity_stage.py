import numpy as np
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point
from braunschweig.synthesis.locations.education_gravity import (
    age_to_level, assign_education_locations, slope_vector_for_level,
    bbs_share_vector,
)


def test_age_to_level_bands():
    assert age_to_level(4) == "kindergarten"
    assert age_to_level(6) == "grundschule"
    assert age_to_level(9) == "grundschule"
    assert age_to_level(10) == "sekundar_1"
    assert age_to_level(15) == "sekundar_1"
    assert age_to_level(16) == "upper_secondary"
    assert age_to_level(19) == "upper_secondary"
    assert age_to_level(25) == "university"


def _persons():
    ages = [4, 7, 12, 17, 30]
    return gpd.GeoDataFrame({
        "person_id": [1, 2, 3, 4, 5],
        "age": ages,
        "home_rs7": [72] * 5,
        "geometry": [Point(0.0, 0.0)] * 5,
    }, crs="EPSG:25832")


def _nds_schools():
    return gpd.GeoDataFrame({
        "school_id": ["g1", "s1", "o1", "b1"],
        "level": ["grundschule", "sekundar_1", "oberstufe", "bbs"],
        "capacity": [100.0, 100.0, 100.0, 100.0],
        "commune_id": ["03101000"] * 4,
        "geometry": [Point(500.0, 0.0), Point(800.0, 0.0),
                     Point(1000.0, 0.0), Point(1500.0, 0.0)],
    }, crs="EPSG:25832")


def _kita():
    """Two Kita facilities near the origin (0,0): person_id 1 (age 4) should
    be assigned to one of these via the capacity-gravity model."""
    return gpd.GeoDataFrame({
        "location_id": ["kita_a", "kita_b"],
        "capacity": [50.0, 50.0],
        "geometry": [Point(100.0, 0.0), Point(400.0, 0.0)],
    }, crs="EPSG:25832")


def _universities():
    """Two real university facilities: one near (2000 m), one far (60 000 m)."""
    return gpd.GeoDataFrame({
        "location_id": ["uni_a", "uni_b"],
        "capacity": [5000.0, 20000.0],
        "geometry": [Point(2000.0, 0.0), Point(60000.0, 0.0)],
    }, crs="EPSG:25832")


def test_assign_education_locations_covers_all_persons():
    cfg = {
        "slope_by_level": {
            "kindergarten": -0.5,
            "grundschule": -0.3, "sekundar_1": -0.15,
            "oberstufe": -0.08, "bbs": -0.05,
        },
        "slope_by_level_rs7": None,
        "max_radius_km_by_level": {
            "kindergarten": 8.0,
            "grundschule": 15.0, "sekundar_1": 30.0,
            "oberstufe": 60.0, "bbs": 100.0,
        },
        "university_slope": -0.08, "university_max_radius_km": 150.0,
        "max_iterations": 50, "tolerance": 1e-6,
        "bbs_share": 0.0,   # deterministically send all upper_secondary to oberstufe
    }
    out = assign_education_locations(
        _persons(), _nds_schools(), _universities(), _kita(), cfg,
        rng=np.random.RandomState(0),
    )
    assert list(out.columns) == ["person_id", "commune_id", "location_id", "geometry"]
    assert set(out["person_id"]) == {1, 2, 3, 4, 5}
    assert len(out) == 5
    by_pid = out.set_index("person_id")["location_id"].to_dict()
    assert by_pid[2] == "g1"
    assert by_pid[3] == "s1"
    assert by_pid[4] == "o1"
    # Person 1 (age 4) must be routed to a real Kita facility.
    assert by_pid[1] in {"kita_a", "kita_b"}
    # Person 5 (age 30) must be routed to a real university facility, not an OSM point.
    assert by_pid[5] in {"uni_a", "uni_b"}


def test_slope_vector_for_level_uses_rs7_override_then_falls_back():
    rs7 = pd.Series([72, 74, 99])   # 99 is an RS7 with no override
    by_level_rs7 = {"grundschule": {72: -0.5, 74: -0.2}}
    scalar_by_level = {"grundschule": -0.3}
    out = slope_vector_for_level("grundschule", rs7, by_level_rs7, scalar_by_level)
    assert list(out) == [-0.5, -0.2, -0.3]   # 99 falls back to the scalar


def test_slope_vector_for_level_scalar_when_no_rs7_dict():
    rs7 = pd.Series([72, 74])
    out = slope_vector_for_level("grundschule", rs7, None, {"grundschule": -0.3})
    assert list(out) == [-0.3, -0.3]


# ---------------------------------------------------------------------------
# Age-resolved BBS share (model improvement #14)
# ---------------------------------------------------------------------------

def test_bbs_share_vector_none_is_scalar_for_all():
    ages = pd.Series([16, 17, 18, 19])
    out = bbs_share_vector(ages, None, 0.681)
    assert list(out) == [0.681, 0.681, 0.681, 0.681]


def test_bbs_share_vector_uses_age_override_then_falls_back():
    ages = pd.Series([16, 17, 19, 18])
    by_age = {16: 0.2, 17: 0.5, 19: 0.9}   # 18 missing -> scalar fallback
    out = bbs_share_vector(ages, by_age, 0.681)
    assert list(out) == [0.2, 0.5, 0.9, 0.681]


def test_bbs_share_vector_accepts_string_age_keys():
    """synpp/YAML config may deliver age keys as strings; they must coerce."""
    ages = pd.Series([16, 19])
    by_age = {"16": 0.2, "19": 0.9}
    out = bbs_share_vector(ages, by_age, 0.681)
    assert list(out) == [0.2, 0.9]


def _upper_secondary_persons():
    """Eight age-16-19 pupils plus one university student, at one origin."""
    ages = [16, 16, 17, 17, 18, 18, 19, 19, 30]
    return gpd.GeoDataFrame({
        "person_id": list(range(1, len(ages) + 1)),
        "age": ages,
        "home_rs7": [72] * len(ages),
        "geometry": [Point(0.0, 0.0)] * len(ages),
    }, crs="EPSG:25832")


def _base_cfg():
    return {
        "slope_by_level": {
            "kindergarten": -0.5,
            "grundschule": -0.3, "sekundar_1": -0.15,
            "oberstufe": -0.08, "bbs": -0.05,
        },
        "slope_by_level_rs7": None,
        "max_radius_km_by_level": {
            "kindergarten": 8.0,
            "grundschule": 15.0, "sekundar_1": 30.0,
            "oberstufe": 60.0, "bbs": 100.0,
        },
        "university_slope": -0.08, "university_max_radius_km": 150.0,
        "max_iterations": 50, "tolerance": 1e-6,
        "bbs_share": 0.681,
    }


def _expected_levels_scalar(ages, share, seed):
    """Reproduce the legacy scalar draw for the given upper-secondary ages."""
    rng = np.random.RandomState(seed)
    draw = rng.random_sample(size=len(ages)) < share
    return np.where(draw, "bbs", "oberstufe")


def test_bbs_split_none_path_byte_identical_to_legacy_scalar():
    """With education_bbs_share_by_age absent, the per-pupil split must use the
    exact same RNG sequence and assignment as the legacy scalar code path."""
    persons = _upper_secondary_persons()
    cfg = _base_cfg()
    # No "bbs_share_by_age" key at all -> must behave like the scalar path.
    out = assign_education_locations(
        persons, _nds_schools(), _universities(), _kita(), cfg,
        rng=np.random.RandomState(7),
    )
    # Recompute the expected per-pupil level with the legacy scalar logic.
    us_ages = [a for a in persons["age"] if 16 <= a <= 19]
    expected = _expected_levels_scalar(us_ages, cfg["bbs_share"], seed=7)
    # oberstufe -> "o1", bbs -> "b1" in _nds_schools().
    expected_loc = ["b1" if lv == "bbs" else "o1" for lv in expected]
    by_pid = out.set_index("person_id")["location_id"].to_dict()
    got_loc = [by_pid[pid] for pid in range(1, len(us_ages) + 1)]
    assert got_loc == expected_loc


def test_bbs_split_none_explicit_key_byte_identical():
    """An explicit education_bbs_share_by_age=None is identical to omitting it."""
    persons = _upper_secondary_persons()
    cfg = _base_cfg()
    cfg["bbs_share_by_age"] = None
    out = assign_education_locations(
        persons, _nds_schools(), _universities(), _kita(), cfg,
        rng=np.random.RandomState(7),
    )
    us_ages = [a for a in persons["age"] if 16 <= a <= 19]
    expected = _expected_levels_scalar(us_ages, cfg["bbs_share"], seed=7)
    expected_loc = ["b1" if lv == "bbs" else "o1" for lv in expected]
    by_pid = out.set_index("person_id")["location_id"].to_dict()
    got_loc = [by_pid[pid] for pid in range(1, len(us_ages) + 1)]
    assert got_loc == expected_loc


def test_bbs_split_by_age_matches_per_age_probability():
    """With a provided age->share dict, the BBS probability per age must match
    the dict. Verified on a large synthetic single-age cohort with a fixed seed:
    age 16 -> ~0.2 BBS, age 19 -> ~0.9 BBS."""
    n = 4000
    ages = [16] * n + [19] * n
    persons = gpd.GeoDataFrame({
        "person_id": list(range(1, 2 * n + 1)),
        "age": ages,
        "home_rs7": [72] * (2 * n),
        "geometry": [Point(0.0, 0.0)] * (2 * n),
    }, crs="EPSG:25832")
    cfg = _base_cfg()
    cfg["bbs_share_by_age"] = {16: 0.2, 19: 0.9}
    out = assign_education_locations(
        persons, _nds_schools(), _universities(), _kita(), cfg,
        rng=np.random.RandomState(1),
    )
    by_pid = out.set_index("person_id")["location_id"].to_dict()
    age16_pids = range(1, n + 1)
    age19_pids = range(n + 1, 2 * n + 1)
    bbs16 = np.mean([by_pid[p] == "b1" for p in age16_pids])
    bbs19 = np.mean([by_pid[p] == "b1" for p in age19_pids])
    assert abs(bbs16 - 0.2) < 0.02
    assert abs(bbs19 - 0.9) < 0.02


def test_bbs_split_by_age_missing_age_falls_back_to_scalar():
    """Ages absent from the dict use the scalar education_bbs_share. With a
    scalar of 1.0 and only age 16 overridden to 0.0, all age-18 pupils go to
    BBS while all age-16 pupils go to oberstufe."""
    n = 1500
    ages = [16] * n + [18] * n
    persons = gpd.GeoDataFrame({
        "person_id": list(range(1, 2 * n + 1)),
        "age": ages,
        "home_rs7": [72] * (2 * n),
        "geometry": [Point(0.0, 0.0)] * (2 * n),
    }, crs="EPSG:25832")
    cfg = _base_cfg()
    cfg["bbs_share"] = 1.0                 # scalar fallback -> always BBS
    cfg["bbs_share_by_age"] = {16: 0.0}    # age 16 overridden -> never BBS
    out = assign_education_locations(
        persons, _nds_schools(), _universities(), _kita(), cfg,
        rng=np.random.RandomState(2),
    )
    by_pid = out.set_index("person_id")["location_id"].to_dict()
    assert all(by_pid[p] == "o1" for p in range(1, n + 1))           # age 16
    assert all(by_pid[p] == "b1" for p in range(n + 1, 2 * n + 1))   # age 18
