"""The student flows switch in the education location stage (issue #140, Phase 4, design B5, B7).

``braunschweig.synthesis.locations.education_gravity`` places the persons of level ``university`` by the residents'
flows when ``braunschweig.students.flows_enabled`` is on: the fit of the flow matrix on the persons present, the
unbiased rounding, the allotment inside each home Kreis and the building draw. The tests here cover what only the
stage wiring can get wrong: the inputs it declares per switch state, the configuration refusals, the shares the
destinations receive on a toy pipeline, the counted routes (a home Kreis without a row of T, the absent persons),
the report file and the independence of the draws from the row order. The pure functions have their own tests
(``tests/test_university_assignment_fit.py``, ``tests/test_university_assignment_allotment.py``); the OFF path's
byte-identity is pinned by the golden values of ``tests/test_education_routing.py``.
"""
from __future__ import annotations

import json

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point, box

from braunschweig.data.education.student_flows_inputs import KEY_FLOWS_ENABLED, T_COLUMNS
from braunschweig.synthesis.locations import education_gravity as stage

CRS = "EPSG:25832"
_ON_ONLY_STAGES = {stage.ABSENCE_STAGE, stage.TRIPS_STAGE, stage.HOME_ZONES_STAGE}


class _ConfigureContext:
    """synpp ConfigurationContext: ``config(key[, default])`` declares a key (an override wins), ``stage`` a parent."""

    def __init__(self, overrides=None):
        self.overrides = overrides or {}
        self.declared, self.stages = {}, []

    def config(self, key, default=None):
        value = self.overrides.get(key, default)
        self.declared[key] = value
        return value

    def stage(self, name, **kwargs):
        self.stages.append(name)


class _ExecuteContext:
    """synpp ExecuteContext: ``config(key)`` takes one argument and only a declared key; ``stage`` only a parent."""

    def __init__(self, declared, declared_stages, stages, path):
        self._config, self._declared_stages, self._stages, self._path = declared, set(declared_stages), stages, path

    def config(self, key):
        return self._config[key]

    def stage(self, name):
        assert name in self._declared_stages, f"stage {name} read but not declared"
        return self._stages[name]

    def path(self):
        return str(self._path)


def _configure(**overrides):
    context = _ConfigureContext(overrides)
    stage.configure(context)
    return context


# ------------------------------------------------------------------------------------------------ declarations
def test_off_path_declares_no_new_input_and_no_new_key():
    on = _configure()
    off = _configure(**{KEY_FLOWS_ENABLED: False})
    assert _ON_ONLY_STAGES <= set(on.stages)
    assert not _ON_ONLY_STAGES & set(off.stages)
    flows_keys = {stage.KEY_KREIS_MATCHING_RULE, stage.KEY_FIT_TOLERANCE, stage.KEY_FIT_MAX_ITERATIONS,
                  stage.KEY_MATCHING_FALLBACK_WARN_SHARE, stage.KEY_OD_TABLE_PATH, stage.KEY_DAY_ABSENCE_ENABLED,
                  stage.KEY_DAY_ABSENCE_STUDENT_CELLS}
    assert flows_keys <= set(on.declared)
    assert not flows_keys & set(off.declared)
    assert on.declared[stage.KEY_KREIS_MATCHING_RULE] == "home_distance"   # owner decision 2026-11-28 (ADR-0213)


def test_flows_with_day_absence_off_declare_no_absence_stage():
    context = _configure(day_absence_enabled=False)
    assert stage.ABSENCE_STAGE not in context.stages
    assert {stage.TRIPS_STAGE, stage.HOME_ZONES_STAGE} <= set(context.stages)


@pytest.mark.parametrize("overrides,names", [
    ({"day_absence_enabled": True, "day_absence_student_cells_enabled": False},
     ["braunschweig.students.flows_enabled", "day_absence_enabled", "day_absence_student_cells_enabled"]),
    ({"braunschweig.students.kreis_matching_rule": "nearest"}, ["braunschweig.students.kreis_matching_rule"]),
])
def test_refused_configurations_raise_naming_the_keys(overrides, names):
    with pytest.raises(ValueError) as error:
        _configure(**overrides)
    for name in names:
        assert name in str(error.value)
    # the same settings with the flows OFF are not refused
    _configure(**{KEY_FLOWS_ENABLED: False, **overrides})


def test_repeated_literals_equal_their_owners():
    """The stage repeats the names of the day-absence stage and the table key of the university facilities (import
    boundary of Phase 4); a renamed owner would leave the stage reading a key nobody sets."""
    from braunschweig.data.schools import university_facilities
    from braunschweig.synthesis.day_absence import absence, absence_stage
    assert (stage.KEY_DAY_ABSENCE_ENABLED, stage.DEFAULT_DAY_ABSENCE_ENABLED) == (
        absence_stage.KEY_ENABLED, absence_stage.DEFAULT_ENABLED)
    assert (stage.KEY_DAY_ABSENCE_STUDENT_CELLS, stage.DEFAULT_DAY_ABSENCE_STUDENT_CELLS) == (
        absence_stage.KEY_STUDENT_CELLS, absence_stage.DEFAULT_STUDENT_CELLS)
    assert (stage.ABSENCE_STAGE, stage.TRIPS_STAGE, stage.HOME_ZONES_STAGE) == (
        absence_stage.STAGE_NAME, absence_stage.TRIPS_STAGE, absence_stage.HOME_ZONES_STAGE)
    assert stage.STATE_PRESENT == absence.STATE_PRESENT
    assert stage.DAY_ABSENCE_STATE_COLUMN in absence.ABSENCE_COLUMNS
    # The output key has no constant in its owner: read it off the stage's own output (OFF path, no file read).
    enriched = pd.DataFrame({"person_id": [1, 2], "household_id": [1, 1], "age": [22, 50]})
    output = absence_stage.execute(_ExecuteContext({absence_stage.KEY_ENABLED: False}, ["synthesis.population.enriched"],
                                                   {"synthesis.population.enriched": enriched}, None))
    assert stage.DAY_ABSENCE_STATE_COLUMN in output[stage.ABSENCE_OUTPUT_KEY].columns
    assert (stage.KEY_OD_TABLE_PATH, stage.DEFAULT_OD_TABLE_PATH) == (
        university_facilities.KEY_OD_TABLE_PATH, university_facilities.DEFAULT_OD_TABLE_PATH)


# ------------------------------------------------------------------------------------------------ the toy pipeline
# Two home Kreise with rows of T (03101, 03102) and Wolfsburg (03103) without one; three destinations with a share
# (a local site of two buildings, a local site of one, a surrounding institution) and one destination ("ZERO") that
# only an outside Kreis attends: no share, no facility.
_T_ROWS = [  # home, in region, destination, scope, heads, cell kind
    ("03101", True, "BS-TU", "local", 600.0, "commuter"),
    ("03101", True, "BS-TU", "local", 50.0, "mover"),          # two rows of one pair: the seed is their sum
    ("03101", True, "WF", "local", 100.0, "commuter"),
    ("03101", True, "GOE", "surrounding", 300.0, "commuter"),
    ("03102", True, "BS-TU", "local", 200.0, "commuter"),
    ("03102", True, "WF", "local", 300.0, "commuter"),
    ("03102", True, "GOE", "surrounding", 100.0, "commuter"),
    ("03201", False, "BS-TU", "local", 100.0, "commuter"),
    ("03201", False, "ZERO", "local", 40.0, "commuter"),
]
_REGION_HEADS = {"BS-TU": 850.0, "WF": 400.0, "GOE": 400.0}
_PRESENT = {"03101": 50, "03102": 30}
_ABSENT = {"03101": 10, "03102": 6}
_WOLFSBURG = 4          # persons of 03103, the first of them absent


def _write_od_table(data_path):
    rows = []
    for home, in_region, destination, scope, heads, kind in _T_ROWS:
        row = {column: 0.0 for column in T_COLUMNS}
        row.update(home_ars5=home, home_in_region=in_region, destination_id=destination, institution=destination,
                   site_kreis_ars5="03101", scope=scope, students_persons=heads, cell_kind=kind)
        rows.append(row)
    path = data_path / stage.DEFAULT_OD_TABLE_PATH
    path.parent.mkdir(parents=True)
    pd.DataFrame(rows, columns=T_COLUMNS).to_csv(path, index=False, encoding="utf-8")


def _people():
    """``[person_id, kreis, present, x, y, leg_km]``: the students of the three Kreise."""
    rng = np.random.default_rng(11)
    rows = []
    centre = {"03101": (605000.0, 5790000.0), "03102": (612000.0, 5781000.0), "03103": (620000.0, 5810000.0)}
    groups = [(kreis, True, n) for kreis, n in _PRESENT.items()] + [(kreis, False, n) for kreis, n in _ABSENT.items()]
    groups += [("03103", False, 1), ("03103", True, _WOLFSBURG - 1)]
    person_id = 100
    for kreis, present, n in groups:
        for _ in range(n):
            x, y = np.array(centre[kreis]) + rng.uniform(-3000.0, 3000.0, 2)
            leg_km = np.nan if person_id % 5 == 0 else float(rng.uniform(0.5, 120.0))   # every fifth: no leg
            rows.append((person_id, kreis, present, x, y, leg_km))
            person_id += 1
    return pd.DataFrame(rows, columns=["person_id", "kreis", "present", "x", "y", "leg_km"])


def _universities():
    return gpd.GeoDataFrame({
        "location_id": ["bs_1", "bs_2", "wf_1", "goe"], "capacity": [300.0, 100.0, 100.0, 1000.0],
        "destination_id": ["BS-TU", "BS-TU", "WF", "GOE"], "scope": ["local", "local", "local", "surrounding"],
        "site_ags8": ["03101000", "03101000", "03158037", None],
        "geometry": [Point(605000.0, 5790000.0), Point(606000.0, 5790500.0), Point(612000.0, 5780000.0),
                     Point(564897.0, 5710437.0)]}, crs=CRS)


def _stages(people, row_order):
    people = people.iloc[row_order].reset_index(drop=True)
    homes = gpd.GeoDataFrame({"household_id": people["person_id"] + 10000},
                             geometry=[Point(x, y) for x, y in zip(people["x"], people["y"])], crs=CRS)
    legs = people.dropna(subset=["leg_km"])
    trips = pd.DataFrame({"person_id": legs["person_id"], "preceding_purpose": "home",
                          "following_purpose": "education", "euclidean_distance": legs["leg_km"] * 1000.0})
    school = gpd.GeoDataFrame({"school_id": ["g1"], "level": ["grundschule"], "capacity": [10.0],
                               "commune_id": ["03101000"]}, geometry=[Point(605000.0, 5790000.0)], crs=CRS)
    return {
        "synthesis.population.spatial.primary.candidates": {"persons": pd.DataFrame({
            "person_id": people["person_id"], "household_id": people["person_id"] + 10000,
            "has_education_trip": True})},
        "synthesis.population.enriched": pd.DataFrame({"person_id": people["person_id"], "age": 23, "P_TAET": 10}),
        "synthesis.population.spatial.home.locations": homes,
        "braunschweig.data.schools.facilities": school,
        "braunschweig.data.schools.university_facilities": _universities(),
        "braunschweig.data.schools.kita_facilities": gpd.GeoDataFrame(
            {"location_id": ["k1"], "capacity": [10.0]}, geometry=[Point(605000.0, 5790000.0)], crs=CRS),
        "data.spatial.municipalities": gpd.GeoDataFrame(
            {"commune_id": ["031010000000"]}, geometry=[box(500000.0, 5600000.0, 700000.0, 5900000.0)], crs=CRS),
        "braunschweig.data.bbsr.regiostar": pd.DataFrame({"commune_id": ["03101000"], "regiostar7": [71]}),
        stage.HOME_ZONES_STAGE: pd.DataFrame({"household_id": people["person_id"] + 10000,
                                              "departement_id": people["kreis"]}),
        stage.TRIPS_STAGE: trips,
        stage.ABSENCE_STAGE: {"absence": pd.DataFrame({
            "person_id": people["person_id"],
            "day_absence_state": np.where(people["present"], "present", "absent_individual")})},
    }


def _run(tmp_path, people, row_order=None, **overrides):
    data_path = tmp_path / "data"
    if not data_path.exists():
        _write_od_table(data_path)
    out_dir = tmp_path / ("out_%d" % len(list(tmp_path.glob("out_*"))))
    out_dir.mkdir()
    overrides.setdefault(stage.KEY_KREIS_MATCHING_RULE, "plan_length")   # these tests exercise the plan rule
    configured = _configure(random_seed=5, data_path=str(data_path), education_bbs_share_by_age={16: 0.5},
                            **overrides)
    order = np.arange(len(people)) if row_order is None else row_order
    context = _ExecuteContext(configured.declared, configured.stages, _stages(people, order), out_dir)
    out = stage.execute(context)
    report = json.loads((out_dir / stage.UNIVERSITY_REPORT_FILE).read_text(encoding="utf-8"))
    return out, report


def test_on_path_gives_every_destination_its_share_of_the_present_persons_and_counts_the_other_routes(tmp_path):
    people = _people()
    out, report = _run(tmp_path, people)
    assert len(out) == len(people) and set(out["person_id"]) == set(people["person_id"])
    assert list(out.columns) == ["person_id", "commune_id", "location_id", "geometry", "routed_level",
                                 "destination_id", "assignment_path"]
    merged = out.merge(people, on="person_id")
    assert (merged["routed_level"] == "university").all()

    # The present persons of the Kreise with a row of T: every destination receives its share of the region's
    # heads in T times their number, within one person; every home Kreis keeps its persons exactly.
    fitted = merged[merged["present"] & (merged["kreis"] != "03103")]
    n_fitted = sum(_PRESENT.values())
    assert len(fitted) == n_fitted
    region_heads = sum(_REGION_HEADS.values())
    for destination, heads in _REGION_HEADS.items():
        received = int((fitted["destination_id"] == destination).sum())
        assert abs(received - n_fitted * heads / region_heads) < 1.0, destination
    assert set(fitted["assignment_path"]) == {"plan_length", "fallback_decay"}   # the plan rule, set explicitly by _run
    # a person with an observed leg is a primary case, one without a leg the counted fallback
    assert (fitted["assignment_path"] == "fallback_decay").tolist() == fitted["leg_km"].isna().tolist()

    # The absent persons follow their own totals and are counted apart; Wolfsburg has no row of T: legacy decay.
    absent = merged[~merged["present"] & (merged["kreis"] != "03103")]
    assert set(absent["assignment_path"]) <= {"absent_plan_length", "absent_fallback_decay"}
    wolfsburg = merged[merged["kreis"] == "03103"]
    assert sorted(wolfsburg["assignment_path"]) == ["absent_legacy_decay"] + ["legacy_decay"] * (_WOLFSBURG - 1)

    # Every person is at a building of the destination named; zero-share "ZERO" is used by nobody.
    building_destination = dict(zip(_universities()["location_id"], _universities()["destination_id"]))
    assert (merged["location_id"].map(building_destination) == merged["destination_id"]).all()
    building_point = dict(zip(_universities()["location_id"], _universities()["geometry"]))
    assert all(geometry.equals(building_point[location])
               for geometry, location in zip(out["geometry"], out["location_id"]))

    # The report counts what the frame shows.
    assert report["n_persons"] == len(people)
    assert report["n_present"] == n_fitted + _WOLFSBURG - 1
    assert report["n_absent"] == sum(_ABSENT.values()) + 1
    assert report["legacy_without_row"]["n_persons"] == _WOLFSBURG
    assert report["legacy_without_row"]["kreise"] == ["03103"]
    assert report["dropped_zero_share_destinations"] == ["ZERO"]
    by_kreis = report["counts_by_kreis"]
    for kreis in ("03101", "03102"):
        assert sum(n for path, n in by_kreis[kreis].items() if not path.startswith("absent_")) == _PRESENT[kreis]
        assert sum(n for path, n in by_kreis[kreis].items() if path.startswith("absent_")) == _ABSENT[kreis]
    assert report["plan_rule"]["n_fallback"] == int(fitted["leg_km"].isna().sum())
    assert report["allotment_diagnostics"]["n_persons"] == n_fitted
    assert report["fit"]["iterations"] >= 1
    assert set(report["realised_distance_km_by_home_kreis"]) == {"03101", "03102", "03103"}


@pytest.mark.parametrize("warn_share,plan_rule_warned", [(0.25, False), (0.1, True)])
def test_the_counted_routes_warn(tmp_path, caplog, warn_share, plan_rule_warned):
    """Wolfsburg's students (no row of T) always warn; the plan rule's fallback (every fifth person has no leg:
    20 % of the present, 25 % of the absent persons) warns above the configured share only, present and absent
    rated apart."""
    with caplog.at_level("INFO", logger=stage.__name__):
        _, report = _run(tmp_path, _people(), **{stage.KEY_MATCHING_FALLBACK_WARN_SHARE: warn_share})
    warnings = [record.getMessage() for record in caplog.records if record.levelname == "WARNING"]
    assert sum("no row of T" in message for message in warnings) == 1
    plan_warnings = [message for message in warnings if stage.KEY_MATCHING_FALLBACK_WARN_SHARE in message]
    assert len(plan_warnings) == (2 if plan_rule_warned else 0)
    assert report["plan_rule_absent"]["n_fallback"] == 4 and report["plan_rule_absent"]["n_persons"] == 16


def test_on_path_does_not_depend_on_the_row_order_and_runs_without_day_absence(tmp_path, monkeypatch):
    people = _people()
    # draw_buildings is free of the row order only for persons indexed by their id: the stage must pass that index.
    indexes = []

    def recording_draw_buildings(destination_ids, facilities, rng):
        indexes.append(destination_ids.index)
        return stage_draw_buildings(destination_ids, facilities, rng)

    stage_draw_buildings = stage.draw_buildings
    monkeypatch.setattr(stage, "draw_buildings", recording_draw_buildings)
    out, _ = _run(tmp_path, people)
    allotted_ids = set(people.loc[people["kreis"] != "03103", "person_id"])
    assert len(indexes) == 1 and indexes[0].name == "person_id" and set(indexes[0]) == allotted_ids
    reversed_out, _ = _run(tmp_path, people, row_order=np.arange(len(people))[::-1])
    by_person = out.set_index("person_id").sort_index()
    by_person_reversed = reversed_out.set_index("person_id").sort_index()
    pd.testing.assert_series_equal(by_person["location_id"], by_person_reversed["location_id"])
    pd.testing.assert_series_equal(by_person["assignment_path"], by_person_reversed["assignment_path"])

    # Day absence OFF: no absence stage is read and every person is present.
    no_absence, report = _run(tmp_path, people, day_absence_enabled=False)
    assert report["n_absent"] == 0
    assert not no_absence["assignment_path"].str.startswith("absent_").any()
