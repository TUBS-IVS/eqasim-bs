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


def test_prepare_logs_the_drop_rate_not_just_the_count(caplog):
    """No-silent-fallback rule (CLAUDE.md, MANDATORY): a dropped-row class must be logged as
    n / total (rate), never as a bare count. One of six persons has a non-positive weight, so
    the warning must state "1/6 persons (16.67%) dropped"."""
    bad = _persons(); bad.loc[0, "GEWICHT_P_ZENSUS"] = 0.0
    caplog.set_level("WARNING")
    A.prepare_absence_persons(bad)
    messages = [record.getMessage() for record in caplog.records]
    assert any("1/6 persons (16.67%) dropped" in message for message in messages), messages


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


def test_build_absence_household_by_size_raises_on_a_non_positive_household_weight():
    """Deferred minor (final-review fix wave): a household weight <= 0 must raise, named."""
    prepared, _ = A.prepare_absence_persons(_persons())
    households = _households(); households.loc[households["HHNR"] == 2, "GEWICHT_HH_ZENSUS"] = 0.0
    with pytest.raises(ValueError, match="non-positive GEWICHT_HH_ZENSUS"):
        A.build_absence_household_by_size(prepared, households)


# ---------------------------------------------------------------------------
# clustering_share (final-review fix wave, ruling R12): the traceable source of ADR-0110's
# "55.8 % (person-weighted; 49.4 % unweighted)" household-clustering figure.
# ---------------------------------------------------------------------------

def test_clustering_share_on_the_tiny_fixture():
    # Households: 1 (1 person, absent -> fully absent), 2 (2 persons, both absent -> fully
    # absent), 3 (3 persons, 1 absent, 2 present -> NOT fully absent). Absent persons: hh1/pnr1
    # (weight 1.0), hh2/pnr1 (weight 1.0), hh2/pnr2 (weight 1.0) -- all in fully absent
    # households -- plus none from hh3. So all 3 absent persons live in a fully absent household:
    # both shares are 1.0 on this fixture (a genuinely partial case is exercised by construction
    # in test_by_household_size_all_absent_share's household 3, which has zero absent members).
    prepared, _ = A.prepare_absence_persons(_persons())
    result = A.clustering_share(prepared)
    assert result["n_absent_persons"] == 3
    assert result["n_absent_in_fully_absent_households"] == 3
    assert result["unweighted_share"] == pytest.approx(1.0)
    assert result["weighted_share"] == pytest.approx(1.0)


def test_clustering_share_distinguishes_clustered_from_partial_absence():
    # A fourth, partially-absent household (id 4): one absent member out of two -- must lower
    # the clustering share below 1.0 while every OTHER household stays as in the tiny fixture.
    persons = _persons()
    partial = pd.DataFrame({
        "HHNR": [4, 4], "PNR": [1, 2], "V_ALTER": [50, 48],
        "E_ANZ_WEGE": [-7, 3], "GEWICHT_P_ZENSUS": [1.0, 1.0], "MITTL_WERKTAG": [1, 1],
    })
    prepared, _ = A.prepare_absence_persons(pd.concat([persons, partial], ignore_index=True))
    result = A.clustering_share(prepared)
    assert result["n_absent_persons"] == 4          # 3 from the tiny fixture + 1 from household 4
    assert result["n_absent_in_fully_absent_households"] == 3   # household 4 is only partially absent
    assert result["unweighted_share"] == pytest.approx(3 / 4)
    assert result["weighted_share"] == pytest.approx(3 / 4)     # equal weights on this fixture


def test_clustering_share_returns_nan_when_nobody_is_absent():
    persons = _persons()
    persons["E_ANZ_WEGE"] = [3, 2, 3, 3, 0, 2]   # nobody away from home
    prepared, _ = A.prepare_absence_persons(persons)
    result = A.clustering_share(prepared)
    assert result["n_absent_persons"] == 0
    assert np.isnan(result["unweighted_share"]) and np.isnan(result["weighted_share"])
