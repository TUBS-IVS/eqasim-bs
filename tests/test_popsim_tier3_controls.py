from braunschweig.popsim.control_spec import (
    tier3_controls, full_catalog, controls_for_seed, GEO_KREIS,
)


def test_tier3_controls_kreis_mid_only():
    cat = tier3_controls()
    names = {c.name for c in cat}
    assert {"employed", "schulabschluss_low", "schulabschluss_mid", "schulabschluss_high",
            "beruflabschluss_none", "beruflabschluss_vocational", "beruflabschluss_tertiary"} <= names
    assert all(c.geography == GEO_KREIS for c in cat)
    assert all(c.seed_table == "persons" for c in cat)
    # MiD expresses all; ENTD drops all (entd=None)
    assert len(controls_for_seed(cat, "mid")) == len(cat)
    assert len(controls_for_seed(cat, "entd")) == 0


def test_full_catalog_includes_tier3():
    base = {c.name for c in full_catalog(("tier0",))}
    with3 = {c.name for c in full_catalog(("tier0", "tier3"))}
    assert "employed" in with3 and "employed" not in base


def test_tier3_expressions_use_raw_seed_columns():
    # Control expressions must reference RAW MiD cols (retained on the seed), not derived
    # attributes -- PopulationSim evaluates them over seed_persons (P_TAET/bildung1/bildung2).
    exprs = " ".join(c.expression_for("mid") for c in tier3_controls())
    assert "persons.P_TAET" in exprs
    assert "persons.bildung1" in exprs
    assert "persons.bildung2" in exprs
    # No derived-attribute references (those won't exist on the seed):
    assert "schulabschluss" not in exprs
    assert "beruflabschluss" not in exprs
    assert "persons.employed" not in exprs
