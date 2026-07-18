import numpy as np
import pandas as pd
import pytest
from braunschweig.synthesis import student_incommuters as si


class Ctx:
    """Minimal synpp-style context stub: config dict only (no stages needed for
    the guard tests)."""
    def __init__(self, cfg):
        self._cfg = cfg

    def config(self, key, default=si._SENTINEL):
        if key in self._cfg:
            return self._cfg[key]
        if default is si._SENTINEL:
            raise KeyError(key)
        return default


def test_disabled_when_cordon_off():
    frames = si.execute(Ctx({"cordon_enabled": False}))
    assert frames["persons"].empty


def test_skip_when_parent_off_and_flag_default():
    # education_gravity OFF + flag left at default (None) -> skip, empty frames.
    ctx = Ctx({"cordon_enabled": True, "education_gravity_enabled": False,
               "cordon_student_incommuters_enabled": None})
    frames = si.execute(ctx)
    assert frames["persons"].empty


def test_raise_when_flag_explicit_on_but_parent_off():
    ctx = Ctx({"cordon_enabled": True, "education_gravity_enabled": False,
               "cordon_student_incommuters_enabled": True})
    with pytest.raises(RuntimeError, match="education_gravity_enabled"):
        si.execute(ctx)


class FullCtx(Ctx):
    """Stage stub: config dict + a fixed stage-name -> return-value map."""
    def __init__(self, cfg, stages):
        super().__init__(cfg)
        self._stages = stages

    def stage(self, name):
        return self._stages[name]


def _full_ctx():
    import geopandas as gpd
    from shapely.geometry import Point, Polygon
    muni = gpd.GeoDataFrame(
        {"commune_id": ["031010000000"]},
        geometry=[Polygon([(0, 0), (1000, 0), (1000, 1000), (0, 1000)])],
        crs="EPSG:25832")
    facilities = gpd.GeoDataFrame(
        {"location_id": ["uni_loc_0"], "capacity": [1000.0]},
        geometry=[Point(500, 500)], crs="EPSG:25832")
    resident = pd.DataFrame({
        "person_id": [1, 2], "commune_id": ["", ""],
        "location_id": ["uni_loc_0", "uni_loc_0"], "geometry": [None, None]})
    hts_persons = pd.DataFrame({
        "person_id": [10], "studies": [True], "employed": [False]})
    hts_trips = pd.DataFrame({
        "person_id": [10, 10],
        "departure_time": [30000.0, 55000.0], "arrival_time": [32000.0, 57000.0],
        "preceding_purpose": ["home", "education"],
        "following_purpose": ["education", "home"]})
    residents = pd.DataFrame({"person_id": [0, 1, 2], "household_id": [0, 1, 2]})
    gates = gpd.GeoDataFrame(
        {"gate_id": ["gate_0000"]}, geometry=[Point(0, 0)], crs="EPSG:25832")
    stages = {
        "braunschweig.data.schools.university_facilities": facilities,
        "synthesis.population.spatial.primary.locations": resident,
        "data.spatial.municipalities": muni,
        "hts": (None, hts_persons, hts_trips),
        "braunschweig.synthesis.cordon_gates": {"gates": gates, "assignment": None},
        "synthesis.population.enriched": residents,
    }
    cfg = {"cordon_enabled": True, "education_gravity_enabled": True,
           "cordon_student_incommuters_enabled": None,
           "student_incommuter_age_band": [18, 29],
           "education_university_slope": -0.1415,
           "education_university_max_radius_km": 150.0,
           "sampling_rate": 0.5, "random_seed": 1,
           "cordon_network_source_buffer_m": 45000.0,
           "data_path": "eqasim-data/data"}
    return FullCtx(cfg, stages)


@pytest.mark.skipif(
    not __import__("os").path.exists("eqasim-data/data/braunschweig/12411-0018_de.csv"),
    reason="needs committed DESTATIS 12411-0018 table (run on a data-complete env)")
def test_injection_produces_education_incommuters():
    frames = si.execute(_full_ctx())
    # enrollment 1000*0.5 = 500, residents 2 -> 498 in-commuters.
    assert len(frames["persons"]) == 498
    assert (frames["activities"]["purpose"] == "education").any()
    assert frames["persons"]["person_id"].min() >= 3   # no collision with residents
