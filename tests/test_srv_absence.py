"""Unit tests for the SrV 2023 full-day absence aggregates (issue #370, sub-project B).

Synthetic SrV-shaped frames only -- the raw scientific-use microdata is local-only and must
never be read by a test. Three households (sizes 1, 2, 3), six persons: the single person and
both members of the 2-person household are away from home the whole reporting day
(``E_ANZ_WEGE == -7``), the 3-person household has one absent member and two present.
"""
import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration import srv_absence as A


def _persons():
    return pd.DataFrame({
        "HHNR": [1, 2, 2, 3, 3, 3], "PNR": [1, 1, 2, 1, 2, 3],
        "V_ALTER": [70, 30, 32, 40, 38, 8],
        "E_ANZ_WEGE": [-7, -7, -7, 3, 0, 2],
        "GEWICHT_P_ZENSUS": [1.0] * 6, "MITTL_WERKTAG": [1] * 6,
    })


def _households():
    return pd.DataFrame({"HHNR": [1, 2, 3], "GEWICHT_HH_ZENSUS": [1.0, 1.0, 1.0]})


def test_age_band_edges_match_the_spec():
    assert list(A.age_band(pd.Series([0, 5, 6, 17, 18, 29, 30, 44, 45, 64, 65, 74, 75, 99]))) == [
        "0-5", "0-5", "6-17", "6-17", "18-29", "18-29", "30-44", "30-44", "45-64", "45-64",
        "65-74", "65-74", "75+", "75+"]


def test_prepare_flags_absent_and_counts():
    prepared, diagnostics = A.prepare_absence_persons(_persons())
    assert prepared["absent"].tolist() == [True, True, True, False, False, False]
    assert diagnostics["n_persons_raw"] == 6 and diagnostics["n_absent"] == 3


def test_prepare_raises_on_unexpected_negative_trip_code():
    bad = _persons(); bad.loc[3, "E_ANZ_WEGE"] = -9
    with pytest.raises(ValueError, match="E_ANZ_WEGE"):
        A.prepare_absence_persons(bad)


def test_prepare_raises_when_not_average_weekday():
    bad = _persons(); bad.loc[0, "MITTL_WERKTAG"] = 0
    with pytest.raises(ValueError, match="MITTL_WERKTAG"):
        A.prepare_absence_persons(bad)


def test_by_age_band_rates_and_all_row():
    prepared, _ = A.prepare_absence_persons(_persons())
    table = A.build_absence_by_age_band(prepared)
    row = table.set_index("band")
    assert row.loc["30-44", "p_absent"] == pytest.approx(2 / 4)   # 30, 32 absent; 40, 38 present
    assert row.loc["6-17", "p_absent"] == pytest.approx(0.0)
    assert row.loc["all", "n_unweighted"] == 6 and row.loc["all", "p_absent"] == pytest.approx(0.5)
    assert list(table["band"]) == list(A.AGE_BAND_LABELS) + ["all"]   # every band present, even empty


def test_by_household_size_all_absent_share():
    prepared, _ = A.prepare_absence_persons(_persons())
    table = A.build_absence_household_by_size(prepared, _households()).set_index("size_class")
    assert table.loc[1, "p_all_absent"] == pytest.approx(1.0)
    assert table.loc[2, "p_all_absent"] == pytest.approx(1.0)
    assert table.loc[3, "p_all_absent"] == pytest.approx(0.0)
    assert np.isnan(table.loc[4, "p_all_absent"]) and table.loc[4, "n_households_unweighted"] == 0
    assert list(table.index) == [1, 2, 3, 4, 5]


def test_invariants_accept_the_builder_output_and_reject_a_share_above_one():
    prepared, _ = A.prepare_absence_persons(_persons())
    by_age = A.build_absence_by_age_band(prepared)
    by_size = A.build_absence_household_by_size(prepared, _households())
    A.check_invariants(by_age, by_size)
    broken = by_age.copy(); broken.loc[0, "p_absent"] = 1.2
    with pytest.raises(ValueError, match="p_absent"):
        A.check_invariants(broken, by_size)
