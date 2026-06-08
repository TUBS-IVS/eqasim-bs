"""Tests for the declarative PopulationSim control specification.

These tests pin the behaviour of ``braunschweig.popsim.control_spec``: the
typed ``ControlDef`` record, the default Zensus control set (household /
population totals, 9 ten-year age bands x sex, male/female totals), the
``controls.csv`` rendering, and the fail-fast expression validation.

Only tiny synthetic in-memory data is used; no real MiD / Zensus file is read.
"""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from braunschweig.popsim.control_spec import (
    ControlDef,
    default_zensus_controls,
    render_controls_csv,
    validate_controls,
)


def test_control_def_is_frozen_dataclass() -> None:
    control = ControlDef(
        name="total_households",
        geography="ZENSUS1km",
        seed_table="households",
        importance=1000,
        control_field="total_households",
        expression="(households.H_GEW > 0)",
    )
    assert dataclasses.is_dataclass(control)
    # Frozen: mutation must raise.
    with pytest.raises(dataclasses.FrozenInstanceError):
        control.importance = 1  # type: ignore[misc]


def test_default_controls_count_per_geography_is_22() -> None:
    controls = default_zensus_controls(geographies=("ZENSUS1km",))
    assert len(controls) == 22


def test_default_controls_total_for_two_geographies_is_44() -> None:
    controls = default_zensus_controls()
    assert len(controls) == 44
    # The two default geographies must both be represented, 22 each.
    by_geo = {}
    for control in controls:
        by_geo[control.geography] = by_geo.get(control.geography, 0) + 1
    assert by_geo == {"ZENSUS100m": 22, "ZENSUS1km": 22}


def _by_field(controls, geography):
    return {c.control_field: c for c in controls if c.geography == geography}


def test_totals_use_correct_seed_table_and_weight_expression() -> None:
    controls = default_zensus_controls(geographies=("ZENSUS1km",))
    fields = _by_field(controls, "ZENSUS1km")

    household_total = fields["total_households"]
    assert household_total.seed_table == "households"
    assert household_total.expression == "(households.H_GEW > 0)"

    population_total = fields["total_population"]
    assert population_total.seed_table == "persons"
    assert population_total.expression == "(persons.P_GEW > 0)"


def test_age_and_sex_controls_use_persons_seed_table() -> None:
    controls = default_zensus_controls(geographies=("ZENSUS1km",))
    age_sex = [
        c
        for c in controls
        if c.control_field not in {"total_households", "total_population"}
    ]
    # 18 age x sex + 2 sex totals = 20 controls on the persons table.
    assert len(age_sex) == 20
    assert all(c.seed_table == "persons" for c in age_sex)


def test_band_edge_expressions_low_middle_high() -> None:
    controls = default_zensus_controls(geographies=("ZENSUS1km",))
    fields = _by_field(controls, "ZENSUS1km")

    # 0-9 band, male: open lower edge -> only "< 10".
    assert (
        fields["age_0_9_male"].expression
        == "(persons.HP_ALTER < 10) & (persons.HP_SEX == 1)"
    )
    # 10-19 band, male: closed range ">= 10 & < 20".
    assert (
        fields["age_10_19_male"].expression
        == "(persons.HP_ALTER >= 10) & (persons.HP_ALTER < 20) & (persons.HP_SEX == 1)"
    )
    # 80+ band, female: open upper edge -> only ">= 80".
    assert (
        fields["age_80_plus_female"].expression
        == "(persons.HP_ALTER >= 80) & (persons.HP_SEX == 2)"
    )


def test_sex_value_wiring_male_and_female() -> None:
    controls = default_zensus_controls(geographies=("ZENSUS1km",))
    fields = _by_field(controls, "ZENSUS1km")

    assert fields["total_male"].expression == "(persons.HP_SEX == 1)"
    assert fields["total_female"].expression == "(persons.HP_SEX == 2)"


def test_custom_sex_values_are_respected() -> None:
    controls = default_zensus_controls(
        geographies=("ZENSUS1km",), male_value=10, female_value=20
    )
    fields = _by_field(controls, "ZENSUS1km")
    assert fields["total_male"].expression == "(persons.HP_SEX == 10)"
    assert (
        fields["age_0_9_male"].expression
        == "(persons.HP_ALTER < 10) & (persons.HP_SEX == 10)"
    )


def test_nine_age_bands_present_for_both_sexes() -> None:
    controls = default_zensus_controls(geographies=("ZENSUS1km",))
    fields = _by_field(controls, "ZENSUS1km")
    expected_bands = [
        "age_0_9",
        "age_10_19",
        "age_20_29",
        "age_30_39",
        "age_40_49",
        "age_50_59",
        "age_60_69",
        "age_70_79",
        "age_80_plus",
    ]
    for band in expected_bands:
        assert f"{band}_male" in fields, band
        assert f"{band}_female" in fields, band


def test_importance_is_configurable() -> None:
    controls = default_zensus_controls(geographies=("ZENSUS1km",), importance=7)
    assert all(c.importance == 7 for c in controls)


def test_render_controls_csv_column_order_and_row_count() -> None:
    controls = default_zensus_controls()
    frame = render_controls_csv(controls)
    assert isinstance(frame, pd.DataFrame)
    assert list(frame.columns) == [
        "target",
        "geography",
        "seed_table",
        "importance",
        "control_field",
        "expression",
    ]
    assert len(frame) == len(controls) == 44


def test_render_controls_csv_maps_name_to_target_and_control_field() -> None:
    control = ControlDef(
        name="total_households",
        geography="ZENSUS1km",
        seed_table="households",
        importance=1000,
        control_field="total_households",
        expression="(households.H_GEW > 0)",
    )
    frame = render_controls_csv([control])
    row = frame.iloc[0]
    assert row["target"] == "total_households"
    assert row["control_field"] == "total_households"
    assert row["geography"] == "ZENSUS1km"
    assert row["seed_table"] == "households"
    assert row["importance"] == 1000
    assert row["expression"] == "(households.H_GEW > 0)"


def test_validate_controls_passes_on_default_set() -> None:
    controls = default_zensus_controls()
    # Must not raise and should return the controls for chaining convenience.
    assert validate_controls(controls) == controls


def test_validate_controls_raises_on_blank_expression() -> None:
    bad = ControlDef(
        name="empty_control",
        geography="ZENSUS1km",
        seed_table="persons",
        importance=1000,
        control_field="empty_control",
        expression="   ",
    )
    with pytest.raises(ValueError) as excinfo:
        validate_controls([bad])
    assert "empty_control" in str(excinfo.value)


def test_validate_controls_raises_on_empty_expression() -> None:
    bad = ControlDef(
        name="empty_control",
        geography="ZENSUS1km",
        seed_table="persons",
        importance=1000,
        control_field="empty_control",
        expression="",
    )
    with pytest.raises(ValueError):
        validate_controls([bad])
