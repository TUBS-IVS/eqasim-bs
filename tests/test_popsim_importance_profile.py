"""Tests for the PopulationSim per-control importance profile (control_spec).

The "optimized_2026_06_30" profile encodes the result of the offline per-group
importance search. These tests pin: the group classifier (rendered control_field
prefixes), that "uniform" is a no-op, that the optimized profile sets the expected
weights per group, that unknown profiles fail fast, and that build_controls_df
passes the profile through.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.popsim import control_spec as cs  # noqa: E402


def _sample_controls() -> pd.DataFrame:
    """A controls.csv-shaped frame covering every importance group (rendered names)."""
    fields = [
        "Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj_ZENSUS100m",  # anchor
        "M_AGE_0_9_agg_ZENSUS100m",                          # age
        "F_AGE_80_plus_agg_ZENSUS100m",                      # age
        "1_Person_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m",   # size15
        "5_Personen_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m", # size15
        "6_Personen_und_mehr_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m",  # six
        "Paare_ohneKind_Typ_priv_HH_Familie_100m_Gitter_ZENSUS100m",        # hhtype
        "EigentuemerHH_Tenure_100m_Gitter_ZENSUS100m",       # tenure
        "building_type_ein_zweifamilienhaus_ZENSUS100m",     # bld
        "building_type_sonstiges_ZENSUS100m",                # son
        "EMPLOYED_M_16_29_agg_ZENSUS100m",                   # employed
        "employed_KREIS",                                    # employed
        "schulabschluss_low_KREIS",                          # edu
    ]
    return pd.DataFrame({
        "target": [f + "_target" for f in fields],
        "geography": ["ZENSUS100m"] * len(fields),
        "seed_table": ["households"] * len(fields),
        "importance": [1000] * len(fields),
        "control_field": fields,
        "expression": ["(households.H_GEW > 0)"] * len(fields),
    })


def test_group_classifier_matches_optimizer_prefixes():
    g = cs.importance_group_for_field
    assert g("Insgesamt_Haushalte_x_ZENSUS100m") == "anchor"
    assert g("6_Personen_und_mehr_x_ZENSUS100m") == "six"
    assert g("building_type_sonstiges_ZENSUS100m") == "son"
    assert g("M_AGE_30_39_agg_ZENSUS100m") == "age"
    assert g("Alleinerziehende_Typ_priv_HH_Familie_100m_Gitter_ZENSUS100m") == "hhtype"
    assert g("MieterHH_Tenure_100m_Gitter_ZENSUS100m") == "tenure"
    assert g("3_Personen_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m") == "size15"
    assert g("building_type_mehrfamilienhaus_ZENSUS100m") == "bld"
    assert g("EMPLOYED_F_60plus_agg_ZENSUS100m") == "employed"
    assert g("employed_KREIS") == "employed"
    assert g("beruflabschluss_tertiary_KREIS") == "edu"
    assert g("something_else") == "other"


def test_uniform_profile_is_noop():
    df = _sample_controls()
    out = cs.apply_importance_profile(df, "uniform")
    assert (out["importance"] == 1000).all()
    # byte-identical frame
    pd.testing.assert_frame_equal(out, df)


def test_optimized_profile_sets_expected_weights():
    df = _sample_controls()
    out = cs.apply_importance_profile(df, "optimized_2026_06_30")
    by = dict(zip(out["control_field"], out["importance"]))
    assert by["Insgesamt_Haushalte_Groesse_des_privaten_Haushalts_100m_Gitter_adj_ZENSUS100m"] == 1_000_000_000
    assert by["M_AGE_0_9_agg_ZENSUS100m"] == 200
    assert by["1_Person_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m"] == 500
    assert by["6_Personen_und_mehr_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m"] == 2000
    assert by["Paare_ohneKind_Typ_priv_HH_Familie_100m_Gitter_ZENSUS100m"] == 200
    assert by["building_type_sonstiges_ZENSUS100m"] == 20000
    assert by["EMPLOYED_M_16_29_agg_ZENSUS100m"] == 2000
    # groups NOT in the profile keep the uniform 1000 (tenure, bld, edu)
    assert by["EigentuemerHH_Tenure_100m_Gitter_ZENSUS100m"] == 1000
    assert by["building_type_ein_zweifamilienhaus_ZENSUS100m"] == 1000
    assert by["schulabschluss_low_KREIS"] == 1000


def test_unknown_profile_raises():
    df = _sample_controls()
    try:
        cs.apply_importance_profile(df, "does_not_exist")
    except KeyError:
        return
    raise AssertionError("expected KeyError for unknown profile")


def test_build_controls_df_passes_profile_through():
    # Imported lazily: braunschweig.popsim.stage pulls numba via sources->stratum,
    # which is unavailable in the local shadowed env (run on the server). Skip there.
    try:
        from braunschweig.popsim import stage
    except Exception as exc:  # pragma: no cover - env-dependent
        import pytest
        pytest.skip(f"stage import unavailable locally: {exc}")
    # catalog source + optimized profile -> sonstiges control weighted up.
    df = stage.build_controls_df(
        controls_source="catalog", seed="mid",
        tiers=("tier0", "tier1", "tier2"),
        importance_profile="optimized_2026_06_30",
    )
    son = df[df["control_field"].str.startswith("building_type_sonstiges")]
    assert len(son) >= 1
    assert (son["importance"] == 20000).all()
    # default uniform leaves it at 1000
    df_u = stage.build_controls_df(
        controls_source="catalog", seed="mid", tiers=("tier0", "tier1", "tier2"),
    )
    son_u = df_u[df_u["control_field"].str.startswith("building_type_sonstiges")]
    assert (son_u["importance"] == 1000).all()


def test_kreis_attribute_controls_map_to_tier_groups_with_hard_raised():
    """KREIS attribute controls: registry tier -> importance group. Under the optimized
    profile the HARD entries (economic_status, number_of_cars) are raised to 2000 (par
    with the Kreis-scale "employed" group); SOFT entries (bikes/ebike/trip_class) have no
    profile entry and keep the uniform 1000 (yield gracefully in small cells)."""
    import pandas as pd
    from braunschweig.popsim.control_spec import (
        IMPORTANCE_PROFILES, apply_importance_profile, importance_group_for_field,
    )

    assert importance_group_for_field("economic_status_very_low_KREIS") == "kreis_hard"
    assert importance_group_for_field("number_of_cars_3plus_KREIS") == "kreis_hard"
    assert importance_group_for_field("number_of_bicycles_4plus_KREIS") == "kreis_soft"
    assert importance_group_for_field("has_ebike_yes_KREIS") == "kreis_soft"
    assert importance_group_for_field("trip_class_0_KREIS") == "kreis_soft"
    assert IMPORTANCE_PROFILES["optimized_2026_06_30"]["kreis_hard"] == 2000
    assert "kreis_soft" not in IMPORTANCE_PROFILES["optimized_2026_06_30"]

    frame = pd.DataFrame({
        "control_field": [
            "economic_status_high_KREIS", "number_of_cars_0_KREIS",
            "trip_class_5plus_KREIS", "has_ebike_no_KREIS",
        ],
        "importance": [1000, 1000, 1000, 1000],
    })
    out = apply_importance_profile(frame, "optimized_2026_06_30")
    assert out["importance"].tolist() == [2000, 2000, 1000, 1000]
