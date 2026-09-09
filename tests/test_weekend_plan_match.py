# tests/test_weekend_plan_match.py
import logging

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


def test_person_keys_employed_matches_canonical_employed_taet():
    """_person_keys must classify 'employed' using the canonical EMPLOYED_TAET set
    from attributes.py (issue #162), not the ad-hoc P_TAET.between(1, 6) range.
    P_TAET=8 (Azubi/Ausbildung) IS employed per EMPLOYED_TAET; between(1,6) wrongly
    excludes it. P_TAET=5 (Elternzeit) is NOT employed; between(1,6) wrongly includes it.
    """
    persons = pd.DataFrame({
        "HP_ALTER": [30, 30],
        "HP_SEX": [1, 1],
        "P_FSCHEIN": [1, 1],
        "P_FKARTE": [1, 1],
        "P_TAET": [8, 5],  # Azubi (employed), Elternzeit (not employed)
    })
    keys = wpm._person_keys(persons)
    assert bool(keys.loc[0, "employed"]) is True, "P_TAET=8 (Azubi) must be employed"
    assert bool(keys.loc[1, "employed"]) is False, "P_TAET=5 (Elternzeit) must not be employed"


def test_match_person_employed_key_uses_canonical_set():
    """An Azubi (P_TAET=8) target must match an Azubi weekday donor at level 0 on
    the 'employed' key, even though a Rentner (P_TAET=11, not employed) donor with
    otherwise-identical keys is also present. The stale between(1, 6) rule treated
    P_TAET=8 as NOT employed, which would incorrectly prefer the Rentner donor.
    """
    weekday = pd.DataFrame({
        "H_ID": [50, 51], "P_ID": [1, 1],
        "HP_ALTER": [20, 20], "HP_SEX": [1, 1],
        "P_FSCHEIN": [1, 1], "P_TAET": [8, 11], "P_FKARTE": [1, 1],
        "P_GEW": [1.0, 1.0],
    })
    target = pd.Series({
        "HP_ALTER": 20, "HP_SEX": 1, "P_FSCHEIN": 1, "P_TAET": 8, "P_FKARTE": 1,
    })
    h, p, level = wpm.match_person(target, weekday, rng=np.random.RandomState(0))
    assert (h, p) == (50, 1) and level == 0


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


# ---------------------------------------------------------------------------
# Un-relaxable (HARD) person keys -- Plan B Task 6, issue #368
# ---------------------------------------------------------------------------
# match_person is shared by BOTH callers of the donor build (weekend_plan_match.
# reassign_weekend_plan_sources and diary_plan_match.reassign_diaryless_plan_sources)
# and both draw from ONE seeded RandomState whose draw SEQUENCE is a documented
# byte-identity contract (braunschweig.popsim.completed_donor module docstring).
# BASELINE_PERSON_DRAWS freezes that sequence for the two fixtures below: it is the
# (H_ID, P_ID, level) triples the function produced BEFORE the hard_keys parameter
# existed, captured by running the pre-change implementation over _hard_key_pool() /
# _hard_key_targets() with a single np.random.RandomState(7) and committed here as
# literals. A change of pool composition, branch order or draw count for the default
# (hard_keys=frozenset()) path must fail HERE rather than silently move every
# synthetic person in the population.
BASELINE_PERSON_DRAWS: list = [
    (105, 1, 2), (107, 1, 3), (101, 1, 3), (111, 1, 2), (105, 1, 0),
    (102, 1, 1), (105, 1, 2), (101, 1, 3), (103, 1, 0), (109, 1, 0),
    (102, 1, 2), (110, 1, 2), (102, 1, 1), (103, 1, 2), (104, 1, 3),
    (105, 1, 0), (104, 1, 2), (109, 1, 2), (102, 1, 3), (104, 1, 2),
    (109, 1, 2), (111, 1, 1), (104, 1, 3), (110, 1, 3), (105, 1, 3),
    (105, 1, 1), (101, 1, 3), (102, 1, 2), (109, 1, 2), (108, 1, 3),
    (103, 1, 2), (109, 1, 1), (105, 1, 0), (109, 1, 2), (102, 1, 0),
    (102, 1, 3), (100, 1, 1), (101, 1, 3), (105, 1, 3), (104, 1, 0),
    (102, 1, 2), (104, 1, 0), (101, 1, 2), (100, 1, 3), (102, 1, 1),
    (105, 1, 1), (106, 1, 3), (110, 1, 1), (100, 1, 3), (111, 1, 3),
]
#: Level histogram of the frozen baseline at capture time: {0: 8, 1: 9, 2: 16,
#: 3: 17}. 33 of the 50 draws sit at level >= 2, i.e. they DID relax past the
#: ``employed`` key -- the baseline therefore covers exactly the ladder step this
#: task makes optional, and it uses all 12 donors of the pool.


def _hard_key_pool(n_donors=12, seed=11):
    """A small, deliberately sparse weekday donor pool.

    Sparse on purpose: with only 12 donors spread over the five match keys, the 50
    targets of :func:`_hard_key_targets` exercise several relaxation levels (and the
    whole-pool fallback), so the frozen baseline is discriminating rather than a row
    of level-0 exact matches.
    """
    rng = np.random.RandomState(seed)
    return pd.DataFrame({
        "H_ID": np.arange(100, 100 + n_donors),
        "P_ID": np.ones(n_donors, dtype=int),
        "HP_ALTER": rng.choice([4, 10, 16, 30, 55, 70], size=n_donors),
        "HP_SEX": rng.choice([1, 2], size=n_donors),
        "P_FSCHEIN": rng.choice([1, 2], size=n_donors),
        # 1 / 8 are in EMPLOYED_TAET, 5 (Elternzeit) / 11 (Rentner) are not.
        "P_TAET": rng.choice([1, 5, 8, 11], size=n_donors),
        # 3 / 4 are in PT_SUBSCRIPTION_FKARTE, 1 / 8 are not.
        "P_FKARTE": rng.choice([1, 3, 4, 8], size=n_donors),
        "P_GEW": rng.uniform(0.5, 5.0, size=n_donors).round(3),
    })


def _hard_key_targets(n_targets=50, seed=23):
    """50 target rows for the frozen draw-sequence baseline (one Series each)."""
    rng = np.random.RandomState(seed)
    frame = pd.DataFrame({
        "HP_ALTER": rng.choice([3, 9, 15, 25, 41, 67], size=n_targets),
        "HP_SEX": rng.choice([1, 2], size=n_targets),
        "P_FSCHEIN": rng.choice([1, 2], size=n_targets),
        "P_TAET": rng.choice([1, 5, 8, 11], size=n_targets),
        "P_FKARTE": rng.choice([1, 3, 4, 8], size=n_targets),
    })
    return [frame.loc[i] for i in frame.index]


def _draw_sequence(*, hard_keys=None, seed=7):
    """Run the 50 baseline targets through match_person on ONE seeded rng.

    ``hard_keys=None`` uses the OLD call shape (no hard_keys argument at all), so the
    two call shapes can be compared against each other AND against the frozen
    baseline.
    """
    pool = _hard_key_pool()
    rng = np.random.RandomState(seed)
    sequence = []
    for target in _hard_key_targets():
        if hard_keys is None:
            household_id, person_id, level = wpm.match_person(target, pool, rng=rng)
        else:
            household_id, person_id, level = wpm.match_person(
                target, pool, rng=rng, hard_keys=hard_keys)
        sequence.append((int(household_id), int(person_id), int(level)))
    return sequence


def test_match_person_with_empty_hard_keys_reproduces_todays_draw_sequence():
    """The default (no hard key) path must be byte-identical to the pre-change function.

    Both call shapes must reproduce BASELINE_PERSON_DRAWS exactly: same donors, same
    relaxation levels, same order, hence the same number of rng draws in the same
    order. This is the blast-radius guard for the weekend caller, which keeps the
    default and whose draws are entangled with member completion and the diary match
    in ONE seeded stream.
    """
    old_call_shape = _draw_sequence(hard_keys=None)
    explicit_default = _draw_sequence(hard_keys=frozenset())
    assert old_call_shape == explicit_default
    assert old_call_shape == BASELINE_PERSON_DRAWS
    # The baseline must be discriminating: it has to contain relaxed matches, not
    # only level-0 exact hits (otherwise it would not notice a broken ladder).
    assert len({level for _h, _p, level in BASELINE_PERSON_DRAWS}) > 1


def test_match_person_never_relaxes_a_hard_key():
    """With ``employed`` hard, the only same-employment donor wins even though it
    differs in EVERY soft key -- and the returned level is the number of SOFT keys.

    Donor 50 matches all four soft keys but is a Rentner (P_TAET 11, not employed);
    donor 51 is employed (P_TAET 1) and differs in has_license, sex, age_band and
    has_pt. Today's ladder drops ``employed`` second-from-last and therefore picks
    donor 50 -- the boundary crossing this guard closes.
    """
    weekday = pd.DataFrame({
        "H_ID": [50, 51], "P_ID": [1, 1],
        "HP_ALTER": [40, 8], "HP_SEX": [1, 2],
        "P_FSCHEIN": [1, 2], "P_TAET": [11, 1], "P_FKARTE": [1, 3],
        "P_GEW": [1.0, 1.0],
    })
    target = pd.Series({
        "HP_ALTER": 41, "HP_SEX": 1, "P_FSCHEIN": 1, "P_TAET": 1, "P_FKARTE": 1,
    })
    n_soft_keys = len(wpm.PERSON_KEYS_BY_PRIORITY) - 1

    h, p, level = wpm.match_person(
        target, weekday, rng=np.random.RandomState(0), hard_keys=frozenset({"employed"}))
    assert (h, p) == (51, 1)
    # With a hard key set, `level` counts SOFT-key relaxations only, so its maximum
    # is the number of soft keys (4), not len(PERSON_KEYS_BY_PRIORITY).
    assert level == n_soft_keys == 4
    assert bool(wpm._person_keys(weekday.loc[[1]])["employed"].iloc[0]) is True

    # Discriminating: without the hard key the SAME call crosses the boundary.
    h_soft, _p_soft, _level_soft = wpm.match_person(
        target, weekday, rng=np.random.RandomState(0))
    assert h_soft == 50


def test_match_person_falls_back_to_the_whole_pool_when_no_donor_shares_the_hard_key(caplog):
    """No donor of the target's employment class -> the whole-pool fallback still
    returns a donor (never raises); the CALLER counts the crossing.

    The fallback's own log line is asserted here because this branch is the ONLY origin of
    a hard-key boundary crossing: it is what makes a crossing visible in a run log at all
    (project rule: no silent fallbacks). The line must name the hard key(s) that could not
    be honoured, otherwise a crossing in a 100 % run cannot be attributed to a key.
    """
    weekday = pd.DataFrame({
        "H_ID": [50, 51], "P_ID": [1, 1],
        "HP_ALTER": [40, 41], "HP_SEX": [1, 1],
        "P_FSCHEIN": [1, 1], "P_TAET": [11, 5], "P_FKARTE": [1, 1],  # neither employed
        "P_GEW": [1.0, 1.0],
    })
    target = pd.Series({
        "HP_ALTER": 41, "HP_SEX": 1, "P_FSCHEIN": 1, "P_TAET": 1, "P_FKARTE": 1,
    })
    with caplog.at_level(logging.DEBUG, logger="braunschweig.popsim.weekend_plan_match"):
        h, p, level = wpm.match_person(
            target, weekday, rng=np.random.RandomState(0),
            hard_keys=frozenset({"employed"}))
    assert (h, p) in {(50, 1), (51, 1)}
    assert level == len(wpm.PERSON_KEYS_BY_PRIORITY) - 1

    fallback_lines = [r.getMessage() for r in caplog.records
                      if "whole-pool size-only fallback" in r.getMessage()]
    assert len(fallback_lines) == 1, caplog.text
    # The hard key that could not be honoured is named, and so is the donor drawn.
    assert "no donor shares the hard key(s) ['employed']" in fallback_lines[0]
    assert f"drew weekday ({h}, {p})" in fallback_lines[0]


def test_the_whole_pool_fallback_line_is_a_hard_key_only_event(caplog):
    """Discrimination for the assertion above: WITHOUT hard keys the line never appears.

    A target that mismatches every one of the five soft keys still does NOT reach the
    fallback: once the ladder has dropped them all, the mask is the unrestricted pool, so
    the ordinary branch returns a donor at the maximum relaxation level. The fallback is
    therefore reachable only when a HARD key excludes every donor -- which is what makes
    the line above a reliable count of boundary crossings, and what the assertion above
    would not prove on its own if the line were emitted on every fully relaxed match.
    """
    weekday = pd.DataFrame({
        "H_ID": [50], "P_ID": [1], "HP_ALTER": [40], "HP_SEX": [2],
        "P_FSCHEIN": [2], "P_TAET": [11], "P_FKARTE": [2], "P_GEW": [1.0],
    })
    target = pd.Series({
        "HP_ALTER": 41, "HP_SEX": 1, "P_FSCHEIN": 1, "P_TAET": 1, "P_FKARTE": 1,
    })
    # Guard the guard: the HIGHEST-priority key must mismatch. The ladder drops keys from
    # the lowest priority upward, so a mismatch on the key that is dropped LAST forces
    # every rung to fail and ``active`` to empty completely -- without that, "no fallback
    # line" would hold for the trivial reason that an earlier rung already matched.
    donor_keys = wpm._person_keys(weekday).iloc[0]
    target_keys = wpm._person_keys(pd.DataFrame([target])).iloc[0]
    top_priority_key = wpm.PERSON_KEYS_BY_PRIORITY[0]
    assert donor_keys[top_priority_key] != target_keys[top_priority_key]

    with caplog.at_level(logging.DEBUG, logger="braunschweig.popsim.weekend_plan_match"):
        h, p, level = wpm.match_person(target, weekday, rng=np.random.RandomState(0))

    assert (h, p) == (50, 1)
    # Maximum relaxation: every soft key was dropped, i.e. the ladder DID run to the
    # bottom rung and still returned through the ordinary branch.
    assert level == len(wpm.PERSON_KEYS_BY_PRIORITY)
    assert not [r for r in caplog.records
                if "whole-pool size-only fallback" in r.getMessage()], caplog.text


def test_match_person_rejects_an_unknown_hard_key():
    """A typo in hard_keys must FAIL, not silently degrade to no guard at all
    (project rule: no silent fallbacks)."""
    weekday = pd.DataFrame({
        "H_ID": [50], "P_ID": [1], "HP_ALTER": [40], "HP_SEX": [1],
        "P_FSCHEIN": [1], "P_TAET": [1], "P_FKARTE": [1], "P_GEW": [1.0],
    })
    target = pd.Series({
        "HP_ALTER": 41, "HP_SEX": 1, "P_FSCHEIN": 1, "P_TAET": 1, "P_FKARTE": 1,
    })
    # Match the GUARD's own wording, not the echoed typo: "employment" appears in the
    # message only because the bad key is echoed back, so matching on it would also pass
    # if some unrelated ValueError mentioning the word were raised instead of this guard.
    with pytest.raises(ValueError, match="unknown hard match key") as excinfo:
        wpm.match_person(target, weekday, rng=np.random.RandomState(0),
                         hard_keys=frozenset({"employment"}))
    # The offending key AND the valid set are both named, so the message is actionable.
    assert "employment" in str(excinfo.value)
    assert str(list(wpm.PERSON_KEYS_BY_PRIORITY)) in str(excinfo.value)


def test_hard_keys_do_not_change_the_number_of_rng_draws():
    """A hard key changes WHICH donor is drawn, never HOW MANY draws are consumed.

    match_person makes exactly one weighted_choice call per invocation whichever
    branch returns, and weighted_choice consumes exactly one rng value. Two runs over
    the same 50 targets from identically seeded RandomStates must therefore end on the
    SAME stream position -- which is what lets the diary caller enable the guard
    without shifting the draws of every later consumer of the shared completion rng
    (weekend match, member completion, the remaining diary remaps).
    """
    pool = _hard_key_pool()
    targets = _hard_key_targets()
    soft_rng = np.random.RandomState(7)
    hard_rng = np.random.RandomState(7)
    soft = [wpm.match_person(target, pool, rng=soft_rng) for target in targets]
    hard = [wpm.match_person(target, pool, rng=hard_rng,
                             hard_keys=frozenset({"employed"})) for target in targets]

    soft_state, hard_state = soft_rng.get_state(), hard_rng.get_state()
    assert soft_state[0] == hard_state[0]
    assert (soft_state[1] == hard_state[1]).all()
    assert soft_state[2:] == hard_state[2:]
    # ...and the guard is not a no-op on this fixture: it moves most of the donors. The
    # measured figure on this fixture is 28 of 50; the floor is deliberately well below
    # that (fixture edits may move it) but far enough above zero that the "different
    # selection" half of this test keeps its meaning -- a bare "> 0" would still pass if a
    # regression reduced the guard's effect to a single donor.
    n_moved = sum(1 for s, h in zip(soft, hard) if s[:2] != h[:2])
    assert n_moved >= 10, f"the hard key moved only {n_moved} of {len(targets)} donors"
