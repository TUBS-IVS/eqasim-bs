"""The student in-commuters from the origin-destination table T (issue #140, Phase 4, design B6).

What the ON path of ``braunschweig.synthesis.student_incommuters`` can get wrong: the balance (every site, every
origin Kreis and the total within one person of f_eff x sampling rate x T, no site without its outside rows), the
commute length T implies (a far origin must not be shortened to its gate point; the fallback counted), the effective
day factor per day-absence mode, the stage wiring (inputs per switch state, the refusal, the residents' placement left
unread) and the byte-identical OFF path against the output of the code before the switch.
"""
from __future__ import annotations

import logging
import os
import shutil

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from braunschweig.analysis.simwrapper import student_commuters
from braunschweig.constants import ROUTED_DETOUR_FACTOR
from braunschweig.data.education import student_flows_inputs as flows_inputs
from braunschweig.data.education import student_incommuter_counts as sic
from braunschweig.synthesis import student_incommuters as si
from tests.test_student_incommuters_stage import _mocked_full_ctx, _patch_mocked_geodata

DATA = os.path.join("eqasim-data", "data")
T_PATH = os.path.join(DATA, "braunschweig", "schools", "student_od_table.csv")
ART_ACADEMY = "Hochsch. f. Bildende Künste Braunschweig | 03101000"
GOLDEN = os.path.join("tests", "fixtures", "student_incommuters_off_golden")
TU = "TU Braunschweig | 03101000"


# ------------------------------------------------------------------------------------------------ the balance
@pytest.mark.parametrize("sampling_rate", [1.0, 0.1])
def test_balance_keeps_every_site_origin_and_total_within_one_person_on_t(sampling_rate):
    od = flows_inputs.load_od_table(T_PATH)
    seed = flows_inputs.outside_seed(od)
    f_eff = 0.32
    balance = sic.balance_incommuter_counts(seed, f_eff, sampling_rate, np.random.default_rng(7))

    assert list(balance.columns) == ["orig_ars5", "destination_id", "n"] and balance["n"].dtype == np.int64
    assert len(balance) == len(seed) and (balance["n"] >= 0).all()
    real = seed.assign(real=seed["seed"] * f_eff * sampling_rate)
    for key, column in (("orig_ars5", "home_ars5"), ("destination_id", "destination_id")):
        got = balance.groupby(key)["n"].sum()
        want = real.groupby(column)["real"].sum()
        assert (got.reindex(want.index) - want).abs().max() < 1.0, key
    assert abs(balance["n"].sum() - real["real"].sum()) < 1.0
    if sampling_rate == 1.0:
        # Every site of T gets its own outside rows, the art academy included (since Task 1b).
        sites_with_agents = set(balance.loc[balance["n"] > 0, "destination_id"])
        assert sites_with_agents == set(seed["destination_id"]) and ART_ACADEMY in sites_with_agents
    again = sic.balance_incommuter_counts(seed, f_eff, sampling_rate, np.random.default_rng(7))
    pd.testing.assert_frame_equal(balance, again)


def test_balance_refuses_a_factor_outside_the_unit_interval():
    seed = pd.DataFrame({"home_ars5": ["03402"], "destination_id": [TU], "seed": [10.0]})
    with pytest.raises(ValueError, match="f_eff"):
        sic.balance_incommuter_counts(seed, 0.0, 1.0, np.random.default_rng(0))


# ------------------------------------------------------------------------------------------------ commute length
_WEIGHTS = {"short": 1.0, "middle": 1.0, "long": 0.3, "max_commute_km": 100.0}


def _communes(*rows):
    return gpd.GeoDataFrame({"ars5": [r[0] for r in rows], "ewz": [r[1] for r in rows]},
                            geometry=[Point(r[2], 0.0) for r in rows], crs="EPSG:25832")


def test_a_far_origin_gets_the_length_of_its_communes_not_of_its_gate():
    """The populous commune beyond the maximum commute distance carries no weight; the drawn commune 90 km away
    gives the length, far beyond the gate point 10 km from the site."""
    gemeinden = _communes(("09999", 1_000.0, 90_000.0), ("09999", 1_000_000.0, 150_000.0))
    gate_km = 10.0
    lengths = [sic.commute_length_km("09999", (0.0, 0.0), gemeinden, _WEIGHTS, np.random.default_rng(s),
                                     od_distance_km=120.0) for s in range(5)]
    for routed_km, branch in lengths:
        assert branch == sic.COMMUTE_COMMUNE_CURVE
        assert routed_km == pytest.approx(90.0 * ROUTED_DETOUR_FACTOR)
        assert routed_km > gate_km * ROUTED_DETOUR_FACTOR


@pytest.mark.parametrize("gemeinden", [None, _communes(("09999", 1_000.0, 150_000.0)),
                                       _communes(("08888", 1_000.0, 50_000.0))])
def test_the_cell_distance_of_t_is_the_counted_fallback(gemeinden):
    routed_km, branch = sic.commute_length_km("09999", (0.0, 0.0), gemeinden, _WEIGHTS, np.random.default_rng(0),
                                              od_distance_km=120.0)
    assert branch == sic.COMMUTE_OD_CELL and routed_km == pytest.approx(120.0 * ROUTED_DETOUR_FACTOR)


# ------------------------------------------------------------------------------------------------ day factor
class _Ctx:
    def __init__(self, cfg):
        self._cfg = cfg

    def config(self, key):
        return self._cfg[key]


def _day_factor_cfg(day_absence, cells=True):
    return {"data_path": DATA, si.KEY_DAY_ABSENCE_ENABLED: day_absence, si.KEY_DAY_ABSENCE_STUDENT_CELLS: cells,
            si.KEY_LEVEL_TABLE_PATH: si.DEFAULT_LEVEL_TABLE_PATH,
            flows_inputs.KEY_HOME_AREA_KREISE: flows_inputs.default_home_area_kreise()}


def test_the_effective_day_factor_follows_the_away_shares_the_run_applies():
    srv_dir = os.path.join(DATA, "braunschweig", "srv")
    e = flows_inputs.education_leg_share(srv_dir)
    away = flows_inputs.away_share_by_area(srv_dir)
    f_off, mode_off = si.effective_day_factor_of_run(_Ctx(_day_factor_cfg(False)))
    f_cells, mode_cells = si.effective_day_factor_of_run(_Ctx(_day_factor_cfg(True)))
    assert (mode_off, mode_cells) == (flows_inputs.ABSENCE_MODE_OFF, flows_inputs.ABSENCE_MODE_CELLS)
    assert f_off == pytest.approx(e)
    assert e * (1 - max(away.values())) < f_cells < e * (1 - min(away.values()))
    with pytest.raises(ValueError, match=si.KEY_DAY_ABSENCE_STUDENT_CELLS):
        si.effective_day_factor_of_run(_Ctx(_day_factor_cfg(True, cells=False)))


# ------------------------------------------------------------------------------------------------ stage wiring
class _ConfigureContext:
    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.stages = []

    def config(self, key, default=None, **kwargs):
        return self.overrides.get(key, {"cordon_enabled": True}.get(key, default))

    def stage(self, name, **kwargs):
        self.stages.append(name)


def test_configure_declares_the_residents_placement_only_off_and_refuses_cells_off():
    on, off = _ConfigureContext(), _ConfigureContext({flows_inputs.KEY_FLOWS_ENABLED: False})
    si.configure(on)
    si.configure(off)
    placement = "synthesis.population.spatial.primary.locations"
    assert placement not in on.stages and placement in off.stages
    assert set(off.stages) - {placement} == set(on.stages)
    with pytest.raises(ValueError, match=si.KEY_DAY_ABSENCE_STUDENT_CELLS):
        si.configure(_ConfigureContext({si.KEY_DAY_ABSENCE_STUDENT_CELLS: False}))


def test_repeated_keys_match_their_owners():
    from braunschweig.data.schools import university_facilities
    from braunschweig.synthesis.day_absence import absence_stage

    assert (si.KEY_OD_TABLE_PATH, si.DEFAULT_OD_TABLE_PATH) == (
        university_facilities.KEY_OD_TABLE_PATH, university_facilities.DEFAULT_OD_TABLE_PATH)
    assert (si.KEY_DESTINATION_POINTS_PATH, si.DEFAULT_DESTINATION_POINTS_PATH) == (
        university_facilities.KEY_DESTINATION_POINTS_PATH, university_facilities.DEFAULT_DESTINATION_POINTS_PATH)
    assert (si.KEY_DAY_ABSENCE_ENABLED, si.DEFAULT_DAY_ABSENCE_ENABLED) == (
        absence_stage.KEY_ENABLED, absence_stage.DEFAULT_ENABLED)
    assert (si.KEY_DAY_ABSENCE_STUDENT_CELLS, si.DEFAULT_DAY_ABSENCE_STUDENT_CELLS) == (
        absence_stage.KEY_STUDENT_CELLS, absence_stage.DEFAULT_STUDENT_CELLS)


_SITE_POINT = (10_000.0, 500.0)
_TOY_T_HEADER = (
    "# Toy table T for the in-commuter stage test.\n"
    "# Weight rule: weight_rule=commune_curve, max_commute_km=100\n"
    "# Weights: short=1 middle=1 long=0.3\n")


def _flows_ctx(monkeypatch, tmp_path):
    """The mocked stage of ``tests/test_student_incommuters_stage.py`` with the flows on: the committed level, SrV and
    student mode tables, a toy T whose only outside origin is the mocked commune 70 km away (Kreis 03402), and the
    facilities of the ON path (one building of the TU). The residents' placement is removed: reading it fails."""
    for relative in (si.DEFAULT_LEVEL_TABLE_PATH,
                     os.path.join("braunschweig", "srv", flows_inputs.PARAMETERS_TABLE),
                     os.path.join("braunschweig", "srv", flows_inputs.PARAMETERS_BY_AREA_TABLE),
                     os.path.join("braunschweig", "mid", "mid2023_student_education_distance_mode.csv")):
        os.makedirs(tmp_path / os.path.dirname(relative), exist_ok=True)
        shutil.copyfile(os.path.join(DATA, relative), tmp_path / relative)
    t = pd.DataFrame([{
        "home_ars5": home, "home_in_region": home == "03101", "destination_id": TU, "institution": "TU Braunschweig",
        "site_kreis_ars5": "03101", "scope": "local", "enrollment_persons": 1200, "distance_km": km,
        "coeff_short": 0, "coeff_middle": 0, "coeff_long": 0, "origin_persons_scaled": heads,
        "upper_bound_persons_scaled": heads, "weight": 1, "students_persons": heads, "movers_persons": 0,
        "cell_kind": "local" if home == "03101" else "commuter"} for home, km, heads in
        (("03101", 0.0, 200.0), ("03402", 70.0, 1000.0))])
    os.makedirs(tmp_path / "braunschweig" / "schools", exist_ok=True)
    with open(tmp_path / si.DEFAULT_OD_TABLE_PATH, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(_TOY_T_HEADER)
        t.to_csv(handle, index=False)
    # The site point of T lies 9.5 km east of the buildings, so a length measured to a building differs from it.
    pd.DataFrame({"destination_id": [TU], "x": [_SITE_POINT[0]], "y": [_SITE_POINT[1]]}).to_csv(
        tmp_path / si.DEFAULT_DESTINATION_POINTS_PATH, index=False, encoding="utf-8")
    ctx = _mocked_full_ctx()
    _patch_mocked_geodata(monkeypatch, ctx)
    del ctx._stages["synthesis.population.spatial.primary.locations"]
    ctx._stages["braunschweig.data.schools.university_facilities"] = gpd.GeoDataFrame(
        {"location_id": ["uni_tu_0", "uni_tu_1"], "capacity": [900.0, 300.0], "destination_id": [TU, TU]},
        geometry=[Point(500, 500), Point(700, 500)], crs="EPSG:25832")
    ctx._cfg.update({"data_path": str(tmp_path), flows_inputs.KEY_FLOWS_ENABLED: True,
                     si.KEY_OD_TABLE_PATH: si.DEFAULT_OD_TABLE_PATH,
                     si.KEY_DESTINATION_POINTS_PATH: si.DEFAULT_DESTINATION_POINTS_PATH,
                     si.KEY_LEVEL_TABLE_PATH: si.DEFAULT_LEVEL_TABLE_PATH,
                     flows_inputs.KEY_HOME_AREA_KREISE: flows_inputs.default_home_area_kreise(),
                     si.KEY_DAY_ABSENCE_ENABLED: True, si.KEY_DAY_ABSENCE_STUDENT_CELLS: True,
                     si.KEY_MODE_BAND_MIN_PERSONS: si.DEFAULT_MODE_BAND_MIN_PERSONS,
                     si.KEY_COMMUTE_FALLBACK_WARN_SHARE: si.DEFAULT_COMMUTE_FALLBACK_WARN_SHARE})
    return ctx


def test_on_path_injects_the_balance_with_sites_buildings_and_the_length_t_implies(monkeypatch, tmp_path, caplog):
    ctx = _flows_ctx(monkeypatch, tmp_path)
    f_eff, _mode = si.effective_day_factor_of_run(ctx)
    with caplog.at_level(logging.INFO, logger=si.__name__):
        frames = si.execute(ctx)
    persons = frames["persons"]

    # Count: f_eff x sampling rate (0.5) x the 1000 outside heads, within one person; only the outside origin.
    assert abs(len(persons) - f_eff * 0.5 * 1000.0) < 1.0
    assert set(persons["orig_ars5"]) == {"03402"} and set(persons["dest_commune"]) == {"03101"}
    assert set(persons["destination_id"]) == {TU}
    # The education location is a building of the site, both drawn (capacity 900 : 300).
    assert set(persons["education_building_id"]) == {"uni_tu_0", "uni_tu_1"}
    locations = frames["locations"]
    campus = locations[locations["activity_index"] == 1].set_index("person_id").geometry
    assert set(zip(campus.x, campus.y)) == {(500.0, 500.0), (700.0, 500.0)}
    # The home is the gate (the commune lies outside the 45 km ring), the commute length the commune's 70 km.
    home = locations[locations["activity_index"] == 0].set_index("person_id").geometry
    home_km = np.hypot(home.x.to_numpy() - campus.x.to_numpy(), home.y.to_numpy() - campus.y.to_numpy()) / 1000.0
    assert (persons["commute_length_source"] == sic.COMMUTE_COMMUNE_CURVE).all()
    assert (persons["home_branch"] == si.HOME_BRANCH_GATE).all()
    # Measured from the only commune (50 km, 50 km) to T's site point, not to the building: 40 km east, 49.5 km north.
    site_km = np.hypot(50_000.0 - _SITE_POINT[0], 50_000.0 - _SITE_POINT[1]) / 1000.0
    np.testing.assert_allclose(persons["commute_length_km"].to_numpy(), site_km * ROUTED_DETOUR_FACTOR)
    assert (persons["commute_length_km"].to_numpy() > 10 * home_km * ROUTED_DETOUR_FACTOR).all()
    assert set(persons["car_availability"]) <= {"all", "none"}
    assert "gate fallback" in caplog.text and "03402" in caplog.text

    # The OD analysis runs on the ON frame.
    out = tmp_path / "simwrapper"
    student_commuters.write_outputs(persons, pd.Series(home_km), str(out))
    od = pd.read_csv(out / "student_commuter_od.csv", dtype={"from_ars5": str, "to_commune": str})
    assert od.to_dict("records") == [{"from_ars5": "03402", "to_commune": "03101", "value": len(persons)}]
    assert (out / "student_commute_distance.csv").is_file()


def test_a_commune_inside_the_ring_gives_the_in_ring_home_branch(monkeypatch, tmp_path):
    """The mocked commune moved 28 km from the site (inside the 45 km ring of the fixture): its agents take the
    population-weighted in-ring home point, not the gate (the gate case is the test above)."""
    ctx = _flows_ctx(monkeypatch, tmp_path)
    near = ctx._stages["_fake_gemeinden"].copy()
    near.geometry = [Point(20_000.0, 20_000.0)]
    ctx._stages["_fake_gemeinden"] = near
    _patch_mocked_geodata(monkeypatch, ctx)
    persons = si.execute(ctx)["persons"]
    assert len(persons) > 0
    assert (persons["home_branch"] == si.HOME_BRANCH_IN_RING).all()


def test_validate_requires_the_flows_tables_only_when_the_stage_injects(tmp_path):
    archive = tmp_path / "germany" / "vg250.zip"
    os.makedirs(archive.parent)
    archive.write_bytes(b"placeholder")
    values = {"data_path": str(tmp_path), "germany.population_path": "germany/vg250.zip", "cordon_enabled": True,
              "cordon_student_incommuters_enabled": None, "education_gravity_enabled": False,
              flows_inputs.KEY_FLOWS_ENABLED: True, si.KEY_OD_TABLE_PATH: si.DEFAULT_OD_TABLE_PATH,
              si.KEY_DESTINATION_POINTS_PATH: si.DEFAULT_DESTINATION_POINTS_PATH,
              si.KEY_LEVEL_TABLE_PATH: si.DEFAULT_LEVEL_TABLE_PATH}
    assert list(si.validate(_Ctx(values))["files"]) == ["vg250_ew"]   # the injection is skipped: no table needed
    with pytest.raises(RuntimeError, match=si.KEY_OD_TABLE_PATH):
        si.validate(_Ctx({**values, "education_gravity_enabled": True}))


def test_validate_names_a_missing_destination_points_file(tmp_path):
    """Every other table of the ON path exists; only the destination-points file is absent: the run stops at DAG time
    and the message names that file."""
    archive = tmp_path / "germany" / "vg250.zip"
    os.makedirs(archive.parent)
    archive.write_bytes(b"placeholder")
    for relative in (si.DEFAULT_OD_TABLE_PATH, si.DEFAULT_LEVEL_TABLE_PATH,
                     os.path.join("braunschweig", "srv", flows_inputs.PARAMETERS_TABLE),
                     os.path.join("braunschweig", "srv", flows_inputs.PARAMETERS_BY_AREA_TABLE),
                     os.path.join("braunschweig", "mid", "mid2023_student_education_distance_mode.csv")):
        os.makedirs(tmp_path / os.path.dirname(relative), exist_ok=True)
        shutil.copyfile(os.path.join(DATA, relative), tmp_path / relative)
    values = {"data_path": str(tmp_path), "germany.population_path": "germany/vg250.zip", "cordon_enabled": True,
              "cordon_student_incommuters_enabled": None, "education_gravity_enabled": True,
              flows_inputs.KEY_FLOWS_ENABLED: True, si.KEY_OD_TABLE_PATH: si.DEFAULT_OD_TABLE_PATH,
              si.KEY_DESTINATION_POINTS_PATH: si.DEFAULT_DESTINATION_POINTS_PATH,
              si.KEY_LEVEL_TABLE_PATH: si.DEFAULT_LEVEL_TABLE_PATH}
    with pytest.raises(RuntimeError, match=si.KEY_DESTINATION_POINTS_PATH):
        si.validate(_Ctx(values))


def test_off_path_output_is_frame_equal_to_the_pre_change_output(monkeypatch):
    """The OFF output on the mocked fixture of ``tests/test_student_incommuters_stage.py`` against the frames the
    code before the switch (commit 9f2441bd) wrote on the same fixture, stored in ``tests/fixtures``. Geometries are
    compared as WKT. ``int32`` is read as ``int64`` on both sides: ``np.where(..., 1, 0)`` gives the platform's
    default integer under numpy 1.x (int32 on Windows, where the golden was written; int64 on Linux)."""
    ctx = _mocked_full_ctx()
    _patch_mocked_geodata(monkeypatch, ctx)
    ctx._cfg[flows_inputs.KEY_FLOWS_ENABLED] = False
    frames = si.execute(ctx)

    def normalised(frame):
        frame = pd.DataFrame(frame).copy()
        if "geometry" in frame.columns:
            frame["geometry"] = frame["geometry"].map(lambda g: g if g is None or isinstance(g, str) else g.wkt)
        return frame.astype({c: "int64" for c, t in frame.dtypes.items() if str(t) == "int32"})

    for name in ("persons", "households", "trips", "activities", "locations", "vehicles"):
        expected = pd.read_pickle(os.path.join(GOLDEN, f"{name}.pkl.gz"), compression="gzip")
        pd.testing.assert_frame_equal(normalised(frames[name]), normalised(expected), obj=name)
    assert frames["vehicle_types"].empty
    assert frames["day_bound"] == {"n": 498, "n_redrawn": 0, "n_attempts_max": 0, "n_unfit": 0, "bound_s": 104400.0,
                                   "n_days_moved": 0, "n_still_late": 0}
