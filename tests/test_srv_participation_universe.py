"""Unit tests for the SrV 2023 at-home-or-mobile participation aggregates (issue #368).

Synthetic SrV-shaped frames only -- the raw scientific-use microdata is local-only and must
never be read by a test. The fixture weights are chosen so every asserted share is an exact
fraction that can be re-derived by hand from the docstring of :func:`_raw`, and deliberately so
that the three WRONG ways of computing a share (unweighted, a mean of the Kreis rates, and a
Kreis rate mistaken for the region rate) all give a DIFFERENT number from the right one -- a
fixture in which every weight is 1.0, or which has only one non-empty Kreis, would let an
unweighted or mean-of-rows implementation pass every assertion.
"""
import pandas as pd
import pytest

from braunschweig.calibration import srv_participation_universe as spu
from braunschweig.calibration.srv_distance_targets import WOLFSBURG_KREIS, ZGB_KREISE


def _raw():
    """Three households in two Kreise, seven persons, four legs.

    ==========  =====  =====  ======  ======  =========================================
    pid         kreis    age  V_ERW   weight  day
    ==========  =====  =====  ======  ======  =========================================
    1_1         03101     40  9          1.0  work leg (E_ZWECK_9 1) -- employed, 14+
    1_2         03101     10  6          1.0  education leg (E_ZWECK_9 3) -- under 14
    2_1         03101     70  3          2.0  no leg -- not employed, 14+
    2_2         03101     16  8          1.0  work leg (E_ZWECK_9 2) -- apprentice, 14+
    3_1         03102     30  10         5.0  DROPPED: away from home all day (E_ANZ_WEGE -7)
    3_2         03102      8  6          3.0  education leg (E_ZWECK_9 4) -- under 14
    3_3         03102     50  9          6.0  no leg -- employed, 14+, does NOT work today
    ==========  =====  =====  ======  ======  =========================================

    Household 2 shares Braunschweig's AGS with household 1, so Kreis 03101 holds persons 1_1 to
    2_2 and Kreis 03102 holds 3_1 to 3_3.

    Derivable expectations (weights in brackets):

    * work universe (14+, at home): 03101 = 1_1 [1], 2_1 [2], 2_2 [1]; 03102 = 3_3 [6].
      employed_share 03101 = (1+1)/4 = 0.5, 03102 = 6/6 = 1.0, region = (1+1+6)/10 = **0.8**.
      A MEAN of the two Kreis rates would give 0.75 and an UNWEIGHTED region share 3/4 = 0.75.
    * p_work_employed 03101 = 2/2 = 1.0, 03102 = 0/6 = 0.0, region = (1+1)/8 = **0.25**.
      A mean of the Kreis rates would give 0.5 and an unweighted share 2/3 = 0.667.
    * education band 6-17: 03101 = 1_2 [1, education] and 2_2 [1, none] -> 0.5; 03102 = 3_2
      [3, education] -> 1.0; region = (1+3)/5 = **0.8**. A mean of the Kreis rates would give
      0.75 and an unweighted share 2/3 = 0.667.
    """
    households = pd.DataFrame({"HHNR": [1, 2, 3], "AGS": [3101000, 3101000, 3102000]})
    persons = pd.DataFrame({
        "HHNR": [1, 1, 2, 2, 3, 3, 3], "PNR": [1, 2, 1, 2, 1, 2, 3],
        "V_ALTER": [40, 10, 70, 16, 30, 8, 50], "V_ERW": [9, 6, 3, 8, 10, 6, 9],
        "E_ANZ_WEGE": [3, 2, 0, 2, -7, 2, 4],
        "GEWICHT_P_ZENSUS": [1.0, 1.0, 2.0, 1.0, 5.0, 3.0, 6.0],
        "MITTL_WERKTAG": [1] * 7})
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
    assert bool(employed["1_1"]) and bool(employed["2_2"]) and bool(employed["3_3"])
    assert not bool(employed["2_1"]) and not bool(employed["1_2"])
    assert out.set_index("pid").loc["2_2", "kreis"] == "03101"
    assert out.set_index("pid").loc["3_3", "kreis"] == "03102"


def test_universe_counts_missing_weight_and_missing_kreis():
    persons, wege, households = _raw()
    persons.loc[0, "GEWICHT_P_ZENSUS"] = -9.0          # invalid weight -> excluded
    households.loc[2, "AGS"] = -10                     # sentinel AGS -> no Kreis for 3_2, 3_3
    out, excl = spu.prepare_universe_persons(persons, households)
    assert excl["missing_weight"] == 1 and "1_1" not in set(out["pid"])
    assert excl["missing_kreis"] == 2
    assert not {"3_2", "3_3"} & set(out["pid"])


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


def test_universe_raises_on_a_non_numeric_trip_count():
    """Controller ruling R16: a non-numeric E_ANZ_WEGE coerces to NaN, which passes both
    'NaN < 0' and 'NaN == -7' as False -- the person would stay in the universe with an unknown
    reporting-day state and appear in NO exclusion class. It must raise, not slip through.
    """
    persons, wege, households = _raw()
    persons["E_ANZ_WEGE"] = persons["E_ANZ_WEGE"].astype(object)
    persons.loc[2, "E_ANZ_WEGE"] = "keine Angabe"
    with pytest.raises(ValueError, match="non-numeric E_ANZ_WEGE"):
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


def test_work_shares_are_weighted_and_pooled_over_persons():
    """The region row must be a WEIGHTED share pooled over persons, not an unweighted count and
    not a mean of the Kreis rates. The fixture is built so all three answers differ (see
    :func:`_raw`): employed_share 0.8 pooled vs 0.75 either wrong way, p_work_employed 0.25
    pooled vs 0.5 as a mean of Kreis rates vs 0.667 unweighted.
    """
    persons, wege, households = _raw()
    df, _ = spu.build_work_by_employment_aggregate(persons, wege, households)
    by_code = df.set_index("code")

    assert by_code.loc["03101", "employed_share"] == pytest.approx(2.0 / 4.0)
    assert by_code.loc["03102", "employed_share"] == pytest.approx(1.0)
    assert by_code.loc["03102", "n_unweighted"] == 1
    assert by_code.loc["03102", "p_work_employed"] == pytest.approx(0.0)
    assert pd.isna(by_code.loc["03102", "p_work_nonemployed"])   # no non-employed person there

    total = by_code.loc["03ZGB"]
    assert total["n_unweighted"] == 4 and total["n_employed_unweighted"] == 3
    assert total["employed_share"] == pytest.approx((1.0 + 1.0 + 6.0) / 10.0)      # 0.8
    assert total["employed_share"] != pytest.approx(0.75)                          # mean/unweighted
    assert total["p_work_employed"] == pytest.approx((1.0 + 1.0) / 8.0)            # 0.25
    assert total["p_work_employed"] != pytest.approx(0.5)                          # mean of rates
    assert total["p_work_employed"] != pytest.approx(2.0 / 3.0)                    # unweighted
    assert total["p_work_nonemployed"] == pytest.approx(0.0)


def test_work_by_employment_columns_and_universe_age_bound():
    persons, wege, households = _raw()
    df, diagnostics = spu.build_work_by_employment_aggregate(persons, wege, households)
    assert list(df.columns) == ["code", "level", "n_unweighted", "n_employed_unweighted",
                                "n_nonemployed_unweighted", "employed_share", "p_work_employed",
                                "p_work_nonemployed"]
    # Both Kreise have 14+ persons (03101: 1_1, 2_1, 2_2; 03102: 3_3 -- 3_1 was away), so the
    # region row pools 4 persons and the Kreis rows sum to it.
    total = df[df["level"] == "total"].iloc[0]
    assert total["n_unweighted"] == 4
    assert df.loc[df["level"] == "kreis", "n_unweighted"].sum() == 4
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


def test_education_share_is_weighted_and_pooled_over_persons():
    """Same weighting/pooling proof as for the work table, on the column that carries four of
    the five committed education numbers: 03102's single 6-17 person carries weight 3.0, so the
    region 6-17 rate is 0.8 pooled, 0.75 as a mean of the Kreis rates and 0.667 unweighted.
    """
    persons, wege, households = _raw()
    df, _ = spu.build_education_by_age_aggregate(persons, wege, households)
    band = df[df["band"] == "education_6_17"].set_index("code")

    assert band.loc["03101", "p_education"] == pytest.approx(0.5)
    assert band.loc["03102", "p_education"] == pytest.approx(1.0)
    assert band.loc["03102", "n_unweighted"] == 1

    total = band.loc[spu.REGION_CODE]
    assert total["n_unweighted"] == 3
    assert total["p_education"] == pytest.approx((1.0 + 3.0) / 5.0)     # 0.8
    assert total["p_education"] != pytest.approx(0.75)                  # mean of the Kreis rates
    assert total["p_education"] != pytest.approx(2.0 / 3.0)             # unweighted


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


# --------------------------------------------------------------------------- Kreis coverage
def _fixture_tables():
    persons, wege, households = _raw()
    work, _ = spu.build_work_by_employment_aggregate(persons, wege, households)
    education, _ = spu.build_education_by_age_aggregate(persons, wege, households)
    return work, education


def test_kreis_coverage_accepts_the_fixture_geography():
    work, education = _fixture_tables()
    spu.check_kreis_coverage(work, education, expected_kreise=("03101", "03102"))


def test_kreis_coverage_allows_only_wolfsburg_to_be_absent():
    """Wolfsburg is not surveyed by SrV, so its absence must never be an error."""
    work, education = _fixture_tables()
    spu.check_kreis_coverage(work, education,
                             expected_kreise=("03101", "03102", WOLFSBURG_KREIS))


def test_kreis_coverage_rejects_a_missing_surveyed_kreis():
    """A delivery that lost a whole Kreis satisfies every table invariant while emitting one
    row fewer, which is why coverage needs its own check (fix round 1, item 2).
    """
    work, education = _fixture_tables()
    spu.check_invariants(work, education)              # the invariants alone do NOT catch it
    with pytest.raises(ValueError, match="03153"):     # Goslar has no row in the fixture
        spu.check_kreis_coverage(work, education, expected_kreise=ZGB_KREISE)


def test_kreis_coverage_rejects_a_kreis_row_outside_the_expected_set():
    work, education = _fixture_tables()
    with pytest.raises(ValueError, match="03102"):
        spu.check_kreis_coverage(work, education, expected_kreise=("03101",))


# --------------------------------------------------------------------------- ADR-0117
def test_an_unreadable_employment_code_becomes_unknown_not_not_employed():
    """SrV codes a refusal / implausible answer as a NEGATIVE V_ERW.

    Before ADR-0117 such a person silently became "not employed", which put a non-answer into the
    DENOMINATOR of the employed share the work control is built from. The flag is now a nullable
    boolean and carries pd.NA instead, so no consumer can mistake the non-answer for an answer.
    The person STAYS in the universe: their education participation is perfectly readable, and
    the education control does not look at employment at all.
    """
    persons, _wege, households = _raw()
    persons.loc[0, "V_ERW"] = -10          # person 1_1: employed (V_ERW 9) -> unreadable
    out, _excl = spu.prepare_universe_persons(persons, households)
    assert "1_1" in set(out["pid"])                     # still in the universe
    flag = out.set_index("pid")["employed"]
    assert pd.isna(flag["1_1"])                         # ... but with no employment answer
    assert bool(flag["2_2"]) and not bool(flag["2_1"])  # the others are untouched


def test_the_work_control_drops_the_unknown_status_and_renormalises():
    persons, wege, households = _raw()
    base_table, _ = spu.build_work_by_employment_aggregate(persons, wege, households)
    base_region = base_table[base_table["level"] == spu.LEVEL_TOTAL].iloc[0]["employed_share"]
    # One extra 14+ person of the same Kreis whose employment code cannot be read, with a large
    # weight: counted as "not employed" it would drag the regional share down by a third.
    extra = persons.iloc[[0]].copy()
    extra["PNR"] = 9
    extra["V_ERW"] = -8
    extra["GEWICHT_P_ZENSUS"] = 50.0
    table, diagnostics = spu.build_work_by_employment_aggregate(
        pd.concat([persons, extra], ignore_index=True), wege, households)
    region = table[table["level"] == spu.LEVEL_TOTAL].iloc[0]["employed_share"]
    assert diagnostics["n_unknown_employment_status"] == 1
    assert region == pytest.approx(base_region)   # a non-answer moves the target by nothing


def test_the_education_control_keeps_a_person_with_an_unreadable_employment_code():
    """The narrow fix: the employment non-answer must not shrink the EDUCATION universe."""
    persons, wege, households = _raw()
    base, _ = spu.build_education_by_age_aggregate(persons, wege, households)
    persons.loc[0, "V_ERW"] = -10
    after, _ = spu.build_education_by_age_aggregate(persons, wege, households)
    pd.testing.assert_frame_equal(base, after)
