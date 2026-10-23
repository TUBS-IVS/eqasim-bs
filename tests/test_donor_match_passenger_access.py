"""The plan-source matchers keep a person's car-passenger access class (eqasim-bs#465, ADR-0162).

The weekend plan match and the diary plan match give a person the diary of ANOTHER MiD respondent while the
person keeps its own attributes (ADR-0106). The passenger availability is derived later from the attribute
respondent's ``P_VAUTO`` (ADR-0121), so a person whose own answer is "never" (``P_VAUTO == 3``) could inherit a
diary with car-passenger trips that the mode choice can then never reproduce (person 903464 of the 1 % rerun:
a night lift home became pt with a 4 h wait into the next morning, stuck at the end of the day). With the key
on, the matchers prefer a donor of the same passenger class; the class is a soft key (relaxed after the
licence, before sex), and every crossing that survives is counted.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import completed_donor as cd
from braunschweig.popsim import diary_facts as dfm
from braunschweig.popsim import diary_plan_match as dpm
from braunschweig.popsim import weekend_plan_match as wpm

FLAGS = dict(exclude_rbw_legs=True, exclude_holidays=True, drop_leading_arrive_home_leg=True)


def _weekday_pool():
    """Two donors identical in every other key; the passenger-capable one carries the heavier weight."""
    return pd.DataFrame({
        "H_ID": [10, 11], "P_ID": [1, 1],
        "HP_ALTER": [21, 21], "HP_SEX": [1, 1],
        "P_FSCHEIN": [2, 2], "P_TAET": [9, 9], "P_FKARTE": [1, 1],
        "P_VAUTO": [1, 3],
        "P_GEW": [1000.0, 1.0],
    })


def _never_passenger_target():
    return pd.Series({"HP_ALTER": 21, "HP_SEX": 1, "P_FSCHEIN": 2, "P_TAET": 9, "P_FKARTE": 1, "P_VAUTO": 3})


def test_passenger_key_gives_a_never_passenger_the_never_passenger_donor():
    pool, target = _weekday_pool(), _never_passenger_target()

    without = wpm.match_person(target, pool, rng=np.random.RandomState(0))
    with_key = wpm.match_person(target, pool, rng=np.random.RandomState(0), passenger_key=True)

    assert without[:2] == (10, 1)     # today: the heavier passenger-capable donor wins the weighted draw
    assert with_key[:2] == (11, 1)    # the key makes the never-passenger donor the only exact candidate
    assert with_key[2] == 0           # found without relaxing any soft key


def test_passenger_key_is_relaxed_when_no_donor_shares_the_class():
    pool = _weekday_pool().iloc[[0]]
    household_id, person_id, level = wpm.match_person(
        _never_passenger_target(), pool, rng=np.random.RandomState(0), passenger_key=True)
    assert (household_id, person_id) == (10, 1)
    assert level > 0


def test_passenger_key_does_not_change_the_number_of_rng_draws():
    pool, target = _weekday_pool(), _never_passenger_target()
    off_rng, on_rng = np.random.RandomState(5), np.random.RandomState(5)
    for _ in range(3):
        wpm.match_person(target, pool, rng=off_rng)
        wpm.match_person(target, pool, rng=on_rng, passenger_key=True)
    off_state, on_state = off_rng.get_state(), on_rng.get_state()
    assert (off_state[1] == on_state[1]).all() and off_state[2:] == on_state[2:]


def test_prepared_pool_with_passenger_key_matches_the_unprepared_path():
    pool, target = _weekday_pool(), _never_passenger_target()
    prepared = wpm.prepare_person_pool(pool, passenger_key=True)
    assert (wpm.match_person(target, pool, rng=np.random.RandomState(0), passenger_key=True,
                             prepared_pool=prepared)
            == wpm.match_person(target, pool, rng=np.random.RandomState(0), passenger_key=True))


def test_prepared_pool_refuses_a_different_passenger_key_setting():
    pool = _weekday_pool()
    prepared = wpm.prepare_person_pool(pool)
    with pytest.raises(ValueError, match="passenger_key"):
        wpm.match_person(_never_passenger_target(), pool, rng=np.random.RandomState(0), passenger_key=True,
                         prepared_pool=prepared)


def test_passenger_key_requires_p_vauto():
    pool = _weekday_pool().drop(columns="P_VAUTO")
    with pytest.raises(KeyError, match="P_VAUTO"):
        wpm.match_person(_never_passenger_target(), pool, rng=np.random.RandomState(0), passenger_key=True)


def test_unanswered_and_child_codes_are_not_the_never_class():
    keys = wpm.passenger_never_class(pd.Series([1, 2, 3, 9, 206, 402, np.nan]))
    assert keys.tolist() == [False, False, True, False, False, False, False]


def test_align_members_keeps_the_passenger_class_inside_the_age_and_sex_cell():
    donor = pd.DataFrame({"HP_ALTER": [40, 40], "HP_SEX": [1, 1], "P_VAUTO": [1, 3]})
    target = pd.DataFrame({"HP_ALTER": [41], "HP_SEX": [1], "P_VAUTO": [3]})
    assert wpm.align_members(target, donor) == [(0, 0)]
    assert wpm.align_members(target, donor, passenger_key=True) == [(0, 1)]


def test_align_members_with_passenger_key_never_changes_the_number_of_pairs():
    donor = pd.DataFrame({"HP_ALTER": [40, 10], "HP_SEX": [1, 2], "P_VAUTO": [1, 402]})
    target = pd.DataFrame({"HP_ALTER": [41, 38, 9], "HP_SEX": [1, 2, 2], "P_VAUTO": [3, 3, 402]})
    assert len(wpm.align_members(target, donor, passenger_key=True)) == len(wpm.align_members(target, donor))


def _diary_donors():
    """Person 1 has no diary (803, mobile, P_VAUTO 3); donors 2 and 3 carry realisable diaries."""
    return pd.DataFrame({
        "H_ID": [1, 2, 3], "P_ID": [1, 1, 1],
        "anzwege1": [803, 2, 2], "mobil": [1, 1, 1], "mobil_diff": [5, 1, 1], "feiertag": [0, 0, 0],
        "kernwo": [2, 2, 2], "member_imputed": [False] * 3,
        "source_H_ID": [1, 2, 3], "source_P_ID": [1, 1, 1],
        "P_GEW": [1.0, 1000.0, 1.0], "HP_ALTER": [21] * 3, "HP_SEX": [1] * 3,
        "P_FSCHEIN": [2] * 3, "P_TAET": [9] * 3, "P_FKARTE": [1] * 3,
        "P_VAUTO": [3, 1, 3],
    })


def _diary_wege():
    return pd.DataFrame({
        "H_ID": [2, 2, 3, 3], "P_ID": [1, 1, 1, 1], "W_ID": [1, 2, 1, 2],
        "W_ZWECK": [7, 8, 7, 8], "W_RBW": [0, 0, 0, 0], "W_SO1": [1, 1, 1, 1],
        "wegkm_imp": [3.0, 3.0, 3.0, 3.0],
    })


def _remapped_source(*, passenger_key):
    donors = _diary_donors()
    out, _trace, report = dpm.reassign_diaryless_plan_sources(
        donors, donors, dfm.compute_diary_facts(_diary_wege()), rng=np.random.RandomState(0),
        hard_employment=True, fine_child_age_bands=True, passenger_key=passenger_key, **FLAGS)
    row = out.loc[out["H_ID"] == 1].iloc[0]
    return (row["source_H_ID"], row["source_P_ID"]), report


def test_diary_match_forwards_the_passenger_key():
    assert _remapped_source(passenger_key=False)[0] == (2, 1)
    source, report = _remapped_source(passenger_key=True)
    assert source == (3, 1)
    assert report.n_crossed_passenger_class == 0


def test_diary_match_counts_passenger_class_crossings_in_both_arms():
    _source, report = _remapped_source(passenger_key=False)
    assert report.n_crossed_passenger_class == 1


def _weekend_households():
    return pd.DataFrame({
        "H_ID": [1, 2], "H_GR": [2, 2], "hh_type5": ["couple", "couple"], "oek_status": [3, 3],
        "RegioStaR7": [71, 71], "H_ANZAUTO": [1, 1], "H_GEW": [1.0, 1.0],
    })


def _weekend_persons():
    """HH 1 reports on a weekday (its two adults differ only in P_VAUTO), HH 2 on a weekend."""
    return pd.DataFrame({
        "H_ID": [1, 1, 2, 2], "P_ID": [1, 2, 1, 2],
        "HP_ALTER": [40, 41, 40, 42], "HP_SEX": [1, 1, 1, 1],
        "P_FSCHEIN": [1, 1, 1, 1], "P_TAET": [1, 1, 1, 1], "P_FKARTE": [1, 1, 1, 1],
        "P_VAUTO": [1, 3, 3, 1], "P_GEW": [1.0] * 4, "kernwo": [2, 2, 6, 6],
        "member_imputed": [False] * 4,
        "source_H_ID": [1, 1, 2, 2], "source_P_ID": [1, 2, 1, 2],
    })


def test_weekend_household_match_forwards_the_passenger_key():
    def sources(passenger_key):
        out, _trace, _report = wpm.reassign_weekend_plan_sources(
            _weekend_households(), _weekend_persons(), rng=np.random.RandomState(0),
            passenger_key=passenger_key)
        weekend = out.loc[out["H_ID"] == 2].set_index("P_ID")
        return {p: (row["source_H_ID"], row["source_P_ID"]) for p, row in weekend.iterrows()}

    assert sources(False) == {1: (1, 1), 2: (1, 2)}   # positional pairing ignores the passenger class
    assert sources(True) == {1: (1, 2), 2: (1, 1)}    # never-passenger P1 gets the never-passenger day


def test_count_passenger_class_crossings_compares_person_and_plan_source():
    persons = _weekend_persons().assign(source_H_ID=[1, 1, 1, 1], source_P_ID=[1, 2, 1, 2])
    n_remapped, n_crossed = cd.count_passenger_class_crossings(persons)
    assert (n_remapped, n_crossed) == (2, 2)


def test_completed_donor_configure_declares_the_passenger_keys_default_on():
    from braunschweig.popsim.stage import (
        KEY_DONOR_MATCH_LIFT_FREE_NEVER_PASSENGER, KEY_DONOR_MATCH_PASSENGER_ACCESS,
    )

    calls = {}

    class _Context:
        def config(self, key, default=None):
            calls[key] = default
            return default

    cd.configure(_Context())
    assert calls[KEY_DONOR_MATCH_PASSENGER_ACCESS] is True
    assert calls[KEY_DONOR_MATCH_LIFT_FREE_NEVER_PASSENGER] is True


def test_build_completed_donor_wires_the_passenger_keys_and_logs_their_rates(tmp_path, monkeypatch, caplog):
    """The stage wires the class key into both matchers only with the MiD passenger availability on (P_VAUTO is
    loaded only then), the lift-free preference (ADR-0166) only on top of the class key, and logs the surviving
    crossing rate and the residual lift rate."""
    seen = {}

    def fake_weekend(households, persons, *, rng, fine_child_age_bands, precompute_matching, passenger_key,
                     car_passenger_diaries):
        seen["weekend"] = (passenger_key, _keys(car_passenger_diaries))
        return persons, pd.DataFrame(), None

    def fake_diary(persons, donors, facts, **kwargs):
        seen["diary"] = (kwargs["passenger_key"], _keys(kwargs["car_passenger_diaries"]))
        return persons, pd.DataFrame(), None

    def _keys(index):
        return None if index is None else sorted(index.tolist())

    persons = _weekend_persons().assign(mobil=1)
    monkeypatch.setattr(cd.mid_donor, "load_completed_donor",
                        lambda *a, **k: (_weekend_households(), persons, _Completeness(), _Report()))
    monkeypatch.setattr(cd.mid_donor, "load_mid_wege", lambda mid_dir: _diary_wege())
    monkeypatch.setattr(cd.weekend_plan_match, "reassign_weekend_plan_sources", fake_weekend)
    monkeypatch.setattr(cd.diary_plan_match, "reassign_diaryless_plan_sources", fake_diary)
    monkeypatch.setattr(cd.diary_facts, "attach_plan_source_facts", lambda p, f: p)
    monkeypatch.setattr(cd.passenger_availability, "attach_car_passenger_diary_evidence",
                        lambda p, w: p.assign(src_has_car_passenger_trip=False))
    lifts = [(2, 1), (3, 1)]
    monkeypatch.setattr(cd.passenger_availability, "car_passenger_diary_keys",
                        lambda wege: pd.MultiIndex.from_tuples(lifts))
    caplog.set_level(logging.INFO)

    def build(**flags):
        seen.clear()
        cd.build_completed_donor(tmp_path, random_seed=1, seed_day_filter=None, weekend_plan_match_on=True,
                                 diary_plan_match_on=True, exclude_holiday_plan_sources=False, **flags)
        return dict(seen)

    assert build(passenger_availability_enabled=True) == {"weekend": (True, lifts), "diary": (True, lifts)}
    assert "[completed_donor] passenger access class" in caplog.text
    assert "lift in a plan source of another respondent" in caplog.text
    assert (build(passenger_availability_enabled=True, donor_match_lift_free_never_passenger=False)
            == {"weekend": (True, None), "diary": (True, None)})
    assert (build(passenger_availability_enabled=True, donor_match_passenger_access=False)
            == {"weekend": (False, None), "diary": (False, None)})
    assert build(passenger_availability_enabled=False) == {"weekend": (False, None), "diary": (False, None)}


# --- ADR-0166: "never" passengers prefer a donor whose diary is lift-free -------------------------------------
# A same-class donor is not always a consistent donor: a "never" respondent may report a lift themselves (the
# own-answer conflict of ADR-0121). On the raw MiD donor 496 of the 1,782 residual cases of ADR-0162 came from
# such donors in the diary re-match and 889 from the household alignment handing a lift diary across the class.

LIFT = wpm.DONOR_LIFT_COLUMN


def _never_pool_with_one_lift():
    """Two "never" donors identical in every key; the HEAVIER one reports a lift in its own diary."""
    return _weekday_pool().assign(P_VAUTO=[3, 3], **{LIFT: [True, False]})


def test_lift_free_preference_gives_a_never_passenger_the_lift_free_never_donor():
    pool, target = _never_pool_with_one_lift(), _never_passenger_target()
    off_rng, on_rng = np.random.RandomState(0), np.random.RandomState(0)

    class_only = wpm.match_person(target, pool, rng=off_rng, passenger_key=True)
    lift_free = wpm.match_person(target, pool, rng=on_rng, passenger_key=True, lift_free_never=True)

    assert class_only[:2] == (10, 1)                 # ADR-0162: both are "never", the heavier lift donor wins
    assert lift_free == (11, 1, 0)                   # the lift-free never donor, without relaxing a key
    assert (off_rng.get_state()[1] == on_rng.get_state()[1]).all()   # one draw either way


def test_lift_free_preference_leaves_a_passenger_capable_person_with_its_own_class():
    pool = _never_pool_with_one_lift().assign(P_VAUTO=[1, 3], **{LIFT: [False, False]})
    target = _never_passenger_target().copy()
    target["P_VAUTO"] = 1
    assert wpm.match_person(target, pool, rng=np.random.RandomState(0), passenger_key=True,
                            lift_free_never=True) == (10, 1, 0)


def test_prepared_pool_with_lift_free_preference_matches_the_unprepared_path():
    pool, target = _never_pool_with_one_lift(), _never_passenger_target()
    prepared = wpm.prepare_person_pool(pool, passenger_key=True, lift_free_never=True)
    kwargs = dict(passenger_key=True, lift_free_never=True)
    assert (wpm.match_person(target, pool, rng=np.random.RandomState(0), prepared_pool=prepared, **kwargs)
            == wpm.match_person(target, pool, rng=np.random.RandomState(0), **kwargs))
    with pytest.raises(ValueError, match="lift_free_never"):
        wpm.match_person(target, pool, rng=np.random.RandomState(0), passenger_key=True, prepared_pool=prepared)


@pytest.mark.parametrize("kwargs, error", [
    (dict(passenger_key=False, lift_free_never=True), ValueError),   # the preference refines the class key
    (dict(passenger_key=True, lift_free_never=True), KeyError),      # the pool lacks the lift column
])
def test_lift_free_preference_fails_fast_on_an_unusable_call(kwargs, error):
    with pytest.raises(error):
        wpm.match_person(_never_passenger_target(), _weekday_pool(), rng=np.random.RandomState(0), **kwargs)


def test_align_members_gives_a_never_passenger_a_lift_free_day_of_its_age_band_before_a_lift():
    """The household offers no lift-free "never" donor: a lift-free donor of the other class in the age band
    beats the same-sex donor whose day has a lift; the pair count is unchanged."""
    donor = pd.DataFrame({"HP_ALTER": [40, 42, 8], "HP_SEX": [1, 2, 1], "P_VAUTO": [1, 1, 402],
                          LIFT: [True, False, False]})
    target = pd.DataFrame({"HP_ALTER": [41, 9], "HP_SEX": [1, 1], "P_VAUTO": [3, 402]})

    assert wpm.align_members(target, donor, passenger_key=True) == [(0, 0), (1, 2)]
    assert wpm.align_members(target, donor, passenger_key=True, lift_free_never=True) == [(0, 1), (1, 2)]


def test_align_members_prefers_the_lift_free_never_donor_over_the_never_donor_with_a_lift():
    donor = pd.DataFrame({"HP_ALTER": [40, 43], "HP_SEX": [1, 1], "P_VAUTO": [3, 3], LIFT: [True, False]})
    target = pd.DataFrame({"HP_ALTER": [41], "HP_SEX": [1], "P_VAUTO": [3]})
    assert wpm.align_members(target, donor, passenger_key=True) == [(0, 0)]
    assert wpm.align_members(target, donor, passenger_key=True, lift_free_never=True) == [(0, 1)]


def _lift_keys(*pairs):
    return pd.MultiIndex.from_tuples(list(pairs), names=["H_ID", "P_ID"])


def test_weekend_household_match_uses_the_lift_of_the_diary_the_donor_hands_over():
    """Weekday HH 1 has two "never" adults; only P1's own day carries a lift. The weekend "never" adult P1 of
    HH 2 is paired with HH 1's P1 by the class key alone and with P2's lift-free day under the preference; the
    returned frame keeps its columns (the lift column is internal)."""
    persons = _weekend_persons().assign(P_VAUTO=[3, 3, 3, 1])

    def sources(car_passenger_diaries):
        out, _trace, _report = wpm.reassign_weekend_plan_sources(
            _weekend_households(), persons, rng=np.random.RandomState(0), passenger_key=True,
            car_passenger_diaries=car_passenger_diaries)
        assert list(out.columns) == list(persons.columns)
        weekend = out.loc[out["H_ID"] == 2].set_index("P_ID")
        return {p: (row["source_H_ID"], row["source_P_ID"]) for p, row in weekend.iterrows()}

    assert sources(None)[1] == (1, 1)
    assert sources(_lift_keys((1, 1)))[1] == (1, 2)


def test_diary_match_prefers_the_lift_free_never_donor():
    donors = _diary_donors().assign(P_VAUTO=[3, 3, 3])     # donor 2 (heavy) and donor 3 are both "never"

    def source(car_passenger_diaries):
        out, _trace, report = dpm.reassign_diaryless_plan_sources(
            donors, donors, dfm.compute_diary_facts(_diary_wege()), rng=np.random.RandomState(0),
            hard_employment=True, fine_child_age_bands=True, passenger_key=True,
            car_passenger_diaries=car_passenger_diaries, **FLAGS)
        assert list(out.columns) == list(donors.columns)
        row = out.loc[out["H_ID"] == 1].iloc[0]
        return row["source_H_ID"], row["source_P_ID"]

    assert source(None) == (2, 1)
    assert source(_lift_keys((2, 1))) == (3, 1)


def test_lift_free_preference_without_any_lift_reproduces_the_class_key_assignment():
    """With no lift in any diary the preference must reduce exactly to ADR-0162 (same sources, same rng state):
    it may only reorder candidates by the lift flag, never by anything else."""
    persons = _weekend_persons().assign(P_VAUTO=[3, 3, 3, 1])
    donors = _diary_donors().assign(P_VAUTO=[3, 3, 3])
    facts = dfm.compute_diary_facts(_diary_wege())
    results = {}
    for name, lifts in (("class_key", None), ("no_lift", _lift_keys())):
        rng = np.random.RandomState(7)
        weekend, _t, _r = wpm.reassign_weekend_plan_sources(
            _weekend_households(), persons, rng=rng, passenger_key=True, car_passenger_diaries=lifts)
        diary, _t, _r = dpm.reassign_diaryless_plan_sources(
            donors, donors, facts, rng=rng, hard_employment=True, fine_child_age_bands=True, passenger_key=True,
            car_passenger_diaries=lifts, **FLAGS)
        results[name] = (weekend, diary, rng.get_state()[1])
    for off, on in zip(results["class_key"], results["no_lift"]):
        if isinstance(off, pd.DataFrame):
            pd.testing.assert_frame_equal(off, on)
        else:
            assert (off == on).all()


def test_car_passenger_diary_keys_follow_the_evidence_rule():
    """A non-rbW trip with main mode 3 (car passenger) counts; an rbW summary row or another mode does not."""
    wege = pd.DataFrame({"H_ID": [1, 2, 3, 3], "P_ID": [1, 1, 1, 1], "hvm_imp": [3, 3, 1, 3],
                         "W_RBW": [0, 1, 0, 0]})
    keys = cd.passenger_availability.car_passenger_diary_keys(wege)
    assert sorted(keys.tolist()) == [(1, 1), (3, 1)]


class _Report:
    n_households_filled = 0
    n_persons_added = 0


class _Completeness:
    completeness_rate = 1.0
