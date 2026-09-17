import logging

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import diary_facts as dfm
from braunschweig.popsim import diary_plan_match as dpm

FLAGS = dict(exclude_rbw_legs=True, exclude_holidays=True, drop_leading_arrive_home_leg=True)


def _donors():
    # real donor persons (weekday, kernwo 2). anzwege1: 803 mobile / 803 immobile / 804 / 0 only-rbW /
    # 2 valid / 2 valid on a public holiday / 1 valid arriving-home-only
    return pd.DataFrame({
        "H_ID": [1, 2, 3, 4, 5, 6, 7], "P_ID": [1, 1, 1, 1, 1, 1, 1],
        "anzwege1": [803, 803, 804, 0, 2, 2, 1],
        "mobil": [1, 0, 9, 1, 1, 1, 1],
        "mobil_diff": [5, 6, 9, 2, 1, 1, 1],
        "feiertag": [0, 0, 0, 0, 0, 1, 0],
        "kernwo": [2, 2, 2, 2, 2, 2, 2],
        "member_imputed": [False] * 7,
        "source_H_ID": [1, 2, 3, 4, 5, 6, 7], "source_P_ID": [1] * 7,
        "P_GEW": [1.0] * 7, "HP_ALTER": [40] * 7, "HP_SEX": [1] * 7,
        "P_FSCHEIN": [1] * 7, "P_TAET": [1] * 7, "P_FKARTE": [1] * 7,
    })


def _wege():
    return pd.DataFrame({
        "H_ID":      [4, 4, 5, 5, 6, 6, 7],
        "P_ID":      [1, 1, 1, 1, 1, 1, 1],
        "W_ID":      [1, 2, 1, 2, 1, 2, 1],
        "W_ZWECK":   [2, 2, 1, 8, 1, 8, 8],
        "W_RBW":     [1, 1, 0, 0, 0, 0, 0],
        "W_SO1":     [701, 701, 1, 809, 1, 809, 2],
        "wegkm_imp": [5.0, 5.0, 3.0, 3.0, 3.0, 3.0, 4.0],
    })


def test_classify_plan_sources_reasons():
    donors = _donors(); facts = dfm.compute_diary_facts(_wege())
    reasons = dpm.classify_plan_sources(donors, donors, facts, **FLAGS)
    assert list(reasons) == ["nodiary_mobile", "nodiary_immobile_keep", "nodiary_unknown", "only_rbw",
                             "realisable", "holiday", "emptied_by_arrive_home_drop"]


def test_classify_respects_flags_off():
    donors = _donors(); facts = dfm.compute_diary_facts(_wege())
    reasons = dpm.classify_plan_sources(donors, donors, facts, exclude_rbw_legs=False,
                                        exclude_holidays=False, drop_leading_arrive_home_leg=False)
    assert reasons.iloc[3] == "realisable"   # only-rbW keeps its legs when rbW stays
    assert reasons.iloc[5] == "realisable"   # holiday diaries allowed
    assert reasons.iloc[6] == "realisable"   # arriving leg kept


def test_realisable_pool_excludes_bad_sources_and_holidays():
    donors = _donors(); facts = dfm.compute_diary_facts(_wege())
    pool_any = dpm.build_realisable_pool(donors, facts, mobility="any", **FLAGS)
    pool_mobile = dpm.build_realisable_pool(donors, facts, mobility="mobile", **FLAGS)
    assert set(pool_any["H_ID"]) == {5}          # 6 = holiday, 7 = emptied, 1-4 = no diary / only rbW
    assert set(pool_mobile["H_ID"]) == {5}


def test_reassign_remaps_only_flagged_sources_and_is_deterministic():
    donors = _donors(); facts = dfm.compute_diary_facts(_wege())
    out1, trace1, rep1 = dpm.reassign_diaryless_plan_sources(donors, donors, facts, rng=np.random.RandomState(3),
                                                             hard_employment=True, fine_child_age_bands=False, **FLAGS)
    out2, trace2, rep2 = dpm.reassign_diaryless_plan_sources(donors, donors, facts, rng=np.random.RandomState(3),
                                                             hard_employment=True, fine_child_age_bands=False, **FLAGS)
    pd.testing.assert_frame_equal(out1, out2)
    remapped = out1[trace1["reason"].isin(dpm.REASONS_REMAP).to_numpy()]
    assert set(zip(remapped["source_H_ID"], remapped["source_P_ID"])) == {(5, 1)}
    kept = out1.loc[out1["H_ID"].isin([2, 5])]
    assert (kept["source_H_ID"] == kept["H_ID"]).all()     # immobile 803 and the valid diary keep their source
    assert rep1.n_remapped == 5 and rep1.counts_by_reason["holiday"] == 1
    assert rep1.share_remapped == pytest.approx(5 / 7)


def test_reassign_raises_on_empty_pool():
    donors = _donors().iloc[:4]; facts = dfm.compute_diary_facts(_wege())
    with pytest.raises(ValueError, match="realisable"):
        dpm.reassign_diaryless_plan_sources(donors, donors, facts, rng=np.random.RandomState(0),
                                            hard_employment=True, fine_child_age_bands=False, **FLAGS)


def test_reassign_requires_mobility_columns():
    donors = _donors().drop(columns=["mobil"]); facts = dfm.compute_diary_facts(_wege())
    with pytest.raises(KeyError, match="mobil"):
        dpm.reassign_diaryless_plan_sources(donors, donors, facts, rng=np.random.RandomState(0),
                                            hard_employment=True, fine_child_age_bands=False, **FLAGS)
    donors_no_holiday_flag = _donors().drop(columns=["feiertag"])
    with pytest.raises(KeyError, match="feiertag"):
        dpm.reassign_diaryless_plan_sources(donors_no_holiday_flag, donors_no_holiday_flag, facts,
                                            rng=np.random.RandomState(0), hard_employment=True, fine_child_age_bands=False,
                                            **FLAGS)  # exclude_holidays=True


def test_reassign_requires_person_match_columns():
    # donor_persons is complete; only the `persons` frame (match_person's target rows) is
    # missing a column match_person reads -- must fail here, not inside weekend_plan_match.
    donors = _donors(); facts = dfm.compute_diary_facts(_wege())
    persons = donors.drop(columns=["HP_ALTER"])
    with pytest.raises(KeyError, match="HP_ALTER"):
        dpm.reassign_diaryless_plan_sources(persons, donors, facts, rng=np.random.RandomState(0),
                                            hard_employment=True, fine_child_age_bands=False, **FLAGS)


# ---------------------------------------------------------------------------
# Un-relaxable employment boundary -- Plan B Task 6, issue #368
# ---------------------------------------------------------------------------
def _crossing_donors():
    """One diary-less EMPLOYED person to remap plus two realisable weekday donors.

    Donor 5 is NOT employed (P_TAET 11, Rentner) but matches the target on EVERY soft
    key; donor 8 IS employed (P_TAET 1) and matches none of them. Today's ladder drops
    ``employed`` second-from-last and therefore picks donor 5 -- exactly the
    employment-boundary crossing this guard closes, and the one the employment-
    conditional work control would otherwise be fighting.
    """
    return pd.DataFrame({
        "H_ID": [1, 5, 8], "P_ID": [1, 1, 1],
        "anzwege1": [803, 2, 2],
        "mobil": [1, 1, 1],
        "feiertag": [0, 0, 0],
        "kernwo": [2, 2, 2],
        "member_imputed": [False] * 3,
        "source_H_ID": [1, 5, 8], "source_P_ID": [1, 1, 1],
        "P_GEW": [1.0, 1.0, 1.0],
        "HP_ALTER": [40, 40, 8],
        "HP_SEX": [1, 1, 2],
        "P_FSCHEIN": [1, 1, 2],
        "P_TAET": [1, 11, 1],
        "P_FKARTE": [1, 1, 3],
    })


def _crossing_wege():
    """Two direct legs (home -> work -> home) for the two realisable donors."""
    return pd.DataFrame({
        "H_ID":      [5, 5, 8, 8],
        "P_ID":      [1, 1, 1, 1],
        "W_ID":      [1, 2, 1, 2],
        "W_ZWECK":   [1, 8, 1, 8],
        "W_RBW":     [0, 0, 0, 0],
        "W_SO1":     [1, 809, 1, 809],
        "wegkm_imp": [3.0, 3.0, 3.0, 3.0],
    })


def test_reassign_reports_zero_crossings_with_hard_employment_and_counts_them_without(caplog):
    donors = _crossing_donors(); facts = dfm.compute_diary_facts(_crossing_wege())
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.diary_plan_match"):
        hard_out, _hard_trace, hard_report = dpm.reassign_diaryless_plan_sources(
            donors, donors, facts, rng=np.random.RandomState(3), hard_employment=True, fine_child_age_bands=False, **FLAGS)
    soft_out, _soft_trace, soft_report = dpm.reassign_diaryless_plan_sources(
        donors, donors, facts, rng=np.random.RandomState(3), hard_employment=False, fine_child_age_bands=False, **FLAGS)

    assert hard_report.n_remapped == soft_report.n_remapped == 1
    # Hard: the employed donor wins although it shares no soft key.
    assert tuple(hard_out.loc[0, ["source_H_ID", "source_P_ID"]]) == (8, 1)
    assert hard_report.n_crossed_employment_boundary == 0
    # Soft (today's ladder): the not-employed donor wins on the soft keys.
    assert tuple(soft_out.loc[0, ["source_H_ID", "source_P_ID"]]) == (5, 1)
    assert soft_report.n_crossed_employment_boundary == 1
    # The count is logged as a rate (no silent fallback).
    assert any("employment boundary crossed by 0/1 remaps (0.00%)" in record.getMessage()
               for record in caplog.records)


def test_reassign_warns_when_a_crossing_survives_hard_employment(caplog):
    """No donor of the target's employment class -> match_person's whole-pool fallback
    still returns a donor, and the caller COUNTS the crossing and WARNS about it."""
    donors = _crossing_donors()
    donors = donors[donors["H_ID"] != 8].reset_index(drop=True)  # drop the only employed donor
    facts = dfm.compute_diary_facts(_crossing_wege())
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.diary_plan_match"):
        out, _trace, report = dpm.reassign_diaryless_plan_sources(
            donors, donors, facts, rng=np.random.RandomState(3), hard_employment=True, fine_child_age_bands=False, **FLAGS)
    assert tuple(out.loc[0, ["source_H_ID", "source_P_ID"]]) == (5, 1)  # fallback, not an exception
    assert report.n_crossed_employment_boundary == 1
    warnings = [r for r in caplog.records
                if r.levelno == logging.WARNING and "employment boundary crossed" in r.getMessage()]
    assert len(warnings) == 1
    assert "1/1 remaps (100.00%)" in warnings[0].getMessage()


# ---------------------------------------------------------------------------
# Fine child age bands for the diary match -- issue #386
# ---------------------------------------------------------------------------
def _child_donors():
    """One diary-less 7-year-old to remap plus two realisable school-child donors.

    Donor 8 is a 7-year-old (primary school), donor 9 a 13-year-old (lower secondary).
    Both share every other match key with the target, so under the coarse 6-13 band
    they are interchangeable and the P_GEW-weighted draw prefers the heavier
    13-year-old -- the defect of issue #386. Donor 5 is an adult, so the fine band
    still has a genuine choice to make rather than a single surviving candidate.
    """
    return pd.DataFrame({
        "H_ID": [1, 5, 8, 9], "P_ID": [1, 1, 1, 1],
        "anzwege1": [803, 2, 2, 2],
        "mobil": [1, 1, 1, 1],
        "feiertag": [0, 0, 0, 0],
        "kernwo": [2, 2, 2, 2],
        "member_imputed": [False] * 4,
        "source_H_ID": [1, 5, 8, 9], "source_P_ID": [1, 1, 1, 1],
        "P_GEW": [1.0, 1.0, 1.0, 1000.0],
        "HP_ALTER": [7, 40, 7, 13],
        "HP_SEX": [1, 1, 1, 1],
        "P_FSCHEIN": [2, 1, 2, 2],
        "P_TAET": [9, 1, 9, 9],
        "P_FKARTE": [3, 1, 3, 3],
    })


def _child_wege():
    """Two direct legs (home -> education -> home) for the three realisable donors."""
    return pd.DataFrame({
        "H_ID":      [5, 5, 8, 8, 9, 9],
        "P_ID":      [1, 1, 1, 1, 1, 1],
        "W_ID":      [1, 2, 1, 2, 1, 2],
        "W_ZWECK":   [3, 8, 3, 8, 3, 8],
        "W_RBW":     [0, 0, 0, 0, 0, 0],
        "W_SO1":     [1, 809, 1, 809, 1, 809],
        "wegkm_imp": [3.0, 3.0, 1.5, 1.5, 4.0, 4.0],
    })


def test_reassign_with_fine_child_bands_prefers_a_primary_school_donor():
    donors = _child_donors(); facts = dfm.compute_diary_facts(_child_wege())
    coarse_out, _t, coarse_report = dpm.reassign_diaryless_plan_sources(
        donors, donors, facts, rng=np.random.RandomState(3), hard_employment=True,
        fine_child_age_bands=False, **FLAGS)
    fine_out, _t2, fine_report = dpm.reassign_diaryless_plan_sources(
        donors, donors, facts, rng=np.random.RandomState(3), hard_employment=True,
        fine_child_age_bands=True, **FLAGS)

    assert coarse_report.n_remapped == fine_report.n_remapped == 1
    # Today: the 13-year-old's diary wins the weighted draw inside the 6-13 band.
    assert tuple(coarse_out.loc[0, ["source_H_ID", "source_P_ID"]]) == (9, 1)
    # With the fine bands the 7-year-old can only inherit a 6-9-year-old's diary.
    assert tuple(fine_out.loc[0, ["source_H_ID", "source_P_ID"]]) == (8, 1)


def test_reassign_counts_and_logs_the_fine_child_band_crossing_rate(caplog):
    """The crossing count is measured against the FINE bands in BOTH arms, so the
    OFF arm reports today's rate and the two arms are directly comparable."""
    donors = _child_donors(); facts = dfm.compute_diary_facts(_child_wege())
    with caplog.at_level(logging.INFO, logger="braunschweig.popsim.diary_plan_match"):
        coarse_report = dpm.reassign_diaryless_plan_sources(
            donors, donors, facts, rng=np.random.RandomState(3), hard_employment=True,
            fine_child_age_bands=False, **FLAGS)[2]
    fine_report = dpm.reassign_diaryless_plan_sources(
        donors, donors, facts, rng=np.random.RandomState(3), hard_employment=True,
        fine_child_age_bands=True, **FLAGS)[2]

    # The one remapped person is a 7-year-old, i.e. inside the band the fine edges split.
    assert coarse_report.n_remapped_in_split_child_band == 1
    assert fine_report.n_remapped_in_split_child_band == 1
    assert coarse_report.n_crossed_fine_child_age_band == 1   # got the 13-year-old
    assert fine_report.n_crossed_fine_child_age_band == 0     # got the 7-year-old
    assert any("fine child age band crossed by 1/1" in record.getMessage()
               for record in caplog.records)


def test_reassign_fine_child_bands_off_is_byte_identical_to_today():
    """The OFF path must reproduce the pre-#386 sources AND leave the rng where it was,
    so enabling the flag never shifts the draws of any later consumer of the shared
    completion stream."""
    donors = _donors(); facts = dfm.compute_diary_facts(_wege())
    off_rng = np.random.RandomState(3)
    on_rng = np.random.RandomState(3)
    off_out = dpm.reassign_diaryless_plan_sources(
        donors, donors, facts, rng=off_rng, hard_employment=True,
        fine_child_age_bands=False, **FLAGS)[0]
    # Pre-#386 donor set, pinned by test_reassign_remaps_only_flagged_sources_and_is_deterministic.
    remapped = off_out[off_out["H_ID"].isin([1, 3, 4, 6, 7])]
    assert set(zip(remapped["source_H_ID"], remapped["source_P_ID"])) == {(5, 1)}

    dpm.reassign_diaryless_plan_sources(donors, donors, facts, rng=on_rng, hard_employment=True,
                                        fine_child_age_bands=True, **FLAGS)
    off_state, on_state = off_rng.get_state(), on_rng.get_state()
    assert off_state[0] == on_state[0]
    assert (off_state[1] == on_state[1]).all()
    assert off_state[2:] == on_state[2:]
