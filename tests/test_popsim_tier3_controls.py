import pandas as pd

from braunschweig.popsim import stage
from braunschweig.popsim.control_spec import (
    tier3_controls, full_catalog, controls_for_seed, GEO_KREIS,
)


def _select(control, persons):
    """Evaluate a control's MiD expression the way PopulationSim does: the seed
    table bound to its own name, returning the boolean selection mask."""
    return eval(control.expression_for("mid"), {"__builtins__": {}}, {"persons": persons})


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


def test_tier3_expression_classification_matches_codeplan_b1():
    # Representative seed persons covering the Codeplan B1 edge codes. The corrected
    # crosswalk (confirmed vs MiD2023 Codeplan B1) must classify them as follows.
    persons = pd.DataFrame({
        "P_TAET":   [1, 6, 7, 9, 11],   # 1,6 employed; 7=freiwilliger Wehrdienst/BFD EXCLUDED; 9 Schueler; 11 Rentner
        "bildung1": [2, 3, 4, 1, 5],    # niedrig, mittel, hoch, (noch) ohne, anderer
        "bildung2": [1, 2, 3, 5, 4],    # Berufs, Hochschul, Berufs+Hochschul, nein, anderer
    })
    by = {c.name: c for c in tier3_controls()}

    def picked(name):
        return set(persons.index[_select(by[name], persons)])

    assert picked("employed") == {0, 1}                  # P_TAET 1..6 only (7 excluded)
    assert picked("schulabschluss_low") == {0}           # bildung1 == 2 (niedrig)
    assert picked("schulabschluss_mid") == {1}           # bildung1 == 3 (mittel)
    assert picked("schulabschluss_high") == {2}          # bildung1 == 4 (hoch)
    assert picked("beruflabschluss_vocational") == {0}   # bildung2 == 1 (Berufsabschluss)
    assert picked("beruflabschluss_tertiary") == {1, 2}  # bildung2 in {2,3} (has Hochschulabschluss)
    assert picked("beruflabschluss_none") == {3}         # bildung2 == 5 (nein)


def test_tier3_schulabschluss_low_census_source_excludes_ohne():
    # low = Haupt-/Volksschule (__21) + POS (__22) only; __3 (ohne allgemeinbildenden
    # Abschluss) is dropped so MiD bildung1 (which can't cleanly isolate "ohne") and the
    # census class measure the same thing.
    by = {c.name: c for c in tier3_controls()}
    assert by["schulabschluss_low"].census_source == ("SCHULABS_STP__21", "SCHULABS_STP__22")


def test_tier3_kreis_controls_excluded_from_grid_source_and_aggregation():
    # Tier-3 controls live at KREIS geography; their census_source columns come from the
    # kreis table, NOT the grid parquet. They must not leak into the grid column load
    # (build_source_columns) or the grid aggregation_map (build_aggregation_map) -- else
    # the stage would request Kreis-census cols from the grid (spurious WARNINGs + bogus
    # all-zero grid columns).
    src = stage.build_source_columns(controls_source="catalog", seed="mid", tiers=("tier0", "tier3"))
    agg = stage.build_aggregation_map(controls_source="catalog", seed="mid", tiers=("tier0", "tier3"))
    leaked_cols = [c for c in src if any(k in c for k in ("ERWERBSTAT", "SCHULABS", "BERUFABS"))]
    assert leaked_cols == [], f"Kreis-census cols leaked into grid load: {leaked_cols}"
    tier3_names = {"employed", "schulabschluss_low", "schulabschluss_mid", "schulabschluss_high",
                   "beruflabschluss_none", "beruflabschluss_vocational", "beruflabschluss_tertiary"}
    assert tier3_names.isdisjoint(agg), f"tier3 names leaked into grid aggregation_map: {tier3_names & set(agg)}"


def test_grid_source_columns_baseline_unchanged():
    # Sanity: after the lossless reduction, the tier0-only grid load is the 19
    # backbone single-source columns: HH_TOTAL + 18 age x sex bands (all 100m).
    # POP_TOTAL and M_TOTAL / F_TOTAL are dropped (derivable sums of the bands); the
    # 1km backbone is HH_TOTAL only, so POP_TOTAL is no longer in the source union.
    src = stage.build_source_columns(controls_source="catalog", seed="mid", tiers=("tier0",),
                                     fine_teen_age_bands=False)
    assert len(src) == 19
    # With the #320 fine teen bands the two 10-19 _agg columns are replaced by the twenty
    # single-year columns they aggregated (ten per sex): 19 - 2 + 20 = 37.
    src_fine = stage.build_source_columns(controls_source="catalog", seed="mid",
                                          tiers=("tier0",))
    assert len(src_fine) == 37


def test_tier3_kreis_control_totals_columns_match_controls_csv_control_field():
    # PopulationSim looks up each KREIS control by its controls.csv control_field
    # (name_KREIS). The control_totals_KREIS.csv column names == the kreis_controls_map
    # keys, so those keys MUST equal the rendered control_fields -- else PopulationSim
    # errors "<field> not in index" (the 1-Kreis mini-run failure).
    from braunschweig.popsim.control_spec import render_catalog_csv
    active = controls_for_seed(tier3_controls(), "mid")
    expected_fields = set(render_catalog_csv(active, "mid")["control_field"])
    kmap = stage._kreis_controls_map(active)
    assert set(kmap) == expected_fields
