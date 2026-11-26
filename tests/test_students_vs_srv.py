"""The synpp stage ``braunschweig.analysis.synthesis.students_vs_srv`` (#140, Phase 1, Task 8).

The stage test runs ``execute`` on six synthetic persons against COPIES of the real committed
SrV student tables (so a schema drift between the committed files and the stage fails here, not
on the run server) and a synthetic Zensus employed-by-age file (the real one is local-only).
Only the VG250 access (``spatial.assign_geographies``) is stubbed, as in
``tests/test_plan_structure_vs_srv.py``. The tables' own numbers are tested in
``tests/test_student_measurement.py``; this file tests the wiring: inputs, outputs, provenance,
the cache token and the stage's refusals.
"""
from __future__ import annotations

import json
import os
import shutil

import numpy as np
import pandas as pd
import pytest

from braunschweig import synpp_tokens
from braunschweig.analysis import student_measurement as M
from braunschweig.analysis.synthesis import students_vs_srv as S
from braunschweig.calibration import srv_student_reference as R
from braunschweig.popsim import zensus_employment_age as Z
from braunschweig.popsim.completed_donor import (DIARY_TRACE_FILE, WEEKEND_TRACE_FILE,
                                                 CompletedDonor)
from braunschweig.synthesis.day_absence.absence import STATE_ABSENT_INDIVIDUAL, STATE_PRESENT

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COMMITTED_SRV_DIR = os.path.join(REPO_ROOT, "eqasim-data", "data", "braunschweig", "srv")
SRV_TABLES = (R.PARAMETER_TABLE, R.STUDENT_SHARE_TABLE, R.YOUNG_ADULT_STATUS_TABLE)
OUTPUTS = ("student_level_by_kreis.csv", "young_adult_status_by_kreis.csv",
           "student_work_class.csv", "student_behaviour_vs_srv.csv", "student_plan_source.csv",
           "summary.md", "provenance.json")
HOUR_S = 3600.0
COMPLETED_DONOR = "braunschweig.popsim.completed_donor"

# person_id, household_id, age, sex, P_TAET, has_license, number_of_cars, number_of_bicycles,
# pt_subscription_type, has_pt_subscription. Students (P_TAET 10): 1 and 2 in 03101, 4 in 03102
# (away all day), 5 in Wolfsburg 03103; 3 and 6 are employed (P_TAET 1).
_PERSONS = [
    (1, 10, 22, "female", 10, True, 1, 2, "job_or_semester_ticket", True),
    (2, 20, 31, "male", 10, False, 0, 0, "deutschlandticket", True),
    (3, 20, 55, "female", 1, True, 0, 0, "single_ticket", False),
    (4, 30, 25, "male", 10, True, 2, 1, "never_pt", False),
    (5, 40, 23, "female", 10, True, 1, 1, "deutschlandticket", True),
    (6, 50, 27, "male", 1, False, 0, 1, "single_ticket", False),
]
_HOMES = {10: "03101", 20: "03101", 30: "03102", 40: "03103", 50: "03102"}
_ABSENT = {4}
# person_id, trip_index, departure hour, arrival hour, preceding purpose, following purpose
_TRIPS = [
    (1, 0, 8.0, 8.5, "home", "education"), (1, 1, 16.0, 16.5, "education", "home"),
    (2, 0, 9.0, 9.5, "home", "education"), (2, 1, 13.0, 13.5, "education", "home"),
    (3, 0, 7.0, 7.5, "home", "work"), (3, 1, 17.0, 17.5, "work", "home"),
    (5, 0, 8.0, 8.5, "home", "education"), (5, 1, 12.0, 12.5, "education", "home"),
    (6, 0, 7.0, 7.5, "home", "work"), (6, 1, 16.0, 16.5, "work", "home"),
]
# Zensus employed persons aged 20-29 (M, F) of the two surveyed Kreise the persons live in;
# every other ZGB Kreis carries (10, 10).
_ZENSUS_20_29 = {"03101": (100, 120), "03102": (50, 40)}
_REDRAWN_DIARY_REASON = [reason for reason in M.DIARY_REASONS
                         if reason not in M.DIARY_KEEP_REASONS][0]


def _persons():
    persons = pd.DataFrame(_PERSONS, columns=[
        "person_id", "household_id", "age", "sex", "P_TAET", "has_license", "number_of_cars",
        "number_of_bicycles", "pt_subscription_type", "has_pt_subscription"])
    persons["employed"] = persons["P_TAET"] == 1
    persons["employment_status"] = np.where(persons["employed"], "vollzeit",
                                            "nicht_erwerbstaetig")
    # Seed key = (household_id * 10, 1); person 3 is the second member of household 20.
    persons["H_ID"] = persons["household_id"] * 10
    persons["P_ID"] = np.where(persons["person_id"] == 3, 2, 1)
    persons["attribute_source_H_ID"] = persons["H_ID"]
    persons["attribute_source_P_ID"] = persons["P_ID"]
    # Student 2 carries a re-drawn plan of donor (900, 1); everybody else the own diary.
    persons["source_H_ID"] = np.where(persons["person_id"] == 2, 900, persons["H_ID"])
    persons["source_P_ID"] = persons["P_ID"]
    return persons


def _trips():
    trips = pd.DataFrame(_TRIPS, columns=["person_id", "trip_index", "dep_h", "arr_h",
                                          "preceding_purpose", "following_purpose"])
    trips["departure_time"] = trips.pop("dep_h") * HOUR_S
    trips["arrival_time"] = trips.pop("arr_h") * HOUR_S
    return trips


def _homes():
    return pd.DataFrame({"household_id": list(_HOMES), "ars5": list(_HOMES.values())})


def _absence():
    persons = _persons()
    return pd.DataFrame({"person_id": persons["person_id"],
                         "household_id": persons["household_id"],
                         "day_absence_state": np.where(persons["person_id"].isin(_ABSENT),
                                                       STATE_ABSENT_INDIVIDUAL, STATE_PRESENT)})


def _completed_donor():
    # H_ID float64, as the production frames carry it.
    donors = pd.DataFrame({"H_ID": [100.0, 200.0, 200.0, 300.0, 400.0, 500.0, 900.0],
                           "P_ID": [1, 1, 2, 1, 1, 1, 1],
                           "P_TAET": [10, 10, 1, 10, 10, 1, 10]})
    return CompletedDonor(households=pd.DataFrame(), persons=donors, completeness_report=None,
                          completion_report=None, weekend_report=None)


def _write_traces(directory, weekend=True, diary=True):
    os.makedirs(directory, exist_ok=True)
    if weekend:
        pd.DataFrame({"H_ID": [100.0, 200.0, 300.0, 400.0], "P_ID": [1, 1, 1, 1],
                      "resolution": ["own_plan", "hh_match", "own_plan", "own_plan"]}
                     ).to_parquet(os.path.join(directory, WEEKEND_TRACE_FILE))
    if diary:
        pd.DataFrame({"H_ID": [100.0, 200.0], "P_ID": [1, 1],
                      "reason": [M.DIARY_KEEP_REASONS[0], _REDRAWN_DIARY_REASON]}
                     ).to_parquet(os.path.join(directory, DIARY_TRACE_FILE))


def _data_path(tmp_path, srv_tables=SRV_TABLES, zensus=True):
    """A data directory holding copies of the committed SrV tables and a synthetic Zensus file."""
    data = tmp_path / "data"
    srv = data / "braunschweig" / "srv"
    srv.mkdir(parents=True)
    for name in srv_tables:
        shutil.copyfile(os.path.join(COMMITTED_SRV_DIR, name), srv / name)
    if zensus:
        rows = []
        for kreis in M.ZGB_KREISE:
            male, female = _ZENSUS_20_29.get(kreis, (10, 10))
            rows += [(kreis, "x", "20-29", "M", male), (kreis, "x", "20-29", "F", female),
                     (kreis, "x", "30-39", "M", 1000)]
        popsim = data / "braunschweig" / "popsim"
        popsim.mkdir(parents=True)
        with open(popsim / Z.KREIS_EMPLOYED_BY_AGE_FILE, "w", encoding="utf-8", newline="\n") as f:
            f.write("# synthetic test table\n")
            pd.DataFrame(rows, columns=["region", "region_name", "age_class", "sex",
                                        "employed_persons"]).to_csv(f, index=False)
    return data


class _ConfigureRecorder:
    """synpp ConfigurationContext stand-in: records declared stages (by local name) and keys."""

    def __init__(self, config=None):
        self.stages = []
        self.config_keys = {}
        self._config = dict(config or {})

    def stage(self, name, alias=None, **kwargs):
        self.stages.append(alias or name)

    def config(self, key, *args, **kwargs):
        value = self._config.get(key, args[0] if args else None)
        self.config_keys[key] = value
        return value


class _ExecuteContext:
    """synpp ExecuteContext stand-in recording what ``execute`` reads; ``config`` takes the key
    alone, as real synpp does (tests/test_execute_context_config_contract.py)."""

    def __init__(self, stages, config, stage_paths):
        self._stages, self._config, self._stage_paths = stages, config, stage_paths
        self.stages_read, self.config_read, self.paths_read = set(), set(), set()

    def stage(self, name):
        self.stages_read.add(name)
        return self._stages[name]

    def config(self, key):
        self.config_read.add(key)
        return self._config[key]

    def path(self, name=None):
        self.paths_read.add(name)
        return self._stage_paths[name]


def _context(tmp_path, data_path, absence_enabled=True):
    stage_dir = tmp_path / "cache" / (COMPLETED_DONOR + ".cache")
    return _ExecuteContext(
        stages={"synthesis.population.enriched": _persons(), "trips": _trips(),
                "synthesis.population.spatial.home.locations": _homes().assign(geometry=None),
                S.ABSENCE_STAGE: {"absence": _absence()},
                COMPLETED_DONOR: _completed_donor()},
        config={"output_path": str(tmp_path / "out"), "data_path": str(data_path),
                "sampling_rate": 0.25, S.KEY_SUBDIR: S.DEFAULT_SUBDIR,
                S.KEY_MAX_UNMATCHED_HOME_SHARE: S.DEFAULT_MAX_UNMATCHED_HOME_SHARE,
                S.KEY_DAY_ABSENCE_ENABLED: absence_enabled, S.KEY_PORTAL_ENABLED: False},
        stage_paths={COMPLETED_DONOR: str(stage_dir)})


@pytest.fixture
def stubbed_geographies(monkeypatch):
    from braunschweig.analysis import spatial
    monkeypatch.setattr(spatial, "assign_geographies", lambda homes, kreise=None: _homes())


def _read(directory, name):
    return pd.read_csv(directory / name, dtype={"code": str})


def test_execute_writes_all_outputs_on_synthetic_stages(tmp_path, stubbed_geographies):
    data_path = _data_path(tmp_path)
    context = _context(tmp_path, data_path)
    _write_traces(context.path(COMPLETED_DONOR))

    S.execute(context)

    out = tmp_path / "out" / S.DEFAULT_SUBDIR
    for name in OUTPUTS:
        assert (out / name).is_file(), name

    # Every stage and key execute reads is declared in configure (synpp raises otherwise).
    recorder = _ConfigureRecorder(config={S.KEY_PORTAL_ENABLED: False})
    S.configure(recorder)
    assert context.stages_read <= set(recorder.stages)
    assert context.config_read <= set(recorder.config_keys)
    assert context.paths_read <= set(recorder.stages)
    assert recorder.config_keys[S.KEY_SUBDIR] == "analysis/students_vs_srv"
    assert recorder.config_keys[S.KEY_MAX_UNMATCHED_HOME_SHARE] == 0.05

    provenance = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
    for name in SRV_TABLES:
        committed = os.path.join(COMMITTED_SRV_DIR, name)
        assert provenance["inputs"]["srv_tables"][name]["sha256"] == \
            synpp_tokens.text_file_sha256(committed)
    zensus_path = os.path.join(data_path, "braunschweig", "popsim", Z.KREIS_EMPLOYED_BY_AGE_FILE)
    assert provenance["inputs"]["zensus_employed_by_age"]["sha256"] == \
        synpp_tokens.file_sha256(zensus_path)
    assert provenance["plan_source"]["trace_available"] is True
    assert provenance["parameters"]["sampling_rate"] == 0.25
    assert provenance["status"] == "measurement, not validation"
    for table in ("student_frame", "student_level", "young_adult_status", "student_work_class",
                  "plan_source"):
        assert isinstance(provenance["diagnostics"][table], dict), table
    assert provenance["diagnostics"]["student_frame"]["n_adult_students_absent"] == 1

    # Region row (surveyed Kreise 03101 and 03102): adults 1, 2, 3, 4, 6; present 1, 2, 3, 6,
    # of whom students 1 and 2.
    level = _read(out, "student_level_by_kreis.csv").set_index("code")
    assert level.loc[M.REGION_CODE, "model_adults"] == 5
    assert level.loc[M.REGION_CODE, "model_student_share_adults"] == pytest.approx(0.5)
    assert level.loc[M.REGION_CODE, "model_students_expanded_persons"] == pytest.approx(3 / 0.25)
    assert np.isnan(level.loc[M.WOLFSBURG_KREIS, "srv_student_share_adults"])
    assert not np.isnan(level.loc[M.REGION_CODE, "srv_student_share_adults"])

    status = _read(out, "young_adult_status_by_kreis.csv")
    employed = status[(status["code"] == M.REGION_CODE) & (status["status"] == "employed")]
    # The region sums the seven surveyed Kreise: 03101 and 03102 plus five Kreise of 10 + 10.
    assert employed["zensus_employed_persons"].iloc[0] == pytest.approx(
        100 + 120 + 50 + 40 + 5 * (10 + 10))

    # Plan source over the adult students 1, 2, 4, 5: student 2 is re-drawn through the diary
    # match, the others carry their own diary.
    plan = _read(out, "student_plan_source.csv")
    assert plan["model_n"].sum() == 4
    redrawn = plan[plan["plan_origin"] == M.PLAN_ORIGIN_REDRAWN]
    assert redrawn[["match_path", "donor_status", "model_n"]].values.tolist() == [
        ["diary:" + _REDRAWN_DIARY_REASON, "student", 1]]

    behaviour = _read(out, "student_behaviour_vs_srv.csv")
    assert list(behaviour.columns) == M.COMPARISON_COLUMNS

    summary = (out / "summary.md").read_text(encoding="utf-8")
    assert "measurement, not validation" in summary
    assert M.REGION_SCOPE_NOTE in summary
    assert "present students" in summary


def test_a_missing_trace_is_recorded_and_its_paths_stay_unresolved(tmp_path, stubbed_geographies):
    context = _context(tmp_path, _data_path(tmp_path))
    _write_traces(context.path(COMPLETED_DONOR), weekend=False)

    S.execute(context)

    out = tmp_path / "out" / S.DEFAULT_SUBDIR
    provenance = json.loads((out / "provenance.json").read_text(encoding="utf-8"))
    assert provenance["plan_source"]["trace_available"] is False
    assert provenance["plan_source"]["traces"]["weekend"]["trace_available"] is False
    assert provenance["plan_source"]["traces"]["diary"]["trace_available"] is True
    plan = _read(out, "student_plan_source.csv")
    # Without the weekend trace only the diary path of student 2 resolves.
    paths = dict(zip(plan["match_path"], plan["model_n"]))
    assert paths == {"diary:" + _REDRAWN_DIARY_REASON: 1, M.MATCH_PATH_UNRESOLVED: 3}


@pytest.mark.parametrize("absence_enabled, srv_tables, zensus, error, match", [
    pytest.param(False, SRV_TABLES, True, ValueError, S.KEY_DAY_ABSENCE_ENABLED,
                 id="day-absence-off"),
    pytest.param(True, SRV_TABLES[1:], True, FileNotFoundError, R.PARAMETER_TABLE,
                 id="srv-table-missing"),
    pytest.param(True, SRV_TABLES, False, FileNotFoundError, Z.KREIS_EMPLOYED_BY_AGE_FILE,
                 id="zensus-missing"),
])
def test_execute_refuses_without_day_absence_or_an_input_table(
        tmp_path, stubbed_geographies, absence_enabled, srv_tables, zensus, error, match):
    context = _context(tmp_path, _data_path(tmp_path, srv_tables, zensus), absence_enabled)
    _write_traces(context.path(COMPLETED_DONOR))
    with pytest.raises(error, match=match):
        S.execute(context)


class _ValidateContext:
    def __init__(self, config):
        self._config = config

    def config(self, key):
        return self._config[key]


def test_validate_token_changes_with_a_reference_table(tmp_path, monkeypatch):
    from braunschweig.analysis import spatial
    archive = tmp_path / "vg250.zip"
    archive.write_bytes(b"placeholder")
    monkeypatch.setattr(spatial, "VG250_ZIP", archive)
    data_path = _data_path(tmp_path)
    context = _ValidateContext({"data_path": str(data_path)})

    before = S.validate(context)
    assert before == S.validate(context)
    assert set(before["modules"]) >= {M.__name__, "braunschweig.calibration.student_parameters",
                                      "braunschweig.analysis.plan_structure",
                                      "braunschweig.calibration.srv_plan_structure"}
    for name in SRV_TABLES:
        path = data_path / "braunschweig" / "srv" / name
        original = path.read_bytes()
        path.write_bytes(original + b"# appended line\n")
        assert S.validate(context)["files"] != before["files"], name
        path.write_bytes(original)
    zensus = data_path / "braunschweig" / "popsim" / Z.KREIS_EMPLOYED_BY_AGE_FILE
    zensus.write_bytes(zensus.read_bytes() + b"# appended line\n")
    assert S.validate(context)["files"] != before["files"]

