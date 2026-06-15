"""Tests for the Tier-2 building_type (3-class) catalog controls.

Step 2 of TDD for the Tier-2 building_type control.  Verifies that:
- building_type controls are absent from the default (tier0-only) catalog
- building_type controls are present for mid and absent for entd when tier2 is included
- each control has the correct multi-column census_source tuple
"""

from __future__ import annotations

from braunschweig.popsim import control_spec as cs


# ---------------------------------------------------------------------------
# 1. Default catalog has no building_type
# ---------------------------------------------------------------------------

def test_default_catalog_has_no_building_type() -> None:
    """full_catalog() (tier0 only) must contain no building_type controls."""
    catalog = cs.full_catalog()
    assert not any("building_type" in c.name for c in catalog), (
        "building_type controls found in tier0-only default catalog"
    )


# ---------------------------------------------------------------------------
# 2. building_type controls present in tier2
# ---------------------------------------------------------------------------

_EXPECTED_BUILDING_TYPE_NAMES = {
    "building_type_ein_zweifamilienhaus",
    "building_type_mehrfamilienhaus",
    "building_type_sonstiges",
}

_EXPECTED_EFH_SOURCES = (
    "FreiEFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
    "EFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
    "EFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
    "Freist_ZFH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
    "ZFH_DHH_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
    "ZFH_Reihenhaus_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
)
_EXPECTED_MFH_SOURCES = (
    "MFH_3bis6Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
    "MFH_7bis12Wohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
    "MFH_13undmehrWohnungen_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
)
_EXPECTED_SONSTIGES_SOURCES = (
    "AndererGebaeudetyp_Wohnung_Gebaeudetyp_Groesse_100m_Gitter",
)


def test_tier2_building_type_is_mid_only() -> None:
    """building_type controls appear for mid and are dropped for entd."""
    catalog = cs.full_catalog(include_tiers=("tier0", "tier1", "tier2"))
    mid_names = {c.name for c in cs.controls_for_seed(catalog, "mid")}
    entd_names = {c.name for c in cs.controls_for_seed(catalog, "entd")}

    for name in _EXPECTED_BUILDING_TYPE_NAMES:
        assert name in mid_names, f"{name} missing from mid controls"
        assert name not in entd_names, f"{name} incorrectly present in entd controls"


def test_tier2_building_type_six_controls_total() -> None:
    """3 classes × 2 geographies = 6 building_type controls in the catalog."""
    bt_controls = [
        c for c in cs.tier2_controls()
        if "building_type" in c.name
    ]
    assert len(bt_controls) == 6, (
        f"Expected 6 building_type controls (3 × 2 geo), got {len(bt_controls)}"
    )


def test_tier2_building_type_covers_both_geographies() -> None:
    """Each building_type class appears at both GEO_100M and GEO_1KM."""
    bt_controls = [c for c in cs.tier2_controls() if "building_type" in c.name]
    for base_name in _EXPECTED_BUILDING_TYPE_NAMES:
        geos = {c.geography for c in bt_controls if c.name == base_name}
        assert cs.GEO_100M in geos, f"{base_name} missing GEO_100M"
        assert cs.GEO_1KM in geos, f"{base_name} missing GEO_1KM"


def test_tier2_building_type_census_sources_ein_zweifamilienhaus() -> None:
    """ein_zweifamilienhaus census_source has all 6 expected source columns."""
    bt_controls = [c for c in cs.tier2_controls() if "building_type" in c.name]
    efh = next(
        c for c in bt_controls
        if c.name == "building_type_ein_zweifamilienhaus" and c.geography == cs.GEO_100M
    )
    assert set(efh.census_source) == set(_EXPECTED_EFH_SOURCES), (
        f"ein_zweifamilienhaus census_source mismatch:\n"
        f"  expected: {sorted(_EXPECTED_EFH_SOURCES)}\n"
        f"  got:      {sorted(efh.census_source)}"
    )


def test_tier2_building_type_census_sources_mehrfamilienhaus() -> None:
    """mehrfamilienhaus census_source has all 3 expected source columns."""
    bt_controls = [c for c in cs.tier2_controls() if "building_type" in c.name]
    mfh = next(
        c for c in bt_controls
        if c.name == "building_type_mehrfamilienhaus" and c.geography == cs.GEO_100M
    )
    assert set(mfh.census_source) == set(_EXPECTED_MFH_SOURCES), (
        f"mehrfamilienhaus census_source mismatch:\n"
        f"  expected: {sorted(_EXPECTED_MFH_SOURCES)}\n"
        f"  got:      {sorted(mfh.census_source)}"
    )


def test_tier2_building_type_census_sources_sonstiges() -> None:
    """sonstiges census_source has exactly 1 expected source column."""
    bt_controls = [c for c in cs.tier2_controls() if "building_type" in c.name]
    sonst = next(
        c for c in bt_controls
        if c.name == "building_type_sonstiges" and c.geography == cs.GEO_100M
    )
    assert set(sonst.census_source) == set(_EXPECTED_SONSTIGES_SOURCES), (
        f"sonstiges census_source mismatch:\n"
        f"  expected: {sorted(_EXPECTED_SONSTIGES_SOURCES)}\n"
        f"  got:      {sorted(sonst.census_source)}"
    )


def test_tier2_building_type_mid_seed_expressions() -> None:
    """MiD seed expressions use the correct haustyp codings."""
    bt_controls = [c for c in cs.tier2_controls() if "building_type" in c.name]

    # ein_zweifamilienhaus: haustyp == 1
    efh = next(c for c in bt_controls if c.name == "building_type_ein_zweifamilienhaus")
    assert efh.expression_for("mid") is not None
    assert "haustyp" in efh.expression_for("mid")
    assert "1" in efh.expression_for("mid")

    # mehrfamilienhaus: haustyp in [2, 3]
    mfh = next(c for c in bt_controls if c.name == "building_type_mehrfamilienhaus")
    assert mfh.expression_for("mid") is not None
    assert "haustyp" in mfh.expression_for("mid")

    # sonstiges: haustyp == 4
    sonst = next(c for c in bt_controls if c.name == "building_type_sonstiges")
    assert sonst.expression_for("mid") is not None
    assert "haustyp" in sonst.expression_for("mid")
    assert "4" in sonst.expression_for("mid")


def test_tier2_building_type_entd_expression_is_none() -> None:
    """All building_type controls must have entd expression = None."""
    bt_controls = [c for c in cs.tier2_controls() if "building_type" in c.name]
    for ctrl in bt_controls:
        assert ctrl.expression_for("entd") is None, (
            f"{ctrl.name}: expected entd expression None, got {ctrl.expression_for('entd')!r}"
        )


def test_tier2_building_type_seed_table_is_households() -> None:
    """All building_type controls reference the households seed table."""
    bt_controls = [c for c in cs.tier2_controls() if "building_type" in c.name]
    for ctrl in bt_controls:
        assert ctrl.seed_table == cs.SEED_TABLE_HOUSEHOLDS, (
            f"{ctrl.name}: expected seed_table 'households', got {ctrl.seed_table!r}"
        )


def test_tier2_building_type_importance_is_1000() -> None:
    """All building_type controls have importance=1000."""
    bt_controls = [c for c in cs.tier2_controls() if "building_type" in c.name]
    for ctrl in bt_controls:
        assert ctrl.importance == 1000


# ---------------------------------------------------------------------------
# Regression: existing tier2 tenure controls still present
# ---------------------------------------------------------------------------

def test_tier2_still_has_tenure_controls() -> None:
    """Tenure controls must still be present after adding building_type."""
    catalog = cs.full_catalog(include_tiers=("tier0", "tier2"))
    mid_names = {c.name for c in cs.controls_for_seed(catalog, "mid")}
    assert "EigentuemerHH_Tenure_100m_Gitter" in mid_names
    assert "MieterHH_Tenure_100m_Gitter" in mid_names
