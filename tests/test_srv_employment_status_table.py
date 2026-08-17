"""Tests for the SrV 2023 employment_status per-Kreis extraction (V_ERW -> P9 taxonomy).

Task 1 of the srv-employment-status-control feature: rebuilds the P9
employment_status taxonomy -- braunschweig.popsim.attributes.EMPLOYMENT_STATUS_BY_P_BKAT
values (vollzeit, teilzeit, geringfuegig, sonstiges, erwerbstaetig_unspec, in_ausbildung,
nicht_erwerbstaetig) -- on the SrV V_ERW variable, so a later task can blend it against
the MiD-side employment_status target into a per-Kreis popsim control.
"""
from __future__ import annotations

import pandas as pd

from braunschweig.popsim.attributes import EMPLOYMENT_STATUS_CATEGORIES
from scripts.extract_srv_employment_status_kreis import (
    V_ERW_TO_EMPLOYMENT_STATUS,
    build_employment_status_table,
)


def test_v_erw_mapping_is_codeplan_exact():
    """The V_ERW -> employment_status mapping must match the codeplan-verified dict
    exactly (SrV2023_Datenkodierung_SciUse.xlsx, V_ERW; verified 2026-07-13).

    Codes 6 (Schueler/in) and 7 (Student/in) map to nicht_erwerbstaetig, not
    in_ausbildung: V_ERW asks about "Taetigkeit/Erwerbstaetigkeit" (extent of
    employment), and only code 8 ("In Ausbildung, Lehre oder Umschulung", an
    apprenticeship with an employment contract) corresponds to the P_BKAT
    in_ausbildung class; full-time pupils/students without an employment
    relationship fall under the not-employed catch-all, mirroring how the MiD
    P_BKAT taxonomy separates apprenticeship from general schooling.
    """
    expected = {
        9: "vollzeit", 10: "teilzeit", 11: "geringfuegig", 8: "in_ausbildung", 70: "sonstiges",
        2: "nicht_erwerbstaetig", 3: "nicht_erwerbstaetig", 4: "nicht_erwerbstaetig",
        5: "nicht_erwerbstaetig", 6: "nicht_erwerbstaetig", 7: "nicht_erwerbstaetig",
        12: "nicht_erwerbstaetig",
    }
    assert V_ERW_TO_EMPLOYMENT_STATUS == expected


def test_build_table_shares_sum_to_one_and_have_ausbildung():
    """Feed a synthetic 6-row persons fixture, all in the ST_CODE 173 stratum, which
    the real SrV household file maps 1:1 to Kreis 03101 (Braunschweig); the pure
    builder consumes the already-resolved `kreis` column (see
    load_persons_with_kreis(), which performs that AGS/HHNR join), not ST_CODE
    directly, since ST_CODE is NOT 1:1 with Kreis in general (see the docstring of
    load_persons_with_kreis for the verified cross-tabulation).
    """
    persons = pd.DataFrame({
        "V_ERW": [9, 9, 8, 6, 3, 10],
        "V_ALTER": [40, 30, 19, 15, 70, 50],
        "GEWICHT_P_ZENSUS": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
        "ST_CODE": [173, 173, 173, 173, 173, 173],
        "kreis": ["03101"] * 6,
    })
    table = build_employment_status_table(persons)

    row = table[table["code"] == "03101"].iloc[0]
    shares = row[list(EMPLOYMENT_STATUS_CATEGORIES)].astype(float)
    assert abs(shares.sum() - 1.0) < 1e-6
    assert shares["in_ausbildung"] > 0
    assert int(row["n_unweighted"]) == 6


def test_build_table_includes_gesamt_aggregate_row():
    persons = pd.DataFrame({
        "V_ERW": [9, 8, 3, 10],
        "V_ALTER": [40, 19, 70, 50],
        "GEWICHT_P_ZENSUS": [1.0, 1.0, 1.0, 1.0],
        "kreis": ["03101", "03101", "03102", "03102"],
    })
    table = build_employment_status_table(persons)
    assert "Gesamt" in set(table["code"])
    gesamt = table[table["code"] == "Gesamt"].iloc[0]
    shares = gesamt[list(EMPLOYMENT_STATUS_CATEGORIES)].astype(float)
    assert abs(shares.sum() - 1.0) < 1e-6
    assert int(gesamt["n_unweighted"]) == 4


def test_age_and_missing_code_universe_is_enforced():
    """Persons below 14 or with a missing V_ERW code (-10/-8) must be dropped from
    the universe, not silently included or crashing the aggregation."""
    persons = pd.DataFrame({
        "V_ERW": [9, 9, -10, -8, 8],
        "V_ALTER": [40, 13, 30, 30, 25],
        "GEWICHT_P_ZENSUS": [1.0, 1.0, 1.0, 1.0, 1.0],
        "kreis": ["03101"] * 5,
    })
    table = build_employment_status_table(persons)
    row = table[table["code"] == "03101"].iloc[0]
    # Only the first (age 40, V_ERW 9) and last (age 25, V_ERW 8) rows are in-universe.
    assert int(row["n_unweighted"]) == 2
    assert row["vollzeit"] == 0.5
    assert row["in_ausbildung"] == 0.5
