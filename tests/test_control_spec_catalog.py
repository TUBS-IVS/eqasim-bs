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
    import pandas as pd
    baseline = pd.read_csv("tests/fixtures/prep3_controls_baseline.csv", sep=";")
    catalog = cs.tier0_backbone_catalog()
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
