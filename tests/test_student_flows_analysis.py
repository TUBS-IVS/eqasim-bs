"""The acceptance table and the diagnostics of the student flows (issue #140, Phase 4, Task 12, design B7).

Pure functions on toy frames with hand-computed values, the FAIL rule, the pins of the literals this module repeats
against their owners, and the stage on a stub context (``configure``/``validate``/``execute``).
"""
from __future__ import annotations

import json
import math

import numpy as np
import pandas as pd
import pytest

from braunschweig.analysis.synthesis import student_flows as S

RATE = 0.5
E = 0.4
LOCAL = "S | 1"
OUTSIDE_INST = "Far | 9"


def _level():
    """Kreis A (code 03101) 100 young + 20 old, Kreis B (03102) 50 young + 10 old."""
    return pd.DataFrame({"code": ["03101", "03101", "03102", "03102"],
                         "age_range": ["18_29", "30plus", "18_29", "30plus"],
                         "students_persons": [100.0, 20.0, 50.0, 10.0]})


def _f():
    return pd.Series({"03101": 0.4 * 0.9, "03102": 0.4 * 0.8}, name="day_factor")


def _kwargs(**override):
    level = _level()
    f = _f()
    by_kreis = level.groupby("code")["students_persons"].sum()
    f_eff = float((by_kreis * f).sum() / by_kreis.sum())
    arguments = dict(
        destinations=pd.DataFrame({"destination_id": [LOCAL, OUTSIDE_INST], "scope": ["local", "surrounding"]}),
        enrollment=pd.Series({LOCAL: 200.0}),
        region_shares=pd.Series({LOCAL: 0.75, OUTSIDE_INST: 0.25}),
        outside_heads=pd.Series({LOCAL: 40.0}),
        outside_heads_by_origin=pd.Series({"03241": 40.0}),
        level=level, f_by_kreis=f, f_eff=f_eff, e=E, rate=RATE,
        residents_at=pd.Series({LOCAL: 20, OUTSIDE_INST: 7}),
        incommuters_at=pd.Series({LOCAL: 5}),
        incommuters_from=pd.Series({"03241": 5}),
        students_by_kreis=pd.Series({"03101": 55, "03102": 20}),
        students_of_day_by_kreis=pd.Series({"03101": 14, "03102": 6}),
        students_30plus_by_kreis=pd.Series({"03101": 3, "03102": 1}))
    arguments.update(override)
    return arguments


def _row(table, section, key):
    rows = table[(table["section"] == section) & (table["key"] == key)]
    assert len(rows) == 1, (section, key, table[["section", "key"]].to_string())
    return rows.iloc[0]


# ----------------------------------------------------------------------------------------- acceptance table
def test_acceptance_table_toy_case_hand_computed_balances():
    kwargs = _kwargs()
    f_eff = kwargs["f_eff"]
    # level totals: 03101 120, 03102 60; f = 0.36 and 0.32; f_eff = (120 * 0.36 + 60 * 0.32) / 180
    assert f_eff == pytest.approx((120 * 0.36 + 60 * 0.32) / 180)
    table = S.acceptance_table(**kwargs)
    assert list(table.columns) == ["section", "key", "model", "balance", "deviation", "deviation_share",
                                   "rule_applies"]
    level_total = 180.0
    residents = _row(table, "site", f"{LOCAL}|residents_of_day")
    assert residents["balance"] == pytest.approx(0.75 * level_total * f_eff * RATE)
    assert residents["model"] == 20
    assert residents["deviation"] == pytest.approx(20 - residents["balance"])
    assert residents["deviation_share"] == pytest.approx(residents["deviation"] / residents["balance"])
    incommuters = _row(table, "site", f"{LOCAL}|incommuters")
    assert incommuters["balance"] == pytest.approx(40.0 * f_eff * RATE)
    assert incommuters["model"] == 5
    total = _row(table, "site", f"{LOCAL}|total")
    assert total["model"] == 25
    assert total["balance"] == pytest.approx(200.0 * f_eff * RATE)
    # one origin Kreis outside the region and the total
    origin = _row(table, "incommuters_by_origin", "03241")
    assert origin["balance"] == pytest.approx(40.0 * f_eff * RATE) and origin["model"] == 5
    assert _row(table, "incommuters_by_origin", "total")["model"] == 5
    # the out-commuters: the residents of the day at a surrounding institution
    out = _row(table, "out_commuters", OUTSIDE_INST)
    assert out["balance"] == pytest.approx(0.25 * level_total * f_eff * RATE) and out["model"] == 7
    assert _row(table, "out_commuters", "total")["model"] == 7
    assert bool(residents["rule_applies"]) and bool(out["rule_applies"])


def test_population_rows_compare_with_the_level_table_not_with_t():
    table = S.acceptance_table(**_kwargs())
    population = _row(table, "students_population", "03101")
    assert population["balance"] == pytest.approx(120.0 * RATE)
    assert population["model"] == 55
    assert population["deviation"] == pytest.approx(55 - 60.0)
    assert _row(table, "students_population", "total")["balance"] == pytest.approx(180.0 * RATE)
    assert _row(table, "students_population", "total")["model"] == 75
    # the day: level x f(k) x rate
    day = _row(table, "students_of_day", "03102")
    assert day["balance"] == pytest.approx(60.0 * 0.32 * RATE)
    assert day["model"] == 6
    assert _row(table, "students_of_day", "total")["balance"] == pytest.approx(
        (120 * 0.36 + 60 * 0.32) * RATE)
    # the report-only row: level 30+ x e x rate, the rule does not apply
    report = _row(table, "students_30plus_report", "03101")
    assert report["balance"] == pytest.approx(20.0 * E * RATE) and report["model"] == 3
    assert not bool(report["rule_applies"])
    assert table.loc[table["section"] == "students_30plus_report", "rule_applies"].eq(False).all()
    assert table.loc[table["section"] != "students_30plus_report", "rule_applies"].eq(True).all()


def test_a_balance_of_zero_has_no_deviation_share():
    table = S.acceptance_table(**_kwargs(incommuters_from=pd.Series({"03241": 5, "03999": 2})))
    row = _row(table, "incommuters_by_origin", "03999")
    assert row["balance"] == 0 and row["model"] == 2 and math.isnan(row["deviation_share"])


def _verdict_table(rows):
    return pd.DataFrame([{"section": "site", "key": f"r{i}", "model": m, "balance": b, "deviation": m - b,
                          "deviation_share": (m - b) / b, "rule_applies": applies}
                         for i, (m, b, applies) in enumerate(rows)])


def test_verdict_fails_a_250_person_row_6_percent_off():
    assert S.acceptance_verdict(_verdict_table([(265.0, 250.0, True)]), 200, 0.05) == "FAIL"


def test_verdict_passes_a_250_person_row_within_the_bound_and_ignores_a_small_row_far_off():
    table = _verdict_table([(255.0, 250.0, True), (225.0, 150.0, True)])
    assert S.acceptance_verdict(table, 200, 0.05) == "PASS"


def test_a_150_person_row_50_percent_off_is_reported_only():
    table = _verdict_table([(225.0, 150.0, True)])
    assert S.acceptance_verdict(table, 200, 0.05) == "REPORT_ONLY"


def test_verdict_report_only_when_no_row_reaches_the_minimum_and_rows_without_the_rule_never_fail():
    assert S.acceptance_verdict(_verdict_table([(10.0, 150.0, True), (190.0, 199.0, True)]), 200, 0.05) \
        == "REPORT_ONLY"
    # a 500-person row far off whose rule does not apply neither fails nor counts as judged
    assert S.acceptance_verdict(_verdict_table([(900.0, 500.0, False)]), 200, 0.05) == "REPORT_ONLY"
    # the bound is exceeded strictly: exactly 5 % passes, the minimum is reached inclusively
    assert S.acceptance_verdict(_verdict_table([(262.5, 250.0, True)]), 250, 0.05) == "PASS"
    assert S.acceptance_verdict(_verdict_table([(200.0 * 1.05001, 200.0, True)]), 200, 0.05) == "FAIL"


def test_verdict_rejects_bad_settings():
    with pytest.raises(ValueError, match="max_deviation_share"):
        S.acceptance_verdict(_verdict_table([(1.0, 1.0, True)]), 200, -0.1)
    with pytest.raises(ValueError, match="min_persons"):
        S.acceptance_verdict(_verdict_table([(1.0, 1.0, True)]), -1, 0.05)


# ----------------------------------------------------------------------------------------- distance bands
def test_band_shares_are_left_inclusive_and_the_emd_is_hand_computed():
    km = np.array([1.0, 5.0, 30.0, 120.0])      # 5.0 km belongs to 5_10
    assert S.band_shares(km).tolist() == [0.25, 0.25, 0.0, 0.25, 0.0, 0.25]
    labels = S._student_mode_reference.BAND_LABELS
    same = {"group": "all", "reference": "srv", "n_reference": 10, "shares": [0.25, 0.25, 0.0, 0.25, 0.0, 0.25],
            "emd_noise_95": 0.1}
    all_in_first_band = {**same, "shares": [1.0, 0, 0, 0, 0, 0], "emd_noise_95": 0.3}
    table = S.band_comparison({"all": km}, [same, all_in_first_band], [1.0], 30)
    assert table["emd"].tolist() == pytest.approx([0.0, 2.25 / 5])   # |cdf diff| = .75 .5 .5 .25 .25
    assert table["emd_within_noise"].tolist() == [True, False]
    assert table["n_reference_low"].tolist() == [True, True]          # 10 respondents < 30
    assert table[f"model_share_{labels[0]}"].tolist() == [0.25, 0.25]


def test_a_group_without_model_persons_or_without_reference_shares_has_no_emd():
    nan_row = {"group": "03103", "reference": "srv", "n_reference": 0, "shares": [math.nan] * 6,
               "emd_noise_95": math.nan}
    table = S.band_comparison({"03103": np.array([2.0]), "03102": np.array([])},
                              [nan_row, {**nan_row, "group": "03102", "shares": [1, 0, 0, 0, 0, 0],
                                         "n_reference": 30}], [1.3], 30)
    assert table["emd"].isna().all()
    assert table["n_reference_low"].tolist() == [True, False]          # the minimum itself is enough


def test_srv_rows_below_the_respondent_minimum_are_reported_only_and_the_threshold_is_configurable():
    bands = pd.DataFrame({"group": ["all", "03101", "03157", "03102", "all"],
                          "reference": ["srv", "srv", "srv", "srv", "mid"],
                          "n_reference_low": [False, False, True, False, False],
                          "emd": [0.1, 0.2, 0.3, math.nan, 0.4]})
    # 03102 has enough respondents but no EMD (no model persons): no comparison, so reported only
    assert S.usable_distance_rows(bands) == {"usable": ["03101", "all"], "reported_only": ["03102", "03157"],
                                             "total_row_code": "03ZGB"}
    # on the committed table: 03101 (68 respondents) and the total 03ZGB (111) reach 30, the other Kreise do not
    srv = pd.read_csv(REAL_DATA / "braunschweig" / "srv" / S.SRV_DISTANCE_TABLE, comment="#", dtype={"code": str})
    model_km = {"03101": np.array([1.0, 6.0, 12.0]), "all": np.array([2.0, 30.0])}
    committed = S.band_comparison(model_km, S.srv_band_references(srv), [1.3], S.DEFAULT_MIN_SRV_RESPONDENTS)
    assert S.usable_distance_rows(committed)["usable"] == ["03101", "all"]
    recorder = _ConfigureRecorder()
    S.configure(recorder)
    assert recorder.keys[S.KEY_MIN_SRV_RESPONDENTS] == 30


def test_students_outside_the_level_kreise_are_counted():
    frame = pd.DataFrame({"home_kreis": ["03101", "03999", "03999", "03101"], "age": [20, 20, 17, 30],
                          "is_student": [True, True, True, False]})
    assert S.students_outside_level(frame, ["03101", "03102"]) == 1   # the 17-year-old and the non-student stay out


def test_incommuter_ids_must_be_unique_and_disjoint_from_the_residents():
    residents = pd.Series([1, 2, 3])
    S.check_incommuter_ids(pd.DataFrame({"person_id": [10, 11]}), residents)
    S.check_incommuter_ids(pd.DataFrame(), residents)       # the injection is off
    with pytest.raises(ValueError, match="repeat"):
        S.check_incommuter_ids(pd.DataFrame({"person_id": [10, 10]}), residents)
    with pytest.raises(ValueError, match="residents' ids"):
        S.check_incommuter_ids(pd.DataFrame({"person_id": [3, 11]}), residents)
    with pytest.raises(ValueError, match="no person_id"):
        S.check_incommuter_ids(pd.DataFrame({"destination_id": ["x"]}), residents)


# ----------------------------------------------------------------------------------------- pins
def test_literals_repeated_from_their_owners_match_them():
    from braunschweig.calibration import srv_student_reference as R
    from braunschweig.data.education import student_incommuter_counts as counts
    from braunschweig.synthesis import student_incommuters as incommuters
    from braunschweig.synthesis.day_absence import absence
    from braunschweig.synthesis.locations import education_gravity as gravity

    assert S.KEY_OD_TABLE_PATH == incommuters.KEY_OD_TABLE_PATH == gravity.KEY_OD_TABLE_PATH
    assert S.DEFAULT_OD_TABLE_PATH == incommuters.DEFAULT_OD_TABLE_PATH == gravity.DEFAULT_OD_TABLE_PATH
    assert S.KEY_LEVEL_TABLE_PATH == incommuters.KEY_LEVEL_TABLE_PATH
    assert S.DEFAULT_LEVEL_TABLE_PATH == incommuters.DEFAULT_LEVEL_TABLE_PATH
    assert S.KEY_DAY_ABSENCE_ENABLED == gravity.KEY_DAY_ABSENCE_ENABLED
    assert S.KEY_DAY_ABSENCE_STUDENT_CELLS == gravity.KEY_DAY_ABSENCE_STUDENT_CELLS
    assert (S.ABSENT_PATH_PREFIX, S.PATH_LEGACY_DECAY) == (gravity.ABSENT_PATH_PREFIX, gravity.PATH_LEGACY_DECAY)
    assert S.STATE_PRESENT == absence.STATE_PRESENT
    assert S.COMMUTE_OD_CELL == counts.COMMUTE_OD_CELL
    assert S.HOME_BRANCH_GATE == incommuters.HOME_BRANCH_GATE
    assert S.ABSENCE_STAGE == gravity.ABSENCE_STAGE and S.TRIPS_STAGE == gravity.TRIPS_STAGE
    assert S.HOME_ZONES_STAGE == gravity.HOME_ZONES_STAGE
    assert (S.BRAUNSCHWEIG_KREIS, S.REGION_HANNOVER_ARS5, S.LANDKREIS_HILDESHEIM_ARS5) == (
        R.BRAUNSCHWEIG_KREIS, R.REGION_HANNOVER_ARS5, R.LANDKREIS_HILDESHEIM_ARS5)
    assert S.HOME_GROUPS == R.HOME_GROUPS
    assert S.SRV_DISTANCE_TABLE == R.EDUCATION_DISTANCE_TABLE and S.SRV_DESTINATION_TABLE == R.DESTINATION_TABLE
    assert S.SRV_LEG_BY_STATUS_TABLE == R.EDUCATION_LEG_BY_STATUS_TABLE
    # the band edges of the SrV table's builder are the leaf's edges
    from braunschweig.calibration.student_behaviour_labels import STUDENT_DISTANCE_EDGES_KM, STUDENT_DISTANCE_LABELS
    assert tuple(S._student_mode_reference.BAND_EDGES_KM) == tuple(STUDENT_DISTANCE_EDGES_KM)
    assert tuple(S._student_mode_reference.BAND_LABELS) == tuple(STUDENT_DISTANCE_LABELS)


@pytest.mark.parametrize("home", ["03101", "03102", "03241"])
@pytest.mark.parametrize("destination", ["03101", "03102", "03103", "03999", "03151", "03241", "03254"])
def test_destination_groups_follow_the_owner_of_the_srv_table(home, destination):
    from braunschweig.calibration import srv_student_reference as R
    sites = frozenset({"03101", "03102", "03103", "03153", "03158"})
    assert S.destination_group(destination, home, sites) == R._destination_group(destination, home, sites)
    for group in S.HOME_GROUPS:
        assert S.destination_vocabulary(sites, group) == R.destination_vocabulary(sites, group)


# ----------------------------------------------------------------------------------------- the stage
import pathlib  # noqa: E402
import shutil  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[1]
REAL_DATA = REPO / "eqasim-data" / "data"
RATE_STAGE = 0.001   # keeps every balance far below the minimum: the verdict is REPORT_ONLY on this toy


class _ConfigureRecorder:
    def __init__(self, config=None):
        self.stages, self.keys, self._config = [], {}, dict(config or {})

    def stage(self, name, alias=None, **kwargs):
        self.stages.append(alias or name)

    def config(self, key, *args, **kwargs):
        self.keys[key] = self._config.get(key, args[0] if args else None)
        return self.keys[key]


class _ExecuteContext:
    """synpp ExecuteContext stand-in: ``config`` takes the key alone, as real synpp does."""

    def __init__(self, stages, config, path):
        self._stages, self._config, self._path = stages, config, path
        self.stages_read, self.config_read = set(), set()

    def stage(self, name):
        self.stages_read.add(name)
        return self._stages[name]

    def config(self, key):
        self.config_read.add(key)
        return self._config[key]

    def path(self, name=None):
        return self._path


def _copy_real_tables(data_path):
    for relative in (*[f"braunschweig/srv/{name}" for name in S.SRV_TABLES], S.DEFAULT_OD_TABLE_PATH,
                     S.DEFAULT_LEVEL_TABLE_PATH, "braunschweig/schools/student_balance_by_site.csv",
                     "braunschweig/schools/student_balance_out_commuters.csv",
                     "braunschweig/mid/mid2023_student_education_distance_mode.csv"):
        target = data_path / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy(REAL_DATA / relative, target)


def _world():
    import geopandas as gpd
    from shapely.geometry import Point

    od = pd.read_csv(REAL_DATA / "braunschweig" / "schools" / "student_od_table.csv", comment="#", dtype=str)
    local = od[(od["scope"] == "local") & (od["site_kreis_ars5"] == "03101")]["destination_id"].iloc[0]
    far = od[od["scope"] == "surrounding"]["destination_id"].iloc[0]
    origin = od[(od["home_in_region"] == "False") & (od["destination_id"] == local)]["home_ars5"].iloc[0]
    crs = "EPSG:25832"

    def point(x):
        return Point(600000.0 + x * 1000.0, 5800000.0)

    persons = pd.DataFrame({
        "person_id": [1, 2, 3, 4, 5, 6, 7, 8], "household_id": [1, 1, 2, 2, 1, 2, 1, 1],
        "age": [25, 22, 24, 26, 40, 35, 17, 21], "P_TAET": [10, 10, 10, 10, 8, 10, 9, 10]})
    zones = pd.DataFrame({"household_id": [1, 2], "departement_id": ["03101", "03102"],
                          "commune_id": ["031010000000", "031020000000"]})
    homes = gpd.GeoDataFrame({"household_id": [1, 2], "geometry": [point(0), point(20)]}, crs=crs)
    paths = ["plan_length", "fallback_decay", "home_distance", "absent_plan_length", None, "plan_length", None]
    gravity = gpd.GeoDataFrame({
        "person_id": [1, 2, 3, 4, 5, 6, 7], "commune_id": "",
        "location_id": ["b1", "b1", "b2", "b1", "s1", "b1", "s2"],
        "geometry": [point(2), point(2), point(40), point(2), point(1), point(2), point(1)],
        "routed_level": ["university"] * 4 + ["adult_education", "university", "sekundar_1"],
        "destination_id": [local, local, far, local, None, local, None], "assignment_path": paths}, crs=crs)
    facilities = gpd.GeoDataFrame({
        "location_id": ["b1", "b2"], "capacity": [100.0, 100.0], "destination_id": [local, far],
        "scope": ["local", "surrounding"], "site_ags8": ["03101000", "03241001"],
        "geometry": [point(2), point(40)]}, crs=crs)
    absence = pd.DataFrame({"person_id": range(1, 9), "household_id": [1, 1, 2, 2, 1, 2, 1, 1],
                            "day_absence_state": ["present", "present", "present", "absent_individual",
                                                  "present", "present", "present", "present"]})
    trips = pd.DataFrame({"person_id": [1, 3], "preceding_purpose": ["home", "education"],
                          "following_purpose": ["education", "home"], "euclidean_distance": [5000.0, 20000.0]})
    incommuters = pd.DataFrame({
        "person_id": [10_000_001, 10_000_002], "orig_ars5": [origin, origin],
        "destination_id": [local, local], "home_branch": ["in_ring", "gate"],
        "commute_length_km": [30.0, 40.0], "commute_length_source": ["commune_curve", "od_cell"]})
    return dict(local=local, far=far, origin=origin, stages={
        S.ENRICHED_STAGE: persons, S.HOME_ZONES_STAGE: zones, S.HOME_LOCATIONS_STAGE: homes, S.TRIPS_STAGE: trips,
        S.GRAVITY_STAGE: gravity, S.FACILITIES_STAGE: facilities, S.INCOMMUTERS_STAGE: {"persons": incommuters},
        S.ABSENCE_STAGE: {"absence": absence, "diagnostics": {"enabled": True, "n_adults": 6,
                                                              "n_adults_unreadable_p_taet": 0}}})


def _config(tmp_path, data_path, **override):
    from braunschweig.data.education import student_flows_inputs as inputs
    return {**{"data_path": str(data_path), "output_path": str(tmp_path / "out"), "sampling_rate": RATE_STAGE,
            "random_seed": 7, S.KEY_OD_TABLE_PATH: S.DEFAULT_OD_TABLE_PATH,
            S.KEY_LEVEL_TABLE_PATH: S.DEFAULT_LEVEL_TABLE_PATH,
            inputs.KEY_HOME_AREA_KREISE: inputs.default_home_area_kreise(), S.KEY_MIN_PERSONS: S.DEFAULT_MIN_PERSONS,
            S.KEY_MAX_DEVIATION_SHARE: S.DEFAULT_MAX_DEVIATION_SHARE,
            S.KEY_DETOUR_SENSITIVITY: S.DEFAULT_DETOUR_SENSITIVITY,
            S.KEY_MIN_SRV_RESPONDENTS: S.DEFAULT_MIN_SRV_RESPONDENTS,
            S.KEY_FALLBACK_WARN_SHARE: S.DEFAULT_FALLBACK_WARN_SHARE, S.KEY_DAY_ABSENCE_ENABLED: True}, **override}


def _run_stage(tmp_path, monkeypatch, mutate_world=None, **config):
    from braunschweig.analysis import spatial
    data_path = tmp_path / "data"
    _copy_real_tables(data_path)
    world = _world()
    if mutate_world is not None:
        mutate_world(world)
    # household 2 lies in 03103 by the VG250 join, but in 03102 by its home zone
    monkeypatch.setattr(spatial, "assign_geographies", lambda homes, kreise=None: pd.DataFrame(
        {"household_id": [1, 2], "ars5": ["03101", "03103"]}))
    own = tmp_path / "cache"
    own.mkdir()
    context = _ExecuteContext(world["stages"], _config(tmp_path, data_path, **config), str(own))
    result = S.execute(context)
    return world, context, result, tmp_path / "out" / S.OUTPUT_SUBDIR


@pytest.fixture
def stage_run(tmp_path, monkeypatch):
    return _run_stage(tmp_path, monkeypatch)


def _csv(directory, key):
    return pd.read_csv(directory / S.OUTPUT_FILES[key], dtype={"home_ars5": str, "orig_ars5": str, "code": str,
                                                                "home_kreis": str})


def test_stage_writes_the_acceptance_table_report_and_diagnostics_on_a_toy_population(stage_run):
    world, context, result, out = stage_run
    for name in S.OUTPUT_FILES.values():
        assert (out / name).is_file(), name
    assert (pathlib.Path(context.path()) / S.REPORT_FILE).is_file()
    table = _csv(out, "acceptance").set_index(["section", "key"])
    local, far = world["local"], world["far"]
    # counted from the toy: persons 1, 2, 6 sit at the local site (person 4 is absent), person 3 at the far one
    assert table.loc[("site", f"{local}|residents_of_day"), "model"] == 3
    assert table.loc[("site", f"{local}|incommuters"), "model"] == 2
    assert table.loc[("site", f"{local}|total"), "model"] == 5
    assert table.loc[("out_commuters", far), "model"] == 1
    assert table.loc[("incommuters_by_origin", world["origin"]), "model"] == 2
    assert table.loc[("students_population", "03101"), "model"] == 3    # persons 1, 2, 8 (8 has no education activity)
    assert table.loc[("students_population", "03102"), "model"] == 3    # persons 3, 4, 6
    assert table.loc[("students_of_day", "03101"), "model"] == 2        # 8 has no activity
    assert table.loc[("students_of_day", "03102"), "model"] == 2        # 4 is absent
    assert table.loc[("students_30plus_report", "03102"), "model"] == 1  # person 6, 35 years
    assert result["verdict"] == S.VERDICT_REPORT_ONLY
    report = json.loads((out / S.REPORT_FILE).read_text(encoding="utf-8"))
    assert report["acceptance"]["verdict"] == S.VERDICT_REPORT_ONLY and report["acceptance"]["n_rows_judged"] == 0
    assert report["counts"]["n_state_path_mismatch"] == 0
    assert report["counts"]["n_non_students_at_a_university_building"] == 0
    assert report["home_kreis_vs_vg250"]["n_different"] == 1            # the household of 03102 by home zone
    assert "mikrozensus_distance_bands" in report["skipped"]
    for entry in report["inputs"].values():
        assert not pathlib.PurePosixPath(entry["path_relative_to_data_path"]).is_absolute()
        assert ":" not in entry["path_relative_to_data_path"]


def test_stage_fallback_rates_and_distance_rows_on_the_toy_population(stage_run):
    _, _, _, out = stage_run
    report = json.loads((out / S.REPORT_FILE).read_text(encoding="utf-8"))
    rates = {entry["tag"]: entry for entry in report["fallback_rates"]}
    plan = rates["university plan rule, present (Task 9)"]
    assert (plan["n"], plan["fallback"], plan["primary"]) == (4, 1, 3)      # persons 1, 2, 3, 6; person 2 falls back
    assert rates["legacy decay, Kreis without a row of T (Task 10)"]["fallback"] == 0
    assert rates["in-commuter commute length (Task 11)"]["fallback"] == 1
    assert rates["in-commuter home rule (Task 11)"]["fallback"] == 1
    bands = _csv(out, "distance_bands")
    everyone = bands[(bands["group"] == "all") & (bands["reference"] == "srv")]
    assert sorted(everyone["detour_factor"]) == [1.2, 1.3, 1.4] and (everyone["n_model"] == 4).all()
    assert (bands["reference"] == "mid").sum() == 3
    # person 1 lives 2 km from the building, person 2 too, person 3 (Kreis 03102, 20 km) 20 km: x 1.3 gives
    # 2.6 and 26 km (the person 6 of 03102 is 18 km from its building: 23.4 km)
    row = everyone[everyone["detour_factor"] == 1.3].iloc[0]
    assert row["model_share_0_5"] == pytest.approx(0.5) and row["model_share_10_25"] == pytest.approx(0.25)
    assert row["model_share_25_50"] == pytest.approx(0.25)
    allotment = _csv(out, "allotment")
    assert allotment.set_index("home_kreis").loc["03101", "n_persons"] == 2
    assert _csv(out, "non_students").set_index("routed_level")["persons"].to_dict() == {"adult_education": 1}
    assert (_csv(out, "flows")["kind"] == "incommuter").sum() >= 1


def test_configure_declares_the_inputs_and_refuses_the_configurations_the_design_rules_out():
    recorder = _ConfigureRecorder()
    S.configure(recorder)
    assert {S.GRAVITY_STAGE, S.INCOMMUTERS_STAGE, S.FACILITIES_STAGE, S.ABSENCE_STAGE, S.TRIPS_STAGE,
            S.HOME_ZONES_STAGE, S.ENRICHED_STAGE} <= set(recorder.stages)
    assert recorder.keys[S.KEY_MIN_PERSONS] == 200 and recorder.keys[S.KEY_MAX_DEVIATION_SHARE] == 0.05
    off = _ConfigureRecorder({S.KEY_DAY_ABSENCE_ENABLED: False})
    S.configure(off)
    assert S.ABSENCE_STAGE not in off.stages
    with pytest.raises(ValueError, match="flows_enabled"):
        S.configure(_ConfigureRecorder({S.KEY_FLOWS_ENABLED: False}))
    with pytest.raises(ValueError, match="student_cells"):
        S.configure(_ConfigureRecorder({S.KEY_DAY_ABSENCE_STUDENT_CELLS: False}))


def test_stage_fail_branch_logs_an_error_names_the_population_rows_and_says_so_in_the_summary(
        tmp_path, monkeypatch, caplog):
    import logging
    with caplog.at_level(logging.ERROR, logger=S.LOGGER.name):
        _, _, result, out = _run_stage(tmp_path, monkeypatch, **{S.KEY_MIN_PERSONS: 0})
    assert result["verdict"] == S.VERDICT_FAIL
    errors = [r.getMessage() for r in caplog.records if r.levelno == logging.ERROR]
    assert any("FAIL" in m and "not used for results on a population that fails the population rows" in m
               for m in errors)
    report = json.loads((out / S.REPORT_FILE).read_text(encoding="utf-8"))
    failing = report["acceptance"]["population_rows_failing"]
    assert failing and {row["section"] for row in failing} <= {"students_population", "students_of_day"}
    assert any(row["key"] == "03101" for row in failing)
    summary = (out / S.OUTPUT_FILES["summary"]).read_text(encoding="utf-8")
    assert "**FAIL**" in summary and "not used for results on a population that fails the population rows" in summary
    # the SrV rows with enough respondents are named; the others are reported only
    assert report["srv_distance_rows"]["usable"] == ["03101", "all"]
    assert report["srv_distance_rows"]["total_row_code"] == "03ZGB"
    assert "03157" in report["srv_distance_rows"]["reported_only"]
    assert "Usable" in summary and "Reported only" in summary
    bands = _csv(out, "distance_bands")
    srv = bands[bands["reference"] == "srv"]
    assert srv.set_index("group")["n_reference_low"].astype(bool).to_dict()["03157"] is True
    assert not srv[srv["group"].isin(["03101", "all"])]["n_reference_low"].astype(bool).any()


def test_stage_lists_each_detour_factor_once_and_counts_students_outside_the_level_kreise(
        tmp_path, monkeypatch):
    _, _, _, out = _run_stage(tmp_path, monkeypatch, **{S.KEY_DETOUR_SENSITIVITY: [1.3, 1.2, 1.2, 1.4]})
    assert json.loads((out / S.REPORT_FILE).read_text(encoding="utf-8"))["parameters"]["detour_factors"] == [
        1.3, 1.2, 1.4]
    bands = _csv(out, "distance_bands")
    assert len(bands[(bands["group"] == "all") & (bands["reference"] == "srv")]) == 3
    report = json.loads((out / S.REPORT_FILE).read_text(encoding="utf-8"))
    assert report["counts"]["n_adult_students_outside_level_kreise"] == 0


def test_stage_refuses_an_incommuter_id_that_is_also_a_residents_id(tmp_path, monkeypatch):
    def clash(world):
        world["stages"][S.INCOMMUTERS_STAGE]["persons"].loc[0, "person_id"] = 1

    with pytest.raises(ValueError, match="residents' ids"):
        _run_stage(tmp_path, monkeypatch, mutate_world=clash)


@pytest.mark.parametrize("paths,present,present_fallback,absent,absent_fallback", [
    (["plan_length", "plan_length", "fallback_decay", "absent_plan_length", "absent_fallback_decay",
      "absent_fallback_decay", "legacy_decay"], 3, 1, 3, 2),
    (["home_distance", "home_distance", "absent_home_distance", "absent_home_distance", "absent_home_distance"],
     2, 0, 3, 0)])
def test_university_rule_rates_count_the_absent_students_under_both_rules(paths, present, present_fallback, absent,
                                                                          absent_fallback):
    """Regression: the absent block reported n 0 because it was computed from the present residents only."""
    present_entry, absent_entry = S.university_rule_rates(pd.Series(paths), 0.25)
    assert (present_entry["n"], present_entry["fallback"]) == (present, present_fallback)
    assert (absent_entry["n"], absent_entry["fallback"]) == (absent, absent_fallback)
