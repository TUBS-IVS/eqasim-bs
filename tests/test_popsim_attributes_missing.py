"""Tests for map_has_license + map_number_of_cars routing through missing.resolve.

Verifies that item-nonresponse codes (9, 99) are IMPUTED (not silently mapped to a
fixed default) and that structural codes (403, 404, 202) are resolved deterministically,
using the uniform missing policy from ``braunschweig.popsim.missing``.
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
