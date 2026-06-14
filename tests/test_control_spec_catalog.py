from braunschweig.popsim import control_spec as cs


def test_controldef_carries_per_seed_expressions_and_geo_constants():
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
