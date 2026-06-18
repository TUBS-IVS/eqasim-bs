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
    tier0_backbone_catalog,
    tier1_controls,
    tier2_controls,
    tier3_controls,
    full_catalog,
    controls_for_seed,
    render_catalog_csv,
    AGE_BANDS,
    GEO_100M,
    GEO_1KM,
    GEO_KREIS,
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


# ===========================================================================
# Lossless control-catalog reduction (over-controlling fix).
#
# The catalog (tier0_backbone_catalog / tier1_controls / tier2_controls) is the
# production source of truth (default_zensus_controls is a separate legacy
# ControlDef factory and is NOT part of this reduction). These tests pin the
# reduced structure precisely.
# ===========================================================================

_HH_TOTAL_BASE = "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj"
_POP_TOTAL_BASE = "POP_TOTAL_100m_adj"

# The 18 backbone age x sex control names (M/F x 9 ten-year bands).
_AGE_BAND_NAMES = [
    f"{sex}_AGE_{label}_agg"
    for sex in ("M", "F")
    for label, _lo, _hi in AGE_BANDS
]


def _names_by_geo(controls):
    by_geo: dict[str, set] = {}
    for c in controls:
        by_geo.setdefault(c.geography, set()).add(c.name)
    return by_geo


def test_tier0_backbone_catalog_is_20_controls() -> None:
    catalog = tier0_backbone_catalog()
    assert len(catalog) == 20


def test_tier0_backbone_geography_split_is_19_100m_and_1_1km() -> None:
    catalog = tier0_backbone_catalog()
    counts = {}
    for c in catalog:
        counts[c.geography] = counts.get(c.geography, 0) + 1
    assert counts == {GEO_100M: 19, GEO_1KM: 1}


def test_tier0_pop_total_absent_everywhere() -> None:
    # POP_TOTAL = exact sum of the 18 age x sex bands -> dropped at 100m AND 1km
    # (lossless). It is NOT kept as a 1km anchor: build_control_totals emits one shared
    # 100m source set, so a 1km-only POP source column would be missing (KeyError).
    by_geo = _names_by_geo(tier0_backbone_catalog())
    assert _POP_TOTAL_BASE not in by_geo[GEO_100M]
    assert _POP_TOTAL_BASE not in by_geo.get(GEO_1KM, set())


def test_tier0_m_f_totals_absent_everywhere() -> None:
    catalog = tier0_backbone_catalog()
    all_names = {c.name for c in catalog}
    assert "M_TOTAL" not in all_names
    assert "F_TOTAL" not in all_names


def test_tier0_hh_total_present_at_both_geographies() -> None:
    by_geo = _names_by_geo(tier0_backbone_catalog())
    assert _HH_TOTAL_BASE in by_geo[GEO_100M]
    assert _HH_TOTAL_BASE in by_geo[GEO_1KM]


def test_tier0_eighteen_age_bands_only_at_100m() -> None:
    by_geo = _names_by_geo(tier0_backbone_catalog())
    # All 18 bands present at 100m...
    for name in _AGE_BAND_NAMES:
        assert name in by_geo[GEO_100M], name
        # ...and none of them at 1km.
        assert name not in by_geo[GEO_1KM], name


def test_tier0_lossless_100m_set_is_hh_total_plus_18_bands() -> None:
    # LOSSLESS property: the dropped 100m totals (POP_TOTAL = sum of 18 bands;
    # M_TOTAL/F_TOTAL = per-sex sums) are exactly reconstructible from the kept
    # 100m set. We pin that the kept 100m set is exactly {HH_TOTAL} + the 18 bands.
    by_geo = _names_by_geo(tier0_backbone_catalog())
    assert by_geo[GEO_100M] == {_HH_TOTAL_BASE, *_AGE_BAND_NAMES}
    # And the kept 1km set is exactly the single HH_TOTAL anchor.
    assert by_geo[GEO_1KM] == {_HH_TOTAL_BASE}


def test_tier1_is_10_controls_100m_only_no_einpersonen() -> None:
    catalog = tier1_controls()
    assert len(catalog) == 10
    # 100m only.
    assert {c.geography for c in catalog} == {GEO_100M}
    names = {c.name for c in catalog}
    # einpersonen entry dropped (single-person count stays pinned via household_size==1).
    assert "EinpersHH_SingleHH_Typ_priv_HH_Familie_100m_Gitter" not in names
    # 6 household-size + 4 household-type.
    size_names = {n for n in names if "Groesse_des_privaten_Haushalts" in n}
    type_names = {n for n in names if "Typ_priv_HH_Familie" in n}
    assert len(size_names) == 6
    assert len(type_names) == 4


def test_tier2_is_5_controls_100m_only() -> None:
    catalog = tier2_controls()
    assert len(catalog) == 5
    assert {c.geography for c in catalog} == {GEO_100M}
    tenure = [c for c in catalog if "Tenure" in c.name]
    building = [c for c in catalog if "building_type" in c.name]
    assert len(tenure) == 2
    assert len(building) == 3


def test_tier3_is_7_controls_kreis_unchanged() -> None:
    catalog = tier3_controls()
    assert len(catalog) == 7
    assert {c.geography for c in catalog} == {GEO_KREIS}


def test_full_catalog_reduced_counts() -> None:
    assert len(full_catalog(("tier0",))) == 20
    assert len(full_catalog(("tier0", "tier1", "tier2"))) == 35
    assert len(full_catalog(("tier0", "tier1", "tier2", "tier3"))) == 42


def test_render_catalog_csv_full_reduced_catalog_42_rows() -> None:
    catalog = full_catalog(("tier0", "tier1", "tier2", "tier3"))
    active = controls_for_seed(catalog, "mid")
    frame = render_catalog_csv(active, "mid")
    assert list(frame.columns) == [
        "target",
        "geography",
        "seed_table",
        "importance",
        "control_field",
        "expression",
    ]
    # MiD expresses every reduced control (tier1 type / tier2 / tier3 are MiD-only
    # but MiD-expressible; only ENTD would drop them).
    assert len(frame) == 42
    # Geographies present in the rendered controls.csv.
    assert set(frame["geography"]) == {GEO_100M, GEO_1KM, GEO_KREIS}


# ===========================================================================
# Employment grid controls (Task 4).
# ===========================================================================

from braunschweig.popsim import control_spec as cs


def test_employment_grid_controls_are_six_age_sex():
    cn = {c.name: c for c in cs.employment_grid_controls()}
    assert set(cn) == {f"EMPLOYED_{s}_{g}_agg" for s in "MF" for g in ("young", "prime", "old")}
    e = cn["EMPLOYED_M_young_agg"].seed_expressions["mid"].replace(" ", "")
    assert "P_TAET.isin([1,2,3,4,6,8])" in e and "HP_SEX==1" in e
    assert "HP_ALTER>15" in e and "HP_ALTER<30" in e   # 16..29
    assert cn["EMPLOYED_F_old_agg"].seed_expressions["entd"] is None


def test_full_catalog_appends_employment_grid_only_when_requested():
    base = cs.full_catalog()
    names_off = {c.name for c in base}
    assert "EMPLOYED_M_young_agg" not in names_off
    on = cs.full_catalog(include_employment_grid=True)
    names_on = {c.name for c in on}
    assert {f"EMPLOYED_{s}_{g}_agg" for s in "MF" for g in ("young", "prime", "old")} <= names_on
    assert len(on) == len(base) + 6


def test_build_controls_df_employment_grid_flag_adds_six_controls():
    # stage.py imports cleanly under the test .venv (no yaml dependency), so the
    # build_controls_df flag is exercised directly here.
    from braunschweig.popsim.stage import build_controls_df

    off = build_controls_df(
        controls_source="catalog", tiers=("tier0",), employment_grid=False
    )
    fields_off = set(off["control_field"])
    assert "EMPLOYED_M_young_agg_ZENSUS100m" not in fields_off
    assert "EMPLOYED_F_old_agg_ZENSUS100m" not in fields_off

    on = build_controls_df(
        controls_source="catalog", tiers=("tier0",), employment_grid=True
    )
    fields_on = set(on["control_field"])
    for s in "MF":
        for g in ("young", "prime", "old"):
            assert f"EMPLOYED_{s}_{g}_agg_ZENSUS100m" in fields_on
