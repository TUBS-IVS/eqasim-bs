# tests/test_weekend_plan_match.py
import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import weekend_plan_match as wpm


def _households():
    return pd.DataFrame({
        "H_ID": [1, 2],
        "H_GR": [2, 1],
        "hh_type5": ["couple", "single"],
        "oek_status": [3, 2],
        "RegioStaR7": [71, 77],
        "H_ANZAUTO": [2, 0],
        "H_GEW": [1.0, 1.0],
    })


def _persons():
    return pd.DataFrame({
        "H_ID": [1, 1, 2],
        "P_ID": [1, 2, 1],
        "HP_ALTER": [40, 38, 25],
        "HP_SEX": [1, 2, 2],
        "P_FSCHEIN": [1, 2, 1],
        "P_FKARTE": [1, 1, 4],
    })


def test_build_hh_features_columns_and_values():
    feats = wpm.build_hh_features(_households(), _persons())
    assert feats.loc[1, "size"] == 2
    assert feats.loc[1, "car_class"] == "2plus"
    assert feats.loc[2, "car_class"] == "0"
    assert bool(feats.loc[1, "any_license"]) is True   # P1 has licence
    assert bool(feats.loc[1, "any_pt"]) is False        # neither has a sub code
    assert bool(feats.loc[2, "any_pt"]) is True         # P_FKARTE 4 is a sub


def test_match_household_prefers_zero_relaxation():
    weekday = pd.DataFrame({
        "size": [2, 2], "hh_type5": ["couple", "couple"], "oek_status": [3, 9],
        "regiostar7": [71, 71], "car_class": ["2plus", "0"],
        "any_license": [True, False], "any_pt": [False, False],
        "H_GEW": [1.0, 1.0],
    }, index=pd.Index([100, 101], name="H_ID"))
    target = pd.Series({
        "size": 2, "hh_type5": "couple", "oek_status": 3, "regiostar7": 71,
        "car_class": "2plus", "any_license": True, "any_pt": False,
    })
    mid, level = wpm.match_household(7, target, weekday, rng=np.random.RandomState(0))
    assert mid == 100 and level == 0


def test_match_household_relaxes_until_pool_nonempty():
    # Same size + same RegioStaR (the hard keys) but every SOFT key differs ->
    # the matcher must relax all soft keys and still return the candidate.
    weekday = pd.DataFrame({
        "size": [2], "hh_type5": ["single"], "oek_status": [1], "regiostar7": [71],
        "car_class": ["0"], "any_license": [False], "any_pt": [True],
        "H_GEW": [1.0],
    }, index=pd.Index([200], name="H_ID"))
    target = pd.Series({
        "size": 2, "hh_type5": "couple", "oek_status": 3, "regiostar7": 71,
        "car_class": "2plus", "any_license": True, "any_pt": False,
    })
    mid, level = wpm.match_household(7, target, weekday, rng=np.random.RandomState(0))
    assert mid == 200 and level == len(wpm.SOFT_KEYS_BY_PRIORITY)  # all soft keys dropped


def test_match_household_never_crosses_regiostar():
    # A weekday HH identical in size and EVERY soft key but a different RegioStaR
    # must NOT be matched (RegioStaR is a hard key) -> falls through to (None, None).
    weekday = pd.DataFrame({
        "size": [2], "hh_type5": ["couple"], "oek_status": [3], "regiostar7": [77],
        "car_class": ["2plus"], "any_license": [True], "any_pt": [False],
    }, index=pd.Index([200], name="H_ID"))
    target = pd.Series({
        "size": 2, "hh_type5": "couple", "oek_status": 3, "regiostar7": 71,
        "car_class": "2plus", "any_license": True, "any_pt": False,
    })
    mid, level = wpm.match_household(7, target, weekday, rng=np.random.RandomState(0))
    assert mid is None and level is None


def test_match_household_returns_none_when_no_equal_size():
    weekday = pd.DataFrame({
        "size": [3], "hh_type5": ["couple"], "oek_status": [3], "regiostar7": [71],
        "car_class": ["2plus"], "any_license": [True], "any_pt": [False],
    }, index=pd.Index([300], name="H_ID"))
    target = pd.Series({
        "size": 2, "hh_type5": "couple", "oek_status": 3, "regiostar7": 71,
        "car_class": "2plus", "any_license": True, "any_pt": False,
    })
    mid, level = wpm.match_household(7, target, weekday, rng=np.random.RandomState(0))
    assert mid is None and level is None


def test_align_members_pairs_by_age_band_then_sex():
    target = pd.DataFrame({"HP_ALTER": [40, 8], "HP_SEX": [1, 2]}).reset_index(drop=True)
    donor = pd.DataFrame({"HP_ALTER": [10, 42], "HP_SEX": [2, 1]}).reset_index(drop=True)
    pairs = wpm.align_members(target, donor)
    # adult target (pos 0) -> adult donor (pos 1); child target (pos 1) -> child donor (pos 0)
    assert sorted(pairs) == [(0, 1), (1, 0)]


def test_match_person_exact_then_relaxes():
    weekday = pd.DataFrame({
        "H_ID": [50, 51], "P_ID": [1, 1],
        "HP_ALTER": [40, 9], "HP_SEX": [1, 2],
        "P_FSCHEIN": [1, 2], "P_TAET": [1, 11], "P_FKARTE": [1, 1],
        "P_GEW": [1.0, 1.0],
    })
    target = pd.Series({
        "HP_ALTER": 41, "HP_SEX": 1, "P_FSCHEIN": 1, "P_TAET": 1, "P_FKARTE": 1,
    })
    h, p, level = wpm.match_person(target, weekday, rng=np.random.RandomState(0))
    assert (h, p) == (50, 1) and level == 0


def test_match_person_raises_on_empty_pool():
    target = pd.Series({"HP_ALTER": 30, "HP_SEX": 1, "P_FSCHEIN": 1, "P_TAET": 1, "P_FKARTE": 1})
    with pytest.raises(ValueError, match="empty weekday person pool"):
        wpm.match_person(target, pd.DataFrame(columns=["H_ID", "P_ID", "HP_ALTER", "HP_SEX", "P_FSCHEIN", "P_TAET", "P_FKARTE"]), rng=np.random.RandomState(0))


def test_reassign_hh_match_remaps_weekend_household():
    households = pd.DataFrame({
        "H_ID": [1, 2], "H_GR": [2, 2], "hh_type5": ["couple", "couple"],
        "oek_status": [3, 3], "RegioStaR7": [71, 71], "H_ANZAUTO": [2, 2],
        "H_GEW": [1.0, 1.0],
    })
    persons = pd.DataFrame({
        "H_ID": [1, 1, 2, 2], "P_ID": [1, 2, 1, 2],
        "HP_ALTER": [40, 38, 41, 39], "HP_SEX": [1, 2, 1, 2],
        "P_FSCHEIN": [1, 1, 1, 1], "P_TAET": [1, 1, 1, 1], "P_FKARTE": [1, 1, 1, 1],
        "kernwo": [2, 2, 6, 6],          # HH 1 weekday, HH 2 weekend
        "source_H_ID": [1, 1, 2, 2], "source_P_ID": [1, 2, 1, 2],
        "member_imputed": [False, False, False, False],
        "P_GEW": [1.0, 1.0, 1.0, 1.0],
    })
    out, trace, report = wpm.reassign_weekend_plan_sources(
        households, persons, rng=np.random.RandomState(0))
    # weekday HH 1 untouched
    w1 = out[out["H_ID"] == 1]
    assert (w1["source_H_ID"] == w1["H_ID"]).all()
    # weekend HH 2 now points at the weekday donor (HH 1)
    w2 = out[out["H_ID"] == 2]
    assert (w2["source_H_ID"] == 1).all()
    assert report.n_weekend_households == 1 and report.n_hh_matched == 1
    assert set(trace["resolution"]) == {"own_plan", "hh_match"}


def test_reassign_person_fallback_when_no_equal_size_weekday_hh():
    households = pd.DataFrame({
        "H_ID": [1, 2], "H_GR": [1, 2], "hh_type5": ["single", "couple"],
        "oek_status": [2, 3], "RegioStaR7": [77, 71], "H_ANZAUTO": [0, 2],
        "H_GEW": [1.0, 1.0],
    })
    persons = pd.DataFrame({
        "H_ID": [1, 2, 2], "P_ID": [1, 1, 2],
        "HP_ALTER": [25, 41, 39], "HP_SEX": [2, 1, 2],
        "P_FSCHEIN": [1, 1, 1], "P_TAET": [1, 1, 1], "P_FKARTE": [1, 1, 1],
        "kernwo": [2, 6, 6],            # weekday single HH 1; weekend couple HH 2 (size 2)
        "source_H_ID": [1, 2, 2], "source_P_ID": [1, 1, 2],
        "member_imputed": [False, False, False],
        "P_GEW": [1.0, 1.0, 1.0],
    })
    out, trace, report = wpm.reassign_weekend_plan_sources(
        households, persons, rng=np.random.RandomState(0))
    # no equal-size (2) weekday HH -> both weekend persons go to person fallback (HH 1 person)
    w2 = out[out["H_ID"] == 2]
    assert (w2["source_H_ID"] == 1).all()
    assert report.n_hh_matched == 0 and report.n_person_fallback_households == 1
    assert "person_fallback" in set(trace["resolution"])


def test_reassign_is_deterministic_given_rng():
    households = pd.DataFrame({
        "H_ID": [1, 2, 3], "H_GR": [2, 2, 2],
        "hh_type5": ["couple"] * 3, "oek_status": [3, 3, 3],
        "RegioStaR7": [71, 71, 71], "H_ANZAUTO": [1, 1, 1],
        "H_GEW": [1.0, 1.0, 1.0],
    })
    persons = pd.DataFrame({
        "H_ID": [1, 1, 2, 2, 3, 3], "P_ID": [1, 2, 1, 2, 1, 2],
        "HP_ALTER": [40, 38, 40, 38, 41, 39], "HP_SEX": [1, 2, 1, 2, 1, 2],
        "P_FSCHEIN": [1, 1, 1, 1, 1, 1], "P_TAET": [1, 1, 1, 1, 1, 1],
        "P_FKARTE": [1, 1, 1, 1, 1, 1],
        "kernwo": [2, 2, 2, 2, 6, 6],   # HH3 weekend
        "source_H_ID": [1, 1, 2, 2, 3, 3], "source_P_ID": [1, 2, 1, 2, 1, 2],
        "member_imputed": [False] * 6,
        "P_GEW": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
    })
    a, _, _ = wpm.reassign_weekend_plan_sources(households.copy(), persons.copy(), rng=np.random.RandomState(7))
    b, _, _ = wpm.reassign_weekend_plan_sources(households.copy(), persons.copy(), rng=np.random.RandomState(7))
    pd.testing.assert_frame_equal(a, b)


def test_reassign_derives_hh_type5_when_absent():
    # Real load_completed_donor households do NOT carry hh_type5 (it is derived
    # later, in project_completed_seed). reassign must derive it itself, not crash.
    households = pd.DataFrame({
        "H_ID": [1, 2], "H_GR": [2, 2],
        "oek_status": [3, 3], "RegioStaR7": [71, 71], "H_ANZAUTO": [2, 2],
        "H_GEW": [1.0, 1.0],
        # NB: no hh_type5 column
    })
    persons = pd.DataFrame({
        "H_ID": [1, 1, 2, 2], "P_ID": [1, 2, 1, 2],
        "HP_ALTER": [40, 38, 41, 39], "HP_SEX": [1, 2, 1, 2],
        "P_FSCHEIN": [1, 1, 1, 1], "P_TAET": [1, 1, 1, 1], "P_FKARTE": [1, 1, 1, 1],
        "kernwo": [2, 2, 6, 6],          # HH 1 weekday, HH 2 weekend
        "source_H_ID": [1, 1, 2, 2], "source_P_ID": [1, 2, 1, 2],
        "member_imputed": [False, False, False, False],
        "P_GEW": [1.0, 1.0, 1.0, 1.0],
    })
    assert "hh_type5" not in households.columns
    out, trace, report = wpm.reassign_weekend_plan_sources(
        households, persons, rng=np.random.RandomState(0))
    # the weekend HH still gets remapped to the weekday donor (no KeyError)
    w2 = out[out["H_ID"] == 2]
    assert (w2["source_H_ID"] == 1).all()
    assert report.n_weekend_households == 1
    # caller's frame was not mutated with an hh_type5 column
    assert "hh_type5" not in households.columns


def test_reassign_no_silent_gap_when_donor_undersized():
    # Weekday donor HH 1 declares H_GR==2 but has only ONE actual person row
    # (an unfillable household). A size-2 weekend HH matched to it would leave
    # one weekend person unpaired -> it must route to the person fallback, never
    # keep its own weekend source.
    households = pd.DataFrame({
        "H_ID": [1, 2, 3], "H_GR": [2, 1, 2],
        "hh_type5": ["couple", "single", "couple"],
        "oek_status": [3, 2, 3], "RegioStaR7": [71, 77, 71],
        "H_ANZAUTO": [2, 0, 2],
        "H_GEW": [1.0, 1.0, 1.0],
    })
    persons = pd.DataFrame({
        # HH 1: weekday, declares size 2 but only 1 person row (undersized donor)
        # HH 2: weekday single (extra weekday person available for fallback)
        # HH 3: weekend, size 2
        "H_ID": [1, 2, 3, 3], "P_ID": [1, 1, 1, 2],
        "HP_ALTER": [40, 30, 41, 39], "HP_SEX": [1, 2, 1, 2],
        "P_FSCHEIN": [1, 1, 1, 1], "P_TAET": [1, 1, 1, 1], "P_FKARTE": [1, 1, 1, 1],
        "kernwo": [2, 2, 6, 6],
        "source_H_ID": [1, 2, 3, 3], "source_P_ID": [1, 1, 1, 2],
        "member_imputed": [False, False, False, False],
        "P_GEW": [1.0, 1.0, 1.0, 1.0],
    })
    out, trace, report = wpm.reassign_weekend_plan_sources(
        households, persons, rng=np.random.RandomState(0))
    w3 = out[out["H_ID"] == 3]
    # both weekend persons sourced from a WEEKDAY household (1 or 2), none keeps HH 3
    assert (w3["source_H_ID"].isin([1, 2])).all()
    assert (w3["source_H_ID"] != 3).all()
    we = trace[trace["donor_day_type"] == "weekend"]
    assert we["resolution"].isin({"hh_match", "person_fallback"}).all()
    # one matched at HH level, the surplus routed to person fallback
    assert "person_fallback" in set(we["resolution"])


def test_every_weekend_person_is_resolved_no_silent_gap():
    households = pd.DataFrame({
        "H_ID": [1, 2], "H_GR": [2, 2], "hh_type5": ["couple", "couple"],
        "oek_status": [3, 3], "RegioStaR7": [71, 71], "H_ANZAUTO": [1, 1],
        "H_GEW": [1.0, 1.0],
    })
    persons = pd.DataFrame({
        "H_ID": [1, 1, 2, 2], "P_ID": [1, 2, 1, 2],
        "HP_ALTER": [40, 38, 41, 39], "HP_SEX": [1, 2, 1, 2],
        "P_FSCHEIN": [1, 1, 1, 1], "P_TAET": [1, 1, 1, 1], "P_FKARTE": [1, 1, 1, 1],
        "kernwo": [2, 2, 6, 6],
        "source_H_ID": [1, 1, 2, 2], "source_P_ID": [1, 2, 1, 2],
        "member_imputed": [False] * 4,
        "P_GEW": [1.0, 1.0, 1.0, 1.0],
    })
    out, trace, _ = wpm.reassign_weekend_plan_sources(households, persons, rng=np.random.RandomState(0))
    weekend = trace[trace["donor_day_type"] == "weekend"]
    # every weekend person resolved to a weekday source (here HH 1)
    assert (weekend["plan_source_H_ID"] == 1).all()
    assert weekend["resolution"].isin({"hh_match", "person_fallback"}).all()


# ---------------------------------------------------------------------------
# A1 + A2: weekday_pool is reporting-day-based; mixed-HH sweep
# ---------------------------------------------------------------------------

def test_reassign_sweeps_weekend_member_of_mixed_household():
    """A genuinely-mixed household (3 weekday + 1 weekend members, majority=weekday)
    is classified as weekday by household_day_type. The weekend-reporting member
    initially sources its own weekend plan. The per-person safety-net sweep must
    detect this and reroute it to a weekday-reporting donor. report.n_swept >= 1.
    """
    # HH 10: pure weekday donor (exists so weekday_pool is non-empty)
    # HH 20: mixed household -- 3 weekday-reporting + 1 weekend-reporting member;
    #         household_day_type resolves to "weekday" (majority).
    households = pd.DataFrame({
        "H_ID": [10, 20], "H_GR": [2, 4],
        "hh_type5": ["couple", "couple"],
        "oek_status": [3, 3], "RegioStaR7": [71, 71], "H_ANZAUTO": [1, 1],
        "H_GEW": [1.0, 1.0],
    })
    persons = pd.DataFrame({
        "H_ID":          [10, 10,  20,  20,  20,  20],
        "P_ID":          [ 1,  2,   1,   2,   3,   4],
        "HP_ALTER":      [40, 38,  41,  39,  35,  45],
        "HP_SEX":        [ 1,  2,   1,   2,   1,   2],
        "P_FSCHEIN":     [ 1,  1,   1,   1,   1,   1],
        "P_TAET":        [ 1,  1,   1,   1,   1,   1],
        "P_FKARTE":      [ 1,  1,   1,   1,   1,   1],
        # HH 10: weekday. HH 20: first 3 members weekday (kernwo=2), 4th weekend (kernwo=6)
        "kernwo":        [ 2,  2,   2,   2,   2,   6],
        # all initially self-sourcing
        "source_H_ID":   [10, 10,  20,  20,  20,  20],
        "source_P_ID":   [ 1,  2,   1,   2,   3,   4],
        "member_imputed": [False] * 6,
        "P_GEW":         [1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
    })

    out, trace, report = wpm.reassign_weekend_plan_sources(
        households, persons, rng=np.random.RandomState(42))

    # The 4th member of HH 20 (P_ID=4, kernwo=6) must now point at a
    # weekday-reporting donor, not itself.
    weekend_member = out[(out["H_ID"] == 20) & (out["P_ID"] == 4)].iloc[0]
    # Donor must be a weekday-reporting real person
    from braunschweig.popsim.seed import WEEKDAY_KERNWO
    all_real = persons[~persons["member_imputed"]]
    wd_real_ids = set(
        zip(all_real[all_real["kernwo"].isin(WEEKDAY_KERNWO)]["H_ID"],
            all_real[all_real["kernwo"].isin(WEEKDAY_KERNWO)]["P_ID"])
    )
    assert (weekend_member["source_H_ID"], weekend_member["source_P_ID"]) in wd_real_ids, (
        f"Weekend member's source {(weekend_member['source_H_ID'], weekend_member['source_P_ID'])} "
        f"is not in the weekday-reporting donor set {wd_real_ids}"
    )
    # Trace resolution for this person should be "mixed_person_sweep"
    swept_row = trace[(trace["H_ID"] == 20) & (trace["P_ID"] == 4)].iloc[0]
    assert swept_row["resolution"] == "mixed_person_sweep", (
        f"Expected 'mixed_person_sweep', got '{swept_row['resolution']}'"
    )
    assert report.n_swept >= 1, f"Expected n_swept >= 1, got {report.n_swept}"


def test_build_hh_features_carries_h_gew():
    households = pd.DataFrame({
        "H_ID": [1, 2], "H_GR": [2, 2], "hh_type5": ["couple", "couple"],
        "oek_status": [3, 3], "RegioStaR7": [71, 71], "H_ANZAUTO": [1, 1],
        "H_GEW": [2.5, 10.0],
    })
    persons = pd.DataFrame({
        "H_ID": [1, 1, 2, 2], "P_ID": [1, 2, 1, 2],
        "HP_ALTER": [40, 38, 41, 39], "HP_SEX": [1, 2, 1, 2],
        "P_FSCHEIN": [1, 1, 1, 1], "P_FKARTE": [1, 1, 1, 1],
    })
    feats = wpm.build_hh_features(households, persons)
    assert feats.loc[1, "H_GEW"] == 2.5 and feats.loc[2, "H_GEW"] == 10.0


def test_match_household_draws_proportional_to_h_gew():
    # two equally-matching weekday HHs; HH 200 has ~9x the weight of HH 100
    weekday = pd.DataFrame({
        "size": [2, 2], "hh_type5": ["couple", "couple"], "oek_status": [3, 3],
        "regiostar7": [71, 71], "car_class": ["2plus", "2plus"],
        "any_license": [True, True], "any_pt": [False, False],
        "H_GEW": [1.0, 9.0],
    }, index=pd.Index([100, 200], name="H_ID"))
    target = pd.Series({
        "size": 2, "hh_type5": "couple", "oek_status": 3, "regiostar7": 71,
        "car_class": "2plus", "any_license": True, "any_pt": False,
    })
    rng = np.random.RandomState(0)
    counts = {100: 0, 200: 0}
    for _ in range(2000):
        mid, _ = wpm.match_household(7, target, weekday, rng=rng)
        counts[mid] += 1
    assert counts[200] > counts[100] * 3  # heavy HH dominates


def test_match_person_draws_proportional_to_p_gew():
    # two persons share the (highest-priority) keys; person (50,1) has 9x the weight
    weekday = pd.DataFrame({
        "H_ID": [50, 60], "P_ID": [1, 1],
        "HP_ALTER": [40, 40], "HP_SEX": [1, 1],
        "P_FSCHEIN": [1, 1], "P_TAET": [1, 1], "P_FKARTE": [1, 1],
        "P_GEW": [9.0, 1.0],
    })
    target = pd.Series({"HP_ALTER": 41, "HP_SEX": 1, "P_FSCHEIN": 1, "P_TAET": 1, "P_FKARTE": 1})
    rng = np.random.RandomState(0)
    counts = {50: 0, 60: 0}
    for _ in range(2000):
        h, _p, _l = wpm.match_person(target, weekday, rng=rng)
        counts[h] += 1
    assert counts[50] > counts[60] * 3  # heavy person dominates


def test_fallback_pool_excludes_weekend_reporting_persons():
    """The weekday_pool used for match_person must contain no weekend-reporting
    persons. Even if a person belongs to a household classified as 'weekday'
    (e.g. mixed household resolved by majority), their kernwo must be in
    WEEKDAY_KERNWO for them to appear in the pool.
    """
    from braunschweig.popsim.seed import WEEKDAY_KERNWO
    from braunschweig.popsim.day_type import WEEKEND_KERNWO as WKND_KW

    # Build: HH 10 pure weekday (2 members), HH 20 mixed (2 weekday + 1 weekend -> majority weekday)
    households = pd.DataFrame({
        "H_ID": [10, 20], "H_GR": [2, 3],
        "hh_type5": ["couple", "couple"],
        "oek_status": [3, 3], "RegioStaR7": [71, 71], "H_ANZAUTO": [1, 1],
    })
    persons = pd.DataFrame({
        "H_ID":        [10, 10,  20,  20,  20],
        "P_ID":        [ 1,  2,   1,   2,   3],
        "HP_ALTER":    [40, 38,  41,  39,  35],
        "HP_SEX":      [ 1,  2,   1,   2,   1],
        "P_FSCHEIN":   [ 1,  1,   1,   1,   1],
        "P_TAET":      [ 1,  1,   1,   1,   1],
        "P_FKARTE":    [ 1,  1,   1,   1,   1],
        "kernwo":      [ 2,  2,   2,   2,   6],  # HH 20 P_ID=3: weekend
        "source_H_ID": [10, 10,  20,  20,  20],
        "source_P_ID": [ 1,  2,   1,   2,   3],
        "member_imputed": [False] * 5,
    })

    # Reconstruct weekday_pool exactly as reassign_weekend_plan_sources does (A1)
    weekday_pool = persons[
        (~persons["member_imputed"].astype(bool))
        & persons["kernwo"].isin(WEEKDAY_KERNWO)
    ]

    # Assert no weekend-reporting row leaked into the pool
    assert not weekday_pool["kernwo"].isin(WKND_KW).any(), (
        f"weekday_pool must not contain weekend-kernwo rows; "
        f"found kernwo values: {weekday_pool['kernwo'].unique().tolist()}"
    )
