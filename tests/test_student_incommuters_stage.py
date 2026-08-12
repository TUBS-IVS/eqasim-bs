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
    # 2026-07-18 Task 5 review fix: the OFF/skip path must also register empty
    # vehicles/vehicle_types (byte-identical no-op for both
    # braunschweig.matsim.scenario.vehicles and .population).
    assert frames["vehicles"].empty
    assert list(frames["vehicles"].columns) == ["owner_id", "vehicle_id", "mode"]
    assert frames["vehicle_types"].empty


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


class _RecordingCtx(Ctx):
    """Context stub that records configure()'s stage() dependency declarations."""
    def __init__(self):
        super().__init__({})
        self.staged = []

    def config(self, key, default=si._SENTINEL):
        # configure() only needs SOME value per key; return the default when given,
        # else a harmless placeholder so no KeyError aborts the recording.
        if default is not si._SENTINEL:
            return default
        return {"cordon_enabled": True}.get(key, 0)

    def stage(self, descriptor, **kwargs):
        self.staged.append((descriptor, kwargs))


def test_configure_declares_hts_via_mid_donor():
    """Task 2: configure() must register the German MiD donor aliased to "hts"
    (not the legacy ENTD). Both SvB and student in-commuter stages use the German
    behaviour for trip timing. A bare context.stage("hts") is NOT a resolvable
    global alias and makes synpp raise a PipelineError at graph-build time. The
    mocked injection tests stub the "hts" stage directly, so only this
    configure-level check catches the regression."""
    ctx = _RecordingCtx()
    si.configure(ctx)
    assert ("braunschweig.data.hts.mid_donor", {"alias": "hts"}) in ctx.staged, ctx.staged
    # the old ENTD form must NOT be used
    assert ("data.hts.selected", {"alias": "hts"}) not in ctx.staged
    # the broken bare-alias form must NOT be used
    assert ("hts", {}) not in ctx.staged


def test_configure_declares_germany_population_keys():
    """Bug 2 (#222): configure() must declare germany.population_path and
    germany.population_source. _inject() calls external_workplaces._load_gemeinden,
    whose _vsi_path() reads both keys under synpp's ExecuteContext -- which raises
    ``PipelineError`` for a key that was not declared in configure(). Unlike the
    skip-gated real-data injection test below, this guard needs no data, so a
    data-less CI still catches a re-regression of the missing declaration."""
    requested: list[str] = []

    class _ConfigRecordingCtx(Ctx):
        def __init__(self):
            super().__init__({})

        def config(self, key, default=si._SENTINEL):
            requested.append(key)
            if default is not si._SENTINEL:
                return default
            return {"cordon_enabled": True}.get(key, 0)

        def stage(self, descriptor, **kwargs):
            pass

    si.configure(_ConfigRecordingCtx())
    assert "germany.population_path" in requested, requested
    assert "germany.population_source" in requested, requested


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
    # synthesis.population.spatial.primary.locations returns a TUPLE
    # (df_work, df_education) -- see synthesis/population/spatial/locations.py.
    # student_incommuters._inject() consumes the EDUCATION frame ([1]); the work
    # frame is never read there, so it is an empty same-schema placeholder.
    resident_education = pd.DataFrame({
        "person_id": [1, 2], "commune_id": ["", ""],
        "location_id": ["uni_loc_0", "uni_loc_0"], "geometry": [None, None]})
    resident_work = resident_education.iloc[0:0].copy()
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
        "synthesis.population.spatial.primary.locations": (
            resident_work, resident_education),
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
           "cordon_gate_speed_kmh": 30.0,
           "cordon_network_source_buffer_m": 45000.0,
           "braunschweig.political_prefix": ["03101"],
           "data_path": "eqasim-data/data",
           # external_workplaces._vsi_path (via _inject -> _load_gemeinden) reads
           # these under the execute context; mirror the configure() declarations.
           "germany.population_path": "germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip",
           "germany.population_source":
               "vg250-ew_12-31.utm32s.gpkg.ebenen/vg250-ew_ebenen_1231/DE_VG250.gpkg"}
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
    # 2026-07-18 Task 5 review fix: every agent owns a car_passenger vehicle,
    # and every car-mode agent additionally owns a car vehicle.
    assert (frames["vehicles"]["mode"] == "car_passenger").sum() == 498
    n_car_mode = (frames["persons"]["car_availability"] == "all").sum()
    assert (frames["vehicles"]["mode"] == "car").sum() == n_car_mode
    assert frames["vehicle_types"].empty  # legacy (non-German-fleet) vehicle builder


# ---------------------------------------------------------------------------
# Full-injection test with the two real-geodata calls mocked out (VG250 Gemeinden
# + DESTATIS 18-29 population), so it runs in ANY environment (unlike the
# skip-gated test above). Verifies the #140 Task 5 review vehicle fix end to
# end: _inject() must return a "vehicles" frame with one car_passenger vehicle
# per agent and one car vehicle per car-mode agent (Finding 2), reusing the SvB
# stage's legacy vehicle builders.
# ---------------------------------------------------------------------------

def _mocked_full_ctx():
    import geopandas as gpd
    from shapely.geometry import Point

    ctx = _full_ctx()
    # Origin Kreis "03402" lies outside the ZGB political prefix ["03101"], so it
    # is a valid external candidate. Only ars5/gem_ags/ewz/geometry are read by
    # student_incommuters._inject / incommuter_origin_homes.
    fake_gem = gpd.GeoDataFrame(
        {"ars5": ["03402"], "gem_ags": ["03402001"], "ewz": [5000.0]},
        geometry=[Point(50_000.0, 50_000.0)], crs="EPSG:25832")
    ctx._stages["_fake_gemeinden"] = fake_gem
    return ctx


def test_injection_with_mocked_geodata_returns_vehicles(monkeypatch):
    ctx = _mocked_full_ctx()
    fake_gem = ctx._stages["_fake_gemeinden"]

    monkeypatch.setattr(
        "braunschweig.data.external_workplaces._load_gemeinden",
        lambda context: fake_gem)
    monkeypatch.setattr(
        "braunschweig.data.education.student_origins.student_age_pop_by_kreis",
        lambda data_path, kreise, age_lower, age_upper: pd.Series({"03402": 100.0}))

    frames = si.execute(ctx)

    n = len(frames["persons"])
    assert n == 498  # same count-anchor arithmetic as the real-data test above
    assert frames["persons"]["person_id"].min() >= 3

    # Finding 2 (#140 Task 5 review): a car_passenger vehicle for every agent...
    vehicles = frames["vehicles"]
    assert (vehicles["mode"] == "car_passenger").sum() == n
    assert set(frames["persons"]["person_id"]) == set(
        vehicles.loc[vehicles["mode"] == "car_passenger", "owner_id"])
    # ... and a car vehicle for every car-mode agent, matching car_availability.
    n_car_mode = (frames["persons"]["car_availability"] == "all").sum()
    assert (vehicles["mode"] == "car").sum() == n_car_mode
    assert n_car_mode > 0  # sanity: the mocked mode reference actually yields car agents
    # No German-fleet HBEFA types are introduced by the legacy vehicle builder.
    assert frames["vehicle_types"].empty


def test_injection_attaches_orig_ars5_and_dest_commune(monkeypatch):
    """#140 Task 6 review fix: persons must carry orig_ars5/dest_commune so the
    downstream OD analysis (braunschweig.analysis.simwrapper.student_commuters)
    can actually run -- see student_incommuters._inject step 10a."""
    ctx = _mocked_full_ctx()
    fake_gem = ctx._stages["_fake_gemeinden"]
    monkeypatch.setattr(
        "braunschweig.data.external_workplaces._load_gemeinden",
        lambda context: fake_gem)
    monkeypatch.setattr(
        "braunschweig.data.education.student_origins.student_age_pop_by_kreis",
        lambda data_path, kreise, age_lower, age_upper: pd.Series({"03402": 100.0}))

    frames = si.execute(ctx)
    persons = frames["persons"]

    assert "orig_ars5" in persons.columns
    assert "dest_commune" in persons.columns
    assert not persons["orig_ars5"].isna().any()
    assert not persons["dest_commune"].isna().any()
    # Only one candidate origin Kreis ("03402", the mocked Gemeinde) and one
    # destination commune ("03101", the single local university facility in
    # the muni fixture) are configured, so every agent must resolve to that pair.
    assert set(persons["orig_ars5"]) == {"03402"}
    assert set(persons["dest_commune"]) == {"03101"}


def test_injection_seeds_distance_consistent_departure(monkeypatch):
    """#140 timing fix: the outbound home-departure seed must be derived from the
    synthetic home->campus distance at the gate speed (like the SvB _agent_times),
    NOT taken raw from the HTS donor trip. Proof: the implied outbound speed is the
    same constant (gate_speed / detour) for every agent, independent of the donor's
    own trip duration."""
    from braunschweig.constants import ROUTED_DETOUR_FACTOR
    from braunschweig.data.cordon.plans import straight_line_distance_km

    ctx = _mocked_full_ctx()
    fake_gem = ctx._stages["_fake_gemeinden"]
    monkeypatch.setattr(
        "braunschweig.data.external_workplaces._load_gemeinden",
        lambda context: fake_gem)
    monkeypatch.setattr(
        "braunschweig.data.education.student_origins.student_age_pop_by_kreis",
        lambda data_path, kreise, age_lower, age_upper: pd.Series({"03402": 100.0}))

    frames = si.execute(ctx)
    trips, loc = frames["trips"], frames["locations"]
    gate_speed_kmh = 30.0  # ctx default (cordon_gate_speed_kmh)

    # Per person: outbound leg (trip_index 0) duration vs the home->campus distance.
    home = loc[loc["activity_index"] == 0].set_index("person_id").geometry
    campus = loc[loc["activity_index"] == 1].set_index("person_id").geometry
    outbound = trips[trips["trip_index"] == 0].set_index("person_id")
    checked = 0
    for pid, leg in outbound.iterrows():
        dist_km = straight_line_distance_km(
            home[pid].x, home[pid].y, campus[pid].x, campus[pid].y)
        expected_s = dist_km * ROUTED_DETOUR_FACTOR / gate_speed_kmh * 3600.0
        actual_s = float(leg["arrival_time"] - leg["departure_time"])
        # depart_home is floored at 0, so only assert the equality where the seed
        # did not clip (arrive_mid - travel_s >= 0); clipping is a documented edge.
        if leg["departure_time"] > 0.0:
            assert abs(actual_s - expected_s) < 1.0, (pid, actual_s, expected_s)
            checked += 1
    assert checked > 0  # at least some agents exercised the distance-consistent seed


def test_injection_with_mocked_geodata_facilities_and_vehicles_wiring(monkeypatch):
    """Exercise the ACTUAL facilities.py / vehicles.py wiring (Findings 1+2) end
    to end against a real (mocked-geodata) student_incommuters output, using the
    same FullCtx stub style as tests/test_student_incommuter_merge.py."""
    import geopandas as gpd
    from shapely.geometry import Point

    import braunschweig.matsim.scenario.facilities as fac_mod
    import braunschweig.matsim.scenario.vehicles as veh_mod

    ctx = _mocked_full_ctx()
    fake_gem = ctx._stages["_fake_gemeinden"]
    monkeypatch.setattr(
        "braunschweig.data.external_workplaces._load_gemeinden",
        lambda context: fake_gem)
    monkeypatch.setattr(
        "braunschweig.data.education.student_origins.student_age_pop_by_kreis",
        lambda data_path, kreise, age_lower, age_upper: pd.Series({"03402": 100.0}))
    student_frames = si.execute(ctx)

    class FacilitiesCtx:
        def __init__(self, cfg, stages):
            self._cfg = cfg
            self._stages = stages

        def config(self, key, default=None):
            return self._cfg.get(key, default)

        def stage(self, name):
            return self._stages[name]

        def path(self):
            return "."

        def progress(self, total, label):
            import contextlib
            return contextlib.nullcontext(_Progress())

    class _Progress:
        def update(self):
            pass

    df_homes = gpd.GeoDataFrame({"household_id": []}, geometry=[], crs="EPSG:25832")
    df_primary = gpd.GeoDataFrame({"location_id": [], "is_work": []},
                                  geometry=[], crs="EPSG:25832")
    df_secondary = gpd.GeoDataFrame({"location_id": [], "offers_leisure": [],
                                     "offers_shop": [], "offers_other": []},
                                    geometry=[], crs="EPSG:25832")
    df_realised = pd.DataFrame({"location_id": []})

    fac_ctx = FacilitiesCtx(
        cfg={"cordon_enabled": True, "secondary_building_potentials": False},
        stages={
            "synthesis.population.spatial.home.locations": df_homes,
            "synthesis.population.spatial.primary.locations": (
                gpd.GeoDataFrame({"location_id": [], "is_work": []}, geometry=[],
                                 crs="EPSG:25832"),
                gpd.GeoDataFrame({"location_id": [], "is_work": []}, geometry=[],
                                 crs="EPSG:25832")),
            "synthesis.locations.secondary": df_secondary,
            "synthesis.population.spatial.secondary.locations": (df_realised,),
            "braunschweig.synthesis.incommuters": {
                # Full (non-empty-schema) locations frame even with zero rows, so
                # the existing SvB block's "activity_index" filter does not choke
                # on a columns-less stub -- mirrors the SvB stage's actual
                # non-empty-agent schema (only the never-exercised zero-SvB-agent
                # _empty_frames path lacks "activity_index", a separate pre-
                # existing gap out of this fix's scope).
                "locations": gpd.GeoDataFrame(
                    {"person_id": [], "activity_index": [], "location_id": []},
                    geometry=[], crs="EPSG:25832"),
                "persons": pd.DataFrame({"person_id": [], "household_id": []}),
            },
            "braunschweig.synthesis.student_incommuters": student_frames,
        },
    )

    written = {}
    monkeypatch.setattr(
        fac_mod.base, "write_facilities",
        lambda output_path, homes, primary, secondary, context: written.update(
            homes=homes, primary=primary) or "facilities.xml.gz")
    fac_mod.execute(fac_ctx)

    n = len(student_frames["persons"])
    edu_ids = {f"ic_edu_{int(pid)}" for pid in student_frames["persons"]["person_id"]}
    home_ids = {f"home_{int(hid)}" for hid in student_frames["persons"]["household_id"]}
    written_primary_ids = set(written["primary"]["location_id"].astype(str))
    written_home_ids = {
        f"home_{int(hid)}" for hid in written["homes"]["household_id"]}
    # Finding 1: every student education facility (ic_edu_<person_id>) and home
    # facility (home_<household_id>) is registered.
    assert edu_ids <= written_primary_ids
    assert home_ids <= written_home_ids
    edu_rows = written["primary"][written["primary"]["location_id"].astype(str).isin(edu_ids)]
    assert not edu_rows["is_work"].any()  # education, not work

    # Finding 2: vehicles.py merges the student stage's vehicles/vehicle_types.
    veh_ctx = FacilitiesCtx(
        cfg={"cordon_enabled": True},
        stages={
            "synthesis.vehicles.vehicles": (
                pd.DataFrame(columns=["type_id", "length", "width", "mode",
                                      "hbefa_cat", "hbefa_tech", "hbefa_size",
                                      "hbefa_emission"]),
                pd.DataFrame(columns=["owner_id", "vehicle_id", "mode"])),
            "braunschweig.synthesis.incommuters": {
                "vehicles": pd.DataFrame(columns=["owner_id", "vehicle_id", "mode"]),
                "vehicle_types": pd.DataFrame(columns=["type_id"]),
            },
            "braunschweig.synthesis.student_incommuters": student_frames,
        },
    )
    veh_written = {}
    monkeypatch.setattr(
        veh_mod.base, "write_vehicles",
        lambda output_path, types, vehicles, context: veh_written.update(
            vehicles=vehicles) or "vehicles.xml.gz")
    veh_mod.execute(veh_ctx)

    assert (veh_written["vehicles"]["mode"] == "car_passenger").sum() == n


def test_facilities_and_vehicles_off_path_registers_nothing_for_students(monkeypatch):
    """OFF/skip path (student_incommuters._empty_frames): facilities.py and
    vehicles.py must register/merge NOTHING for students -- a true no-op,
    keeping the resident + SvB-only output byte-identical."""
    import geopandas as gpd

    import braunschweig.matsim.scenario.facilities as fac_mod
    import braunschweig.matsim.scenario.vehicles as veh_mod

    empty_student_frames = si._empty_frames()

    class StubCtx:
        def __init__(self, cfg, stages):
            self._cfg = cfg
            self._stages = stages

        def config(self, key, default=None):
            return self._cfg.get(key, default)

        def stage(self, name):
            return self._stages[name]

        def path(self):
            return "."

    df_homes = gpd.GeoDataFrame({"household_id": []}, geometry=[], crs="EPSG:25832")
    df_primary_tuple = (
        gpd.GeoDataFrame({"location_id": [], "is_work": []}, geometry=[],
                         crs="EPSG:25832"),
        gpd.GeoDataFrame({"location_id": [], "is_work": []}, geometry=[],
                         crs="EPSG:25832"))
    df_secondary = gpd.GeoDataFrame({"location_id": [], "offers_leisure": [],
                                     "offers_shop": [], "offers_other": []},
                                    geometry=[], crs="EPSG:25832")
    df_realised = pd.DataFrame({"location_id": []})

    fac_ctx = StubCtx(
        cfg={"cordon_enabled": True, "secondary_building_potentials": False},
        stages={
            "synthesis.population.spatial.home.locations": df_homes,
            "synthesis.population.spatial.primary.locations": df_primary_tuple,
            "synthesis.locations.secondary": df_secondary,
            "synthesis.population.spatial.secondary.locations": (df_realised,),
            "braunschweig.synthesis.incommuters": {
                "locations": gpd.GeoDataFrame(
                    {"person_id": [], "activity_index": [], "location_id": []},
                    geometry=[], crs="EPSG:25832"),
                "persons": pd.DataFrame({"person_id": [], "household_id": []}),
            },
            "braunschweig.synthesis.student_incommuters": empty_student_frames,
        },
    )
    written = {}
    monkeypatch.setattr(
        fac_mod.base, "write_facilities",
        lambda output_path, homes, primary, secondary, context: written.update(
            homes=homes, primary=primary) or "facilities.xml.gz")
    fac_mod.execute(fac_ctx)
    assert len(written["homes"]) == 0
    assert len(written["primary"]) == 0

    veh_ctx = StubCtx(
        cfg={"cordon_enabled": True},
        stages={
            "synthesis.vehicles.vehicles": (
                pd.DataFrame(columns=["type_id"]),
                pd.DataFrame(columns=["owner_id", "vehicle_id", "mode"])),
            "braunschweig.synthesis.incommuters": {
                "vehicles": pd.DataFrame(columns=["owner_id", "vehicle_id", "mode"]),
                "vehicle_types": pd.DataFrame(columns=["type_id"]),
            },
            "braunschweig.synthesis.student_incommuters": empty_student_frames,
        },
    )
    veh_written = {}
    monkeypatch.setattr(
        veh_mod.base, "write_vehicles",
        lambda output_path, types, vehicles, context: veh_written.update(
            vehicles=vehicles) or "vehicles.xml.gz")
    veh_mod.execute(veh_ctx)
    assert len(veh_written["vehicles"]) == 0
