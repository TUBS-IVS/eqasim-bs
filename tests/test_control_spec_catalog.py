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
