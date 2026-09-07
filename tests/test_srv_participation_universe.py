"""Unit tests for the SrV 2023 at-home-or-mobile participation aggregates (issue #368).

Synthetic SrV-shaped frames only -- the raw scientific-use microdata is local-only and must
never be read by a test. The fixture weights are chosen so every asserted share is an exact
fraction that can be re-derived by hand from the docstring of :func:`_raw`.
"""
import pandas as pd
import pytest

from braunschweig.calibration import srv_participation_universe as spu


def _raw():
    """Three households, six persons, four legs.

    ==========  =====  =====  ======  ======  =========================================
    pid         kreis    age  V_ERW   weight  day
    ==========  =====  =====  ======  ======  =========================================
    1_1         03101     40  9          1.0  work leg (E_ZWECK_9 1) -- employed, 14+
    1_2         03101     10  6          1.0  education leg (E_ZWECK_9 3) -- under 14
    2_1         03101     70  3          2.0  no leg -- not employed, 14+
    2_2         03101     16  8          1.0  work leg (E_ZWECK_9 2) -- apprentice, 14+
    3_1         03102     30  10         5.0  DROPPED: away from home all day (E_ANZ_WEGE -7)
    3_2         03102      8  6          1.0  education leg (E_ZWECK_9 4) -- under 14
    ==========  =====  =====  ======  ======  =========================================

    Household 2 shares Braunschweig's AGS with household 1, so the 14+ work universe of Kreis
    03101 holds exactly persons 1_1, 2_1 and 2_2 (weights 1.0, 2.0, 1.0).
    """
    households = pd.DataFrame({"HHNR": [1, 2, 3], "AGS": [3101000, 3101000, 3102000]})
    persons = pd.DataFrame({
        "HHNR": [1, 1, 2, 2, 3, 3], "PNR": [1, 2, 1, 2, 1, 2],
        "V_ALTER": [40, 10, 70, 16, 30, 8], "V_ERW": [9, 6, 3, 8, 10, 6],
        "E_ANZ_WEGE": [3, 2, 0, 2, -7, 2], "GEWICHT_P_ZENSUS": [1.0, 1.0, 2.0, 1.0, 5.0, 1.0],
        "MITTL_WERKTAG": [1] * 6})
    wege = pd.DataFrame({"HHNR": [1, 1, 2, 3], "PNR": [1, 2, 2, 2], "E_ZWECK_9": [1, 3, 2, 4]})
    return persons, wege, households


# --------------------------------------------------------------------------- universe
def test_universe_drops_away_persons_and_counts_them():
    persons, wege, households = _raw()
    out, excl = spu.prepare_universe_persons(persons, households)
    assert excl["away_from_home"] == 1 and "3_1" not in set(out["pid"])


def test_universe_columns_and_employed_flag():
    """The apprentice (V_ERW 8) is employed under decision Q5; V_ERW 3 and 6 are not."""
    persons, wege, households = _raw()
    out, _ = spu.prepare_universe_persons(persons, households)
    assert list(out.columns) == ["pid", "kreis", "weight", "age", "employed"]
    employed = out.set_index("pid")["employed"]
    assert bool(employed["1_1"]) and bool(employed["2_2"])
    assert not bool(employed["2_1"]) and not bool(employed["1_2"])
    assert out.set_index("pid").loc["2_2", "kreis"] == "03101"


def test_universe_counts_missing_weight_and_missing_kreis():
    persons, wege, households = _raw()
    persons.loc[0, "GEWICHT_P_ZENSUS"] = -9.0          # invalid weight -> excluded
    households.loc[2, "AGS"] = -10                     # sentinel AGS -> no Kreis for 3_2
    out, excl = spu.prepare_universe_persons(persons, households)
    assert excl["missing_weight"] == 1 and "1_1" not in set(out["pid"])
    assert excl["missing_kreis"] == 1 and "3_2" not in set(out["pid"])


def test_universe_raises_on_a_foreign_reporting_day():
    persons, wege, households = _raw()
    persons.loc[0, "MITTL_WERKTAG"] = 0
    with pytest.raises(ValueError, match="MITTL_WERKTAG"):
        spu.prepare_universe_persons(persons, households)


def test_universe_raises_on_an_unexpected_negative_trip_count():
    """A negative E_ANZ_WEGE other than -7 would be silently swallowed by 'E_ANZ_WEGE >= 0'."""
    persons, wege, households = _raw()
    persons.loc[2, "E_ANZ_WEGE"] = -10
    with pytest.raises(ValueError, match="E_ANZ_WEGE"):
        spu.prepare_universe_persons(persons, households)


def test_universe_raises_on_a_kreis_outside_zgb():
    persons, wege, households = _raw()
    households.loc[2, "AGS"] = 9162000                 # Munich: outside the surveyed region
    with pytest.raises(ValueError, match="ZGB"):
        spu.prepare_universe_persons(persons, households)


# --------------------------------------------------------------------------- work aggregate
def test_work_by_employment_conditional_rates_by_hand():
    persons, wege, households = _raw()
    df, _ = spu.build_work_by_employment_aggregate(persons, wege, households)
    k = df[df["code"] == "03101"].iloc[0]
    # 14+ in 03101: 1/1 (employed V_ERW 9, work leg), 2/1 (V_ERW 3 not employed, no work, w=2),
    # 2/2 (apprentice V_ERW 8 -> EMPLOYED by decision Q5, work leg E_ZWECK_9 2, w=1)
    assert k["n_unweighted"] == 3 and k["n_employed_unweighted"] == 2
    assert k["p_work_employed"] == pytest.approx(1.0) and k["p_work_nonemployed"] == pytest.approx(0.0)
    assert k["employed_share"] == pytest.approx(2.0 / 4.0)
    assert (df["level"] == "total").sum() == 1 and df.loc[df["level"] == "total", "code"].iloc[0] == "03ZGB"


def test_work_by_employment_columns_and_universe_age_bound():
    persons, wege, households = _raw()
    df, diagnostics = spu.build_work_by_employment_aggregate(persons, wege, households)
    assert list(df.columns) == ["code", "level", "n_unweighted", "n_employed_unweighted",
                                "n_nonemployed_unweighted", "employed_share", "p_work_employed",
                                "p_work_nonemployed"]
    # Only Kreis 03101 has 14+ persons left after the away-from-home exclusion (3_1 was the only
    # 14+ person of 03102), so the region total equals that Kreis row.
    total = df[df["level"] == "total"].iloc[0]
    assert total["n_unweighted"] == 3
    assert diagnostics["away_from_home"] == 1
    assert diagnostics["n_below_min_age"] == 2          # 1_2 (age 10) and 3_2 (age 8)


def test_work_by_employment_nan_share_when_a_class_is_empty():
    """No employed person in a Kreis must give NaN, never a silent 0.0."""
    persons, wege, households = _raw()
    persons.loc[[0, 3], "V_ERW"] = 3                   # 1_1 and 2_2 become non-employed
    df, _ = spu.build_work_by_employment_aggregate(persons, wege, households)
    k = df[df["code"] == "03101"].iloc[0]
    assert k["n_employed_unweighted"] == 0
    assert pd.isna(k["p_work_employed"])
    assert k["p_work_nonemployed"] == pytest.approx(2.0 / 4.0)   # 1_1 (w=1) and 2_2 (w=1) work


# --------------------------------------------------------------------------- education aggregate
def test_education_by_age_bands_and_total():
    persons, wege, households = _raw()
    df, _ = spu.build_education_by_age_aggregate(persons, wege, households)
    k617 = df[(df["code"] == "03101") & (df["band"] == "education_6_17")].iloc[0]
    assert k617["n_unweighted"] == 2 and k617["p_education"] == pytest.approx(0.5)   # 1/2 edu (w1), 2/2 no edu (w1)
    assert set(df["band"]) == {"education_0_5", "education_6_17", "education_18plus"}


def test_education_by_age_emits_empty_bands_with_nan():
    """No 0-5 person exists in the fixture: the band row is emitted with n=0 and a NaN share."""
    persons, wege, households = _raw()
    df, _ = spu.build_education_by_age_aggregate(persons, wege, households)
    assert list(df.columns) == ["code", "level", "band", "n_unweighted", "p_education"]
    zero_to_five = df[df["band"] == "education_0_5"]
    assert len(zero_to_five) == 3                      # 03101, 03102 and the region total
    assert (zero_to_five["n_unweighted"] == 0).all() and zero_to_five["p_education"].isna().all()
    total_617 = df[(df["level"] == "total") & (df["band"] == "education_6_17")].iloc[0]
    assert total_617["code"] == spu.REGION_CODE and total_617["n_unweighted"] == 3


# --------------------------------------------------------------------------- invariants
def test_invariants_reject_a_kreis_sum_that_does_not_match_the_total():
    persons, wege, households = _raw()
    w, _ = spu.build_work_by_employment_aggregate(persons, wege, households)
    e, _ = spu.build_education_by_age_aggregate(persons, wege, households)
    spu.check_invariants(w, e)
    with pytest.raises(ValueError, match="n_unweighted"):
        spu.check_invariants(w.assign(n_unweighted=w["n_unweighted"] + 1), e)


def test_invariants_reject_a_broken_education_band_total():
    persons, wege, households = _raw()
    w, _ = spu.build_work_by_employment_aggregate(persons, wege, households)
    e, _ = spu.build_education_by_age_aggregate(persons, wege, households)
    broken = e.copy()
    mask = (broken["level"] == "total") & (broken["band"] == "education_6_17")
    broken.loc[mask, "n_unweighted"] = 99
    with pytest.raises(ValueError, match="n_unweighted"):
        spu.check_invariants(w, broken)


def test_invariants_reject_a_share_outside_the_unit_interval():
    persons, wege, households = _raw()
    w, _ = spu.build_work_by_employment_aggregate(persons, wege, households)
    e, _ = spu.build_education_by_age_aggregate(persons, wege, households)
    with pytest.raises(ValueError, match="p_work_employed"):
        spu.check_invariants(w.assign(p_work_employed=1.5), e)
