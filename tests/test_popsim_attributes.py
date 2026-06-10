"""Tests for mapping MiD donor attributes to the eqasim schema (Phase 5g.3).

Mappings are grounded in the MiD 2023 codebook (Codeplaene), not invented:
P_TAET (employment), P_FSCHEIN (licence), hheink_gr1 (income groups), oek_status
(economic status), H_ANZAUTO (cars).
"""

from __future__ import annotations

import pandas as pd

from braunschweig.popsim import attributes as attr


def test_map_employed_from_p_taet():
    persons = pd.DataFrame({"P_TAET": [1, 2, 3, 7, 8, 11, 12, 99]})
    out = attr.map_employed(persons)
    # 1..7 erwerbstaetig -> True; 8 (Ausbildung), 11 (Rentner), 12 (arbeitslos) -> False.
    # 99 (keine Angabe) is now IMPUTED via missing.resolve (not silently False); with the
    # valid pool {T,T,T,T,F,F,F} and default rng(0), the imputed result is a bool, not NaN.
    assert list(out["employed"][:7]) == [True, True, True, True, False, False, False]
    assert out["employed"].iloc[7] in (True, False)
    assert out["employed"].isna().sum() == 0


def test_map_employed_handles_taet_17():
    import numpy as np

    # MiD 2023 Codeplan B1 (Personen, P_TAET): code 17 = "sonstiges" (other/
    # miscellaneous activity) -> not employment and not education -> employed=False.
    # The real MiD Personen table carries P_TAET=17 (~4,043 persons), so map_employed
    # must enumerate it explicitly rather than raising on an unenumerated code.
    df = pd.DataFrame({"P_TAET": [1, 11, 17]})
    out = attr.map_employed(df, rng=np.random.RandomState(0))
    # 1 (Angestellte/r) -> True; 11 (Rentner/in) -> False; 17 (sonstiges) -> False.
    assert out["employed"].tolist() == [True, False, False]


def test_map_has_license_from_p_fschein():
    persons = pd.DataFrame({"P_FSCHEIN": [1, 2, 9, 403]})
    out = attr.map_has_license(persons)
    # 1 -> True; 2 -> False; 403 (structural under-age) -> False.
    # 9 (keine Angabe) is imputed from the valid pool {True, False} via missing.resolve;
    # the result must be a bool, not the old silent False default.
    assert out["has_license"].iloc[0] is True or out["has_license"].iloc[0] == True
    assert out["has_license"].iloc[1] is False or out["has_license"].iloc[1] == False
    assert out["has_license"].iloc[3] is False or out["has_license"].iloc[3] == False
    assert out["has_license"].iloc[2] in (True, False)
    assert out["has_license"].isna().sum() == 0


def test_map_economic_status_from_oek_status():
    hh = pd.DataFrame({"oek_status": [1, 2, 3, 4, 5]})
    out = attr.map_economic_status(hh)
    assert list(out["economic_status"]) == [
        "very_low", "low", "medium", "high", "very_high"
    ]


def test_household_income_eur_from_group():
    hh = pd.DataFrame({"hheink_gr1": [1, 3, 15]})
    out = attr.map_household_income_eur(hh)
    # group 1 (<500) -> 250; group 3 (900-1500) -> 1200; group 15 (>7000) -> 8000.
    assert list(out["household_income_eur"]) == [250.0, 1200.0, 8000.0]


def test_map_number_of_cars_clips_missing():
    hh = pd.DataFrame({"H_ANZAUTO": [0, 2, 99]})
    out = attr.map_number_of_cars(hh)
    # 99 (keine Angabe) is now IMPUTED via missing.resolve (not silently 0). With no
    # hhgr_gr column the global valid pool {0, 2} is used; imputed value is in {0, 2}.
    assert out["number_of_cars"].iloc[0] == 0
    assert out["number_of_cars"].iloc[1] == 2
    assert out["number_of_cars"].iloc[2] in (0, 2)
    assert out["number_of_cars"].isna().sum() == 0


def test_derive_car_availability():
    assert attr.derive_car_availability(0, 2) == "none"
    assert attr.derive_car_availability(2, 2) == "all"
    assert attr.derive_car_availability(3, 2) == "all"
    assert attr.derive_car_availability(1, 2) == "some"
    assert attr.derive_car_availability(1, 0) == "all"  # no adults -> cars cover them


def test_map_has_pt_subscription_from_p_fkarte():
    # P_FKARTE 3..6 = flatrate (Deutschlandticket, Wochen/Monat ohne Abo,
    # Monat-Abo/Jahreskarte, Jobticket/Semesterticket); 1,2,7,8 not.
    # 99 (keine Angabe) is now IMPUTED via missing.resolve (not silently False); the
    # imputed result is a bool, not NaN.
    persons = pd.DataFrame({"P_FKARTE": [1, 2, 3, 4, 5, 6, 7, 8, 99]})
    out = attr.map_has_pt_subscription(persons)
    assert list(out["has_pt_subscription"][:8]) == [
        False, False, True, True, True, True, False, False
    ]
    assert out["has_pt_subscription"].iloc[8] in (True, False)
    assert out["has_pt_subscription"].isna().sum() == 0


def test_map_number_of_bicycles():
    hh = pd.DataFrame({"H_ANZRAD": [0, 3, 99]})
    out = attr.map_number_of_bicycles(hh)
    # 99 (keine Angabe) is now IMPUTED via missing.resolve (not silently 0). With no
    # hhgr_gr column the global valid pool {0, 3} is used; imputed value is in {0, 3}.
    assert out["number_of_bicycles"].iloc[0] == 0
    assert out["number_of_bicycles"].iloc[1] == 3
    assert out["number_of_bicycles"].iloc[2] in (0, 3)
    assert out["number_of_bicycles"].isna().sum() == 0


def test_derive_bicycle_availability():
    assert attr.derive_bicycle_availability(0, 3) == "none"
    assert attr.derive_bicycle_availability(3, 3) == "all"
    assert attr.derive_bicycle_availability(4, 3) == "all"
    assert attr.derive_bicycle_availability(1, 3) == "some"
