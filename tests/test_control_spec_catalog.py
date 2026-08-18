"""Tests for CatalogControl and its per-seed expression dispatch."""

from __future__ import annotations

from braunschweig.popsim import control_spec as cs


def test_catalogcontrol_carries_per_seed_expressions_and_geo_constants() -> None:
    # New geography constants exist for the multi-geo tiers.
    assert cs.GEO_KREIS == "KREIS"
    assert cs.GEO_GEMEINDE == "GEMEINDE"

    cd = cs.CatalogControl(
        name="household_size_1",
        geography=cs.GEO_100M,
        seed_table=cs.SEED_TABLE_HOUSEHOLDS,
        importance=1000,
        census_source=("hh_size_1",),
        seed_expressions={"mid": "(households.H_GR == 1)", "entd": "(households.H_GR == 1)", "ipf": None},
    )
    assert cd.expression_for("mid") == "(households.H_GR == 1)"
    assert cd.expression_for("entd") == "(households.H_GR == 1)"
    assert cd.expression_for("ipf") is None
    assert cd.expression_for("unknown") is None


# ---------------------------------------------------------------------------
# Task 3: controls_for_seed workflow filter
# ---------------------------------------------------------------------------

import logging


def _two_control_catalog():
    return [
        cs.CatalogControl(
            name="household_size_1", geography=cs.GEO_100M,
            seed_table=cs.SEED_TABLE_HOUSEHOLDS, importance=1000,
            census_source=("hh_size_1",),
            seed_expressions={"mid": "(households.H_GR == 1)", "entd": "(households.H_GR == 1)"},
        ),
        cs.CatalogControl(
            name="building_type_mfh", geography=cs.GEO_1KM,
            seed_table=cs.SEED_TABLE_HOUSEHOLDS, importance=1000,
            census_source=("bldg_mfh",),
            seed_expressions={"mid": "(households.haustyp.isin([2, 3]))", "entd": None},
        ),
    ]


def test_controls_for_seed_filters_and_warns_on_drop(caplog) -> None:
    catalog = _two_control_catalog()
    with caplog.at_level(logging.WARNING):
        mid = cs.controls_for_seed(catalog, "mid")
        entd = cs.controls_for_seed(catalog, "entd")
    assert {c.name for c in mid} == {"household_size_1", "building_type_mfh"}
    assert {c.name for c in entd} == {"household_size_1"}
    # ENTD drops building_type_mfh -> exactly one WARNING naming the control + seed.
    drops = [r for r in caplog.records if "building_type_mfh" in r.message and "entd" in r.message]
    assert len(drops) == 1


# ---------------------------------------------------------------------------
# Task 4: tier0_backbone_catalog + render_catalog_csv
# ---------------------------------------------------------------------------


def test_tier0_render_reproduces_production_baseline() -> None:
    """The committed fixture is the PRE-#320 control set, so it is the flag-OFF baseline.

    With the fine teen bands disabled the rendered catalog must still be byte-identical
    to it (flag-OFF byte-identity, repository convention).
    """
    import pandas as pd
    baseline = pd.read_csv("tests/fixtures/prep3_controls_baseline.csv", sep=";")
    catalog = cs.tier0_backbone_catalog(fine_teen_age_bands=False)
    rendered = cs.render_catalog_csv(cs.controls_for_seed(catalog, "mid"), "mid")
    key = ["target", "geography", "seed_table", "importance", "control_field", "expression"]
    left = baseline[key].sort_values(key).reset_index(drop=True)
    right = rendered[key].sort_values(key).reset_index(drop=True)
    pd.testing.assert_frame_equal(left, right, check_dtype=False)


def test_tier1_household_size_present_for_both_seeds() -> None:
    catalog = cs.full_catalog(include_tiers=("tier0", "tier1"))
    mid = {c.name for c in cs.controls_for_seed(catalog, "mid")}
    entd = {c.name for c in cs.controls_for_seed(catalog, "entd")}
    bases = [
        "1_Person_Groesse_des_privaten_Haushalts_100m_Gitter",
        "2_Personen_Groesse_des_privaten_Haushalts_100m_Gitter",
        "3_Personen_Groesse_des_privaten_Haushalts_100m_Gitter",
        "4_Personen_Groesse_des_privaten_Haushalts_100m_Gitter",
        "5_Personen_Groesse_des_privaten_Haushalts_100m_Gitter",
        "6_Personen_und_mehr_Groesse_des_privaten_Haushalts_100m_Gitter",
    ]
    for b in bases:
        assert b in mid and b in entd


def test_default_catalog_is_tier0_only() -> None:
    default = cs.full_catalog()
    # Tier-1 names follow the pattern "<N>_Person(en)_Groesse_des_privaten_Haushalts_100m_Gitter"
    # (no "_adj" suffix, no "Insgesamt_Haushalte_" prefix).  The Tier-0 household
    # total "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj"
    # must NOT be confused with a Tier-1 control; the discriminator is the leading
    # digit (1-6) in Tier-1 names.
    tier1_names = {c.name for c in cs.tier1_controls()}
    assert not any(c.name in tier1_names for c in default)


# ---------------------------------------------------------------------------
# Task 8: Tier-1 household_type (Lebensform/Familie 5-class) — MiD-only
# ---------------------------------------------------------------------------


def test_tier1_household_type_is_mid_only() -> None:
    catalog = cs.full_catalog(include_tiers=("tier0", "tier1"))
    mid = {c.name for c in cs.controls_for_seed(catalog, "mid")}
    entd = {c.name for c in cs.controls_for_seed(catalog, "entd")}
    # LOSSLESS reduction: "einpersonen" is dropped (exact residual of the partition;
    # single-person count stays pinned by the household-size control H_GR == 1).
    bases = [
        "Paare_ohneKind_Typ_priv_HH_Familie_100m_Gitter",
        "Paare_mitKind_Typ_priv_HH_Familie_100m_Gitter",
        "Alleinerziehende_Typ_priv_HH_Familie_100m_Gitter",
        "MehrpersHHohneKernfam_Typ_priv_HH_Familie_100m_Gitter",
    ]
    for b in bases:
        assert b in mid          # MiD can express household type
        assert b not in entd     # ENTD drops it (composition differs)
    # The dropped einpersonen control must not appear for either seed.
    assert "EinpersHH_SingleHH_Typ_priv_HH_Familie_100m_Gitter" not in mid
    assert "EinpersHH_SingleHH_Typ_priv_HH_Familie_100m_Gitter" not in entd


# ---------------------------------------------------------------------------
# Task 9: Tier-2 tenure (owner / renter, MiD H_MIETE) — MiD-only
# ---------------------------------------------------------------------------


def test_tier2_tenure_is_mid_only() -> None:
    catalog = cs.full_catalog(include_tiers=("tier0", "tier1", "tier2"))
    mid = {c.name for c in cs.controls_for_seed(catalog, "mid")}
    entd = {c.name for c in cs.controls_for_seed(catalog, "entd")}
    for b in ("EigentuemerHH_Tenure_100m_Gitter", "MieterHH_Tenure_100m_Gitter"):
        assert b in mid and b not in entd


def test_default_catalog_has_no_tenure() -> None:
    assert all("Tenure" not in c.name for c in cs.full_catalog())


# ---------------------------------------------------------------------------
# Issue #320: fine teen age bands (10-15 / 16-17 / 18-19) in the tier0 backbone
# ---------------------------------------------------------------------------


def test_tier0_splits_the_teen_band_at_the_published_bin_edges() -> None:
    """The 10-19 band is replaced by 10-15 / 16-17 / 18-19 per sex.

    The backbone controls age in nine ten-year bands, so the composition INSIDE a band
    is unconstrained. Measured on the 100% population (issue #307): 15-17 is +64% and
    18-19 is -75% against DESTATIS 12411-0018 (5,297 synthetic persons against 21,582),
    while the 10-19 total is fine. The two new edges sit on published Zensus bins (the
    5-class Unter18 and the INFR a16bis18), so the targets rest on published data.

    The old band is REPLACED, not kept alongside: the three new controls sum to it
    exactly (verified bit-for-bit on the cell parquet), so keeping it would re-introduce
    precisely the derivable redundancy the tier0 reduction removed.
    """
    catalog = cs.tier0_backbone_catalog()
    names = {c.name for c in catalog if c.geography == cs.GEO_100M}
    for sex in ("M", "F"):
        for band in ("10_15", "16_17", "18_19"):
            assert f"{sex}_AGE_{band}_agg" in names
        assert f"{sex}_AGE_10_19_agg" not in names
    # 1 household total + 22 age x sex controls (11 bands x 2 sexes).
    assert len([c for c in catalog if c.geography == cs.GEO_100M]) == 23


def test_fine_teen_controls_aggregate_the_single_year_census_columns() -> None:
    """The new bands have no precomputed ``_agg`` column in the cell parquet, so their
    census source is the tuple of single-year columns; the row-sum is done by
    ``prepared_cells.add_aggregated_controls`` (the existing multi-source mechanism).
    The untouched ten-year bands stay single-source identities."""
    by_name = {c.name: c for c in cs.tier0_backbone_catalog()
               if c.geography == cs.GEO_100M}
    assert by_name["M_AGE_18_19_agg"].census_source == ("M_AGE_18", "M_AGE_19")
    assert by_name["F_AGE_16_17_agg"].census_source == ("F_AGE_16", "F_AGE_17")
    assert by_name["M_AGE_10_15_agg"].census_source == tuple(
        f"M_AGE_{year}" for year in range(10, 16))
    assert by_name["M_AGE_20_29_agg"].census_source == ("M_AGE_20_29_agg",)


def test_fine_teen_band_expressions_select_exactly_their_ages() -> None:
    """The seed expression must match the census column, or the control compares two
    different populations."""
    by_name = {c.name: c for c in cs.tier0_backbone_catalog()
               if c.geography == cs.GEO_100M}
    assert by_name["M_AGE_18_19_agg"].seed_expressions["mid"] == (
        "(persons.HP_ALTER > 17)&(persons.HP_ALTER < 20)&(persons.HP_SEX==1)")
    assert by_name["F_AGE_10_15_agg"].seed_expressions["mid"] == (
        "(persons.HP_ALTER > 9)&(persons.HP_ALTER < 16)&(persons.HP_SEX==2)")
