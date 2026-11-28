"""The student level table as the PopulationSim stage reads it (design A2, issue #140, ADR-0211).

All frames are SYNTHETIC except ``test_the_committed_level_table_reads_for_the_eight_kreise``, which
reads the committed table. The table's rules are owned by ADR-0210 (Decision 5) and
``braunschweig.calibration.student_level_table``; this file tests only the reader, the mapping of
its four age groups onto the two young grid groups (A13), the stage switch and its refusals.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
import pytest

from braunschweig.popsim import stage
from braunschweig.popsim import student_level as SL
from braunschweig.popsim.stage import config_keys

REPO_ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = REPO_ROOT / "eqasim-data" / "data"
KEY = config_keys.KEY_STUDENT_EMPLOYMENT_CONCEPT
KEY_RE = KEY.replace(".", r"\.")
OMEGA = 0.5
KREISE = ("03101", "03158")
# Students per (sex, age group) of every synthetic Kreis: 10 / 20 / 30 / 40 persons.
PERSONS = {"18_19": 10.0, "20_24": 20.0, "25_29": 30.0, "30plus": 40.0}


def _rows(kreise=KREISE, omega=OMEGA):
    return [{"code": kreis, "sex": sex, "age_group": age, "students_persons": persons,
             "students_counted_employed_persons": persons * omega}
            for kreis in kreise for sex in SL.SEXES for age, persons in PERSONS.items()]


def _write(data_path: Path, rows=None, omega_text="omega=0.5", columns=None) -> Path:
    path = data_path / SL.STUDENT_LEVEL_TABLE_RELPATH
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = pd.DataFrame(_rows() if rows is None else rows)
    if columns is not None:
        frame = frame[columns]
    header = f"# Table: synthetic level table\n# Census-year enrollment: {omega_text} = 1 - Z / S; Z=1\n"
    path.write_text(header + frame.to_csv(index=False, lineterminator="\n"), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- reader


def test_level_table_reader_checks_omega_cells_and_maps_four_groups_to_two(tmp_path):
    _write(tmp_path)
    level = SL.read_student_level_table(tmp_path, KREISE)
    assert level.omega == pytest.approx(OMEGA)
    assert list(level.table.columns) == SL.LEVEL_COLUMNS
    by_group = SL.employed_students_by_grid_group(level)
    # 18_19 + 20_24 + 25_29 -> 16_29 (60 persons x omega); 30plus -> 30_39 (40 x omega); A13.
    for kreis in KREISE:
        for sex in ("M", "F"):
            assert by_group[(kreis, sex, "16_29")] == pytest.approx(60.0 * OMEGA)
            assert by_group[(kreis, sex, "30_39")] == pytest.approx(40.0 * OMEGA)
    assert {group for _, _, group in by_group} == {"16_29", "30_39"}
    assert len(by_group) == len(KREISE) * 2 * 2


def test_the_mapping_onto_the_grid_groups_is_derived_from_a13():
    assert SL.grid_group_by_level_age() == {
        "18_19": "16_29", "20_24": "16_29", "25_29": "16_29", "30plus": "30_39"}


def test_a_missing_level_table_names_the_path_and_the_switch(tmp_path):
    with pytest.raises(ValueError, match=KEY_RE) as error:
        SL.read_student_level_table(tmp_path, KREISE)
    assert "student_level_table.csv" in str(error.value)


@pytest.mark.parametrize("omega_text", ["omega=1.2", "omega=0", "omega=nan", "other=0.5"])
def test_an_omega_outside_zero_one_or_absent_raises(tmp_path, omega_text):
    _write(tmp_path, omega_text=omega_text)
    with pytest.raises(ValueError, match="omega"):
        SL.read_student_level_table(tmp_path, KREISE)


def test_a_missing_column_raises(tmp_path):
    _write(tmp_path, columns=SL.LEVEL_COLUMNS[:-1])
    with pytest.raises(ValueError, match="students_counted_employed_persons"):
        SL.read_student_level_table(tmp_path, KREISE)


def test_a_missing_cell_or_kreis_raises_naming_it(tmp_path):
    rows = [r for r in _rows() if not (r["code"] == "03158" and r["sex"] == "male"
                                       and r["age_group"] == "25_29")]
    _write(tmp_path, rows=rows)
    with pytest.raises(ValueError, match=r"03158.*male.*25_29"):
        SL.read_student_level_table(tmp_path, KREISE)
    _write(tmp_path)
    with pytest.raises(ValueError, match="03103"):
        SL.read_student_level_table(tmp_path, KREISE + ("03103",))


@pytest.mark.parametrize("code", ["3101", "", "031010"])
def test_a_missing_or_malformed_kreis_code_raises(tmp_path, code):
    rows = _rows()
    rows[0]["code"] = code
    _write(tmp_path, rows=rows)
    with pytest.raises(ValueError, match="5-digit"):
        SL.read_student_level_table(tmp_path, KREISE)


def test_a_duplicate_or_unknown_cell_raises(tmp_path):
    rows = _rows()
    _write(tmp_path, rows=rows + rows[:1])
    with pytest.raises(ValueError, match="more than once"):
        SL.read_student_level_table(tmp_path, KREISE)
    _write(tmp_path, rows=rows + [dict(rows[0], age_group="15_17")])
    with pytest.raises(ValueError, match="15_17"):
        SL.read_student_level_table(tmp_path, KREISE)


def test_counted_employed_must_be_the_level_times_omega(tmp_path):
    rows = _rows()
    rows[3]["students_counted_employed_persons"] *= 1.001
    _write(tmp_path, rows=rows)
    with pytest.raises(ValueError, match="omega"):
        SL.read_student_level_table(tmp_path, KREISE)


def test_the_committed_level_table_reads_for_the_eight_kreise():
    kreise = ("03101", "03102", "03103", "03151", "03153", "03154", "03157", "03158")
    level = SL.read_student_level_table(DATA_PATH, kreise)
    assert 0.0 < level.omega < 1.0
    by_group = SL.employed_students_by_grid_group(level)
    assert sum(by_group.values()) == pytest.approx(
        level.table["students_persons"].sum() * level.omega, rel=1e-6)
    assert {kreis for kreis, _, _ in by_group} == set(kreise)


# --------------------------------------------------------------------------- stage switch


class _ExecuteContext:
    """synpp's ExecuteContext: ``config(key)`` takes ONE argument; an unstubbed key fails loudly."""

    def __init__(self, values):
        self.values = values

    def config(self, key):
        if key not in self.values:
            raise AssertionError(f"unexpected context.config({key!r})")
        return self.values[key]


def _values(data_path, **overrides):
    values = {KEY: True, "data_path": str(data_path),
              config_keys.KEY_STUDENT_EDUCATION_CONTROL: "on"}
    values.update(overrides)
    return values


def _read(context, *, grid_on=True, follow=True, level_source="zensus"):
    return stage._read_student_employment_config(
        context, employment_grid_on=grid_on, follow_grids=follow,
        employment_level_source=level_source, kreise=list(KREISE))


def test_the_switch_off_reads_nothing_else():
    settings = _read(_ExecuteContext({KEY: False}), grid_on=False, follow=False)
    assert settings.level is None and settings.students_employed_by_group is None
    assert settings.report() == {"enabled": False, "active": False, "reason": "switched off"}


def test_the_switch_on_returns_the_level_and_the_grid_mapping(tmp_path):
    _write(tmp_path)
    settings = _read(_ExecuteContext(_values(tmp_path)))
    assert settings.level.omega == pytest.approx(OMEGA)
    assert settings.students_employed_by_group[("03101", "F", "16_29")] == pytest.approx(30.0)


def test_the_switch_on_with_the_grid_off_warns_and_keeps_todays_path(caplog):
    # No entry point: nothing to lower. Logged as a WARNING and counted in the report, not refused
    # (code-default runs have the grid off). Neither the table nor data_path is read.
    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.stage"):
        settings = _read(_ExecuteContext({KEY: True}), grid_on=False, follow=False)
    assert settings.students_employed_by_group is None
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(KEY in m and config_keys.KEY_EMPLOYMENT_GRID in m and "inactive" in m
               for m in warnings), warnings
    report = settings.report()
    assert report["enabled"] is True and report["active"] is False
    assert config_keys.KEY_EMPLOYMENT_GRID in report["reason"]


@pytest.mark.parametrize("follow, level_source, write_table, other_key", [
    (False, "zensus", True, config_keys.KEY_KREIS_CONTROLS_FOLLOW_GRIDS),
    (True, "survey", True, config_keys.KEY_EMPLOYMENT_LEVEL_SOURCE),
    (True, "zensus", False, "student_level_table.csv"),
], ids=["follow_grids_false", "survey_level_source", "no_table"])
def test_student_employment_concept_refusals(tmp_path, follow, level_source, write_table, other_key):
    if write_table:
        _write(tmp_path)
    with pytest.raises(ValueError, match=KEY_RE) as error:
        _read(_ExecuteContext(_values(tmp_path)), follow=follow, level_source=level_source)
    assert other_key in str(error.value)


def test_the_student_education_entries_off_only_warn(tmp_path, caplog):
    _write(tmp_path)
    context = _ExecuteContext(_values(
        tmp_path, **{config_keys.KEY_STUDENT_EDUCATION_CONTROL: "off"}))
    with caplog.at_level(logging.WARNING, logger="braunschweig.popsim.stage"):
        settings = _read(context)
    assert settings.students_employed_by_group is not None
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert any(config_keys.KEY_STUDENT_EDUCATION_CONTROL in m and KEY in m for m in warnings), warnings


class _ConfigureContext:
    def __init__(self, values=None):
        self.declared, self.values = {}, values or {}

    def config(self, key, default=None, **kwargs):
        self.declared.setdefault(key, default)
        return self.values.get(key, default)

    def stage(self, *args, **kwargs):
        return None


def test_configure_declares_the_switch_on_by_default_with_its_inputs():
    context = _ConfigureContext()
    stage.configure(context)
    assert context.declared[KEY] is True
    assert config_keys.DEFAULT_STUDENT_EMPLOYMENT_CONCEPT is True
    assert "data_path" in context.declared
    assert context.declared[config_keys.KEY_STUDENT_EDUCATION_CONTROL] == "on"


def test_configure_refuses_a_non_boolean_switch():
    with pytest.raises(ValueError, match=KEY_RE):
        stage.configure(_ConfigureContext({KEY: "yes"}))


def test_base_config_switches_the_concept_on():
    text = (REPO_ROOT / "configs" / "base_bs.yml").read_text(encoding="utf-8").replace("\r\n", "\n")
    assert f"  {KEY}: true\n" in text


# --------------------------------------------------------------------------- stage report


def test_the_report_lists_the_subtracted_persons_and_the_smallest_remaining_target(tmp_path):
    _write(tmp_path)
    settings = _read(_ExecuteContext(_values(tmp_path)))
    lowered = pd.DataFrame({
        "KREIS": ["03101", "03101", "03158", "03158"], "sex": ["M", "F", "M", "F"],
        "grid_age_group": ["16_29", "30_39", "16_29", "30_39"],
        "zensus_employed_persons": [300.0, 200.0, 100.0, 80.0],
        "students_counted_employed_persons": [50.0, 10.0, 90.0, 5.0],
        "employed_target_persons": [250.0, 190.0, 10.0, 75.0]})
    settings.record_lowered_levels(lowered)
    report = settings.report()
    assert report["enabled"] is True and report["active"] is True and report["omega"] == OMEGA
    assert report["level_table"] == SL.STUDENT_LEVEL_TABLE_RELPATH
    assert report["subtracted_persons_total"] == pytest.approx(155.0)
    assert len(report["rows"]) == 4
    assert report["rows"][2] == {
        "KREIS": "03158", "sex": "M", "grid_age_group": "16_29", "zensus_employed_persons": 100.0,
        "students_counted_employed_persons": 90.0, "employed_target_persons": 10.0}
    assert report["smallest_employed_target"] == {
        "KREIS": "03158", "sex": "M", "grid_age_group": "16_29", "employed_target_persons": 10.0}
