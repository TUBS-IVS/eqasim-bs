"""Tests for all attribute mappers routing through missing.resolve.

Verifies that item-nonresponse codes (9, 99) are IMPUTED (not silently mapped to a
fixed default) and that structural codes (403, 404, 202, 402, 206) are resolved
deterministically, using the uniform missing policy from ``braunschweig.popsim.missing``.

Covers: map_has_license, map_number_of_cars (Task 2) and map_employed, map_economic_status,
map_household_income, map_household_income_eur, map_has_pt_subscription,
map_number_of_bicycles (Task 2b).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from braunschweig.popsim import attributes as a


def test_has_license_structural_child_is_false_nonresponse_imputed():
    persons = pd.DataFrame({
        "P_FSCHEIN": [1, 2, 403, 9],
        "alter_gr1": [3, 3, 1, 3],
    })
    out = a.map_has_license(persons, rng=np.random.RandomState(0))
    # code 1 -> True, code 2 -> False, code 403 (structural under-age) -> False
    assert out["has_license"].tolist()[:3] == [True, False, False]
    # code 9 is nonresponse: must be imputed to a bool (True or False), never NaN
    assert out["has_license"].tolist()[3] in (True, False)
    assert out["has_license"].isna().sum() == 0


def test_number_of_cars_missing_is_imputed_not_silently_zero():
    households = pd.DataFrame({"H_ANZAUTO": [0, 2, 99], "hhgr_gr": [1, 2, 2]})
    out = a.map_number_of_cars(households, rng=np.random.RandomState(0))
    # 99 (keine Angabe) must be imputed, not silently set to 0
    assert out["number_of_cars"].isna().sum() == 0
    assert (out["number_of_cars"] >= 0).all()
    # the imputed value for row 2 (hhgr_gr=2, same as row 1 with cars=2) should
    # be drawn from the valid pool for hhgr_gr=2 -> only valid value is 2
    assert out["number_of_cars"].iloc[2] == 2


def test_license_adult_coverage_code_is_imputed_not_false():
    import numpy as np, pandas as pd
    from braunschweig.popsim import attributes
    df = pd.DataFrame({
        "P_FSCHEIN": [1, 1, 1, 1, 202, 404],
        "alter_gr1": [5, 5, 5, 5, 5, 5],
    })
    out = attributes.map_has_license(df, rng=np.random.RandomState(0))
    assert out["has_license"].tolist() == [True, True, True, True, True, True]


def test_license_underage_code_403_is_false():
    import numpy as np, pandas as pd
    from braunschweig.popsim import attributes
    df = pd.DataFrame({"P_FSCHEIN": [1, 403], "alter_gr1": [5, 1]})
    out = attributes.map_has_license(df, rng=np.random.RandomState(0))
    assert out["has_license"].tolist() == [True, False]


def test_has_license_no_rng_backward_compatible():
    """Callers that omit rng must not raise; default rng is applied."""
    persons = pd.DataFrame({"P_FSCHEIN": [1, 2, 9]})
    out = a.map_has_license(persons)  # no rng -> must not raise
    assert out["has_license"].isna().sum() == 0


def test_number_of_cars_no_rng_backward_compatible():
    """Callers that omit rng must not raise; default rng is applied."""
    households = pd.DataFrame({"H_ANZAUTO": [0, 1, 99]})
    out = a.map_number_of_cars(households)  # no rng -> must not raise
    assert out["number_of_cars"].isna().sum() == 0
    assert (out["number_of_cars"] >= 0).all()


# ---------------------------------------------------------------------------
# Task 2b: employment, economic status, income, PT subscription, bicycles
# ---------------------------------------------------------------------------

def test_employed_valid_codes_map_to_existing_semantics():
    """Codes 1..7 map to True, 8..16 to False; no NaN in output."""
    persons = pd.DataFrame({"P_TAET": [1, 7, 8, 16]})
    out = a.map_employed(persons, rng=np.random.RandomState(0))
    assert out["employed"].tolist() == [True, True, False, False]
    assert out["employed"].isna().sum() == 0


def test_employed_nonresponse_is_imputed():
    """P_TAET=99 (keine Angabe) must be imputed from the valid pool, never NaN."""
    persons = pd.DataFrame({"P_TAET": [1, 2, 8, 99], "alter_gr1": [3, 3, 3, 3]})
    out = a.map_employed(persons, rng=np.random.RandomState(0))
    assert out["employed"].iloc[3] in (True, False)
    assert out["employed"].isna().sum() == 0


def test_employed_no_rng_backward_compatible():
    """Callers that omit rng must not raise; default rng is applied."""
    persons = pd.DataFrame({"P_TAET": [1, 8, 99]})
    out = a.map_employed(persons)
    assert out["employed"].isna().sum() == 0


def test_economic_status_valid_codes_map_correctly():
    """oek_status 1..5 map to the existing class strings; no NaN in output."""
    hh = pd.DataFrame({"oek_status": [1, 2, 3, 4, 5]})
    out = a.map_economic_status(hh, rng=np.random.RandomState(0))
    assert list(out["economic_status"]) == ["very_low", "low", "medium", "high", "very_high"]
    assert out["economic_status"].isna().sum() == 0


def test_economic_status_nonresponse_imputed_within_group():
    """oek_status=9 is imputed from the valid pool in the same hhgr_gr group."""
    hh = pd.DataFrame({
        "oek_status": [3, 4, 9],
        "hhgr_gr": [2, 2, 2],
    })
    out = a.map_economic_status(hh, rng=np.random.RandomState(0))
    # imputed value must be a valid class string, not None/NaN
    assert out["economic_status"].iloc[2] in ("very_low", "low", "medium", "high", "very_high")
    assert out["economic_status"].isna().sum() == 0


def test_economic_status_no_rng_backward_compatible():
    """Callers that omit rng must not raise."""
    hh = pd.DataFrame({"oek_status": [1, 9]})
    out = a.map_economic_status(hh)
    assert out["economic_status"].isna().sum() == 0


def test_household_income_valid_codes_map_correctly():
    """hheink_gr1 1..15 map to the existing class strings; no NaN for valid codes."""
    hh = pd.DataFrame({"hheink_gr1": [1, 3, 15]})
    out = a.map_household_income(hh, rng=np.random.RandomState(0))
    assert list(out["household_income"]) == ["under_500", "900_1500", "over_7000"]
    assert out["household_income"].isna().sum() == 0


def test_household_income_nonresponse_imputed():
    """hheink_gr1=99 is imputed from the valid pool in the same hhgr_gr group."""
    hh = pd.DataFrame({
        "hheink_gr1": [3, 5, 99],
        "hhgr_gr": [2, 2, 2],
    })
    out = a.map_household_income(hh, rng=np.random.RandomState(0))
    assert out["household_income"].iloc[2] in (
        "under_500", "500_900", "900_1500", "1500_2000", "2000_2600",
        "2600_3000", "3000_3600", "3600_4000", "4000_4600", "4600_5000",
        "5000_5600", "5600_6000", "6000_6600", "6600_7000", "over_7000",
    )
    assert out["household_income"].isna().sum() == 0


def test_household_income_eur_valid_codes_map_correctly():
    """hheink_gr1 1..15 map to EUR midpoints; no NaN for valid codes."""
    hh = pd.DataFrame({"hheink_gr1": [1, 3, 15]})
    out = a.map_household_income_eur(hh, rng=np.random.RandomState(0))
    assert list(out["household_income_eur"]) == [250.0, 1200.0, 8000.0]
    assert out["household_income_eur"].isna().sum() == 0


def test_household_income_eur_nonresponse_imputed():
    """hheink_gr1=99 is imputed from the valid pool, producing a numeric EUR value."""
    hh = pd.DataFrame({
        "hheink_gr1": [3, 5, 99],
        "hhgr_gr": [2, 2, 2],
    })
    out = a.map_household_income_eur(hh, rng=np.random.RandomState(0))
    assert pd.notna(out["household_income_eur"].iloc[2])
    assert out["household_income_eur"].iloc[2] > 0.0
    assert out["household_income_eur"].isna().sum() == 0


def test_pt_subscription_structural_and_nonresponse():
    """P_FKARTE structural (402 = Kind<14) resolves to False; 99 is imputed."""
    persons = pd.DataFrame({"P_FKARTE": [3, 8, 402, 99], "alter_gr1": [3, 3, 1, 3]})
    out = a.map_has_pt_subscription(persons, rng=np.random.RandomState(0))
    assert out["has_pt_subscription"].tolist()[:3] == [True, False, False]
    assert out["has_pt_subscription"].isna().sum() == 0


def test_pt_subscription_structural_202_206_resolve_to_false():
    """Structural codes 202 (PAPI) and 206 (Proxy) must resolve deterministically to False."""
    persons = pd.DataFrame({"P_FKARTE": [5, 202, 206]})
    out = a.map_has_pt_subscription(persons, rng=np.random.RandomState(0))
    assert out["has_pt_subscription"].tolist() == [True, False, False]
    assert out["has_pt_subscription"].isna().sum() == 0


def test_pt_subscription_nonresponse_imputed():
    """P_FKARTE=99 (keine Angabe) is imputed to a bool, never NaN."""
    persons = pd.DataFrame({
        "P_FKARTE": [3, 8, 99],
        "alter_gr1": [2, 2, 2],
    })
    out = a.map_has_pt_subscription(persons, rng=np.random.RandomState(0))
    assert out["has_pt_subscription"].iloc[2] in (True, False)
    assert out["has_pt_subscription"].isna().sum() == 0


def test_pt_subscription_no_rng_backward_compatible():
    """Callers that omit rng must not raise."""
    persons = pd.DataFrame({"P_FKARTE": [3, 99]})
    out = a.map_has_pt_subscription(persons)
    assert out["has_pt_subscription"].isna().sum() == 0


def test_number_of_bicycles_valid_codes_map_correctly():
    """H_ANZRAD 0..10 map identity; no NaN for valid codes."""
    hh = pd.DataFrame({"H_ANZRAD": [0, 3, 10]})
    out = a.map_number_of_bicycles(hh, rng=np.random.RandomState(0))
    assert list(out["number_of_bicycles"]) == [0, 3, 10]
    assert out["number_of_bicycles"].isna().sum() == 0


def test_number_of_bicycles_missing_is_imputed_not_silently_zero():
    """H_ANZRAD=99 is imputed from the valid pool in the same hhgr_gr group, not forced to 0."""
    hh = pd.DataFrame({"H_ANZRAD": [0, 2, 99], "hhgr_gr": [1, 2, 2]})
    out = a.map_number_of_bicycles(hh, rng=np.random.RandomState(0))
    assert out["number_of_bicycles"].isna().sum() == 0
    assert (out["number_of_bicycles"] >= 0).all()
    # hhgr_gr=2 valid pool contains only value 2 -> imputed value must be 2
    assert out["number_of_bicycles"].iloc[2] == 2


def test_number_of_bicycles_no_rng_backward_compatible():
    """Callers that omit rng must not raise."""
    hh = pd.DataFrame({"H_ANZRAD": [0, 1, 99]})
    out = a.map_number_of_bicycles(hh)
    assert out["number_of_bicycles"].isna().sum() == 0
    assert (out["number_of_bicycles"] >= 0).all()
