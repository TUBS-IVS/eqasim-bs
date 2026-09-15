"""Exact-equivalence contracts for prepared person donor pools."""

import logging
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import completed_donor as cd
from braunschweig.popsim import diary_facts
from braunschweig.popsim import diary_plan_match as dpm
from braunschweig.popsim import weekend_plan_match as wpm
from tests.test_diary_plan_match import FLAGS, _child_donors, _child_wege, _donors, _wege
from tests.test_weekend_plan_match import _hard_key_pool, _hard_key_targets, _random_weekend_population


def _assert_random_state_equal(left, right):
    assert left[0] == right[0]
    np.testing.assert_array_equal(left[1], right[1])
    assert left[2:] == right[2:]


def _matching_row(**changes):
    row = {
        "H_ID": 10,
        "P_ID": 1,
        "HP_ALTER": 40,
        "HP_SEX": 1,
        "P_FSCHEIN": 1,
        "P_TAET": 1,
        "P_FKARTE": 4,
        "P_GEW": 1.0,
    }
    row.update(changes)
    return row


def _target(**changes):
    row = _matching_row()
    row.pop("H_ID")
    row.pop("P_ID")
    row.pop("P_GEW")
    row.update(changes)
    return pd.Series(row)


@pytest.mark.parametrize(
    "changes, expected_level",
    [
        ({}, 0),
        ({"P_FKARTE": 1}, 1),
        ({"P_TAET": 11}, 2),
        ({"HP_ALTER": 8}, 3),
        ({"HP_SEX": 2}, 4),
        ({"P_FSCHEIN": 2}, 5),
    ],
)
def test_prepared_pool_matches_every_soft_relaxation_level(changes, expected_level):
    pool = pd.DataFrame([_matching_row(**changes)], index=[37])
    old_rng = np.random.RandomState(1234)
    new_rng = np.random.RandomState(1234)
    old_rng.normal()
    new_rng.normal()  # make the Gaussian-cache fields non-default and observable

    old = wpm.match_person(_target(), pool, rng=old_rng)
    prepared = wpm.prepare_person_pool(pool)
    new = wpm.match_person(_target(), pool, rng=new_rng, prepared_pool=prepared)

    assert old == new == (10, 1, expected_level)
    _assert_random_state_equal(old_rng.get_state(), new_rng.get_state())


def test_prepared_pool_preserves_hard_only_and_whole_pool_fallbacks(caplog):
    caplog.set_level(logging.DEBUG, logger="braunschweig.popsim.weekend_plan_match")
    hard_only = pd.DataFrame([
        _matching_row(H_ID=20, HP_ALTER=8, HP_SEX=2, P_FSCHEIN=2, P_FKARTE=1),
    ], index=[91])
    whole_pool = hard_only.assign(P_TAET=11)

    for pool in (hard_only, whole_pool):
        old_rng = np.random.RandomState(11)
        new_rng = np.random.RandomState(11)
        hard_keys = frozenset({"employed"})
        old = wpm.match_person(_target(), pool, rng=old_rng, hard_keys=hard_keys)
        new = wpm.match_person(
            _target(), pool, rng=new_rng, hard_keys=hard_keys,
            prepared_pool=wpm.prepare_person_pool(pool),
        )
        assert old == new == (20, 1, 4)
        _assert_random_state_equal(old_rng.get_state(), new_rng.get_state())

    assert any("whole-pool" in record.getMessage() for record in caplog.records)


@pytest.mark.parametrize("edges", [wpm.AGE_BAND_EDGES, wpm.FINE_CHILD_AGE_BAND_EDGES])
def test_prepared_pool_preserves_weighted_draws_order_and_full_rng_state(edges):
    pool = pd.DataFrame([
        _matching_row(H_ID=30, P_GEW=0.5),
        _matching_row(H_ID=10, P_GEW=7.0),
        _matching_row(H_ID=20, P_GEW=2.5),
    ], index=[101, -7, 44])
    pool = pool.iloc[[2, 0, 1]]
    targets = [_target() for _ in range(40)] + [_target(HP_ALTER=7) for _ in range(10)]
    old_rng = np.random.RandomState(7)
    new_rng = np.random.RandomState(7)
    old_rng.normal()
    new_rng.normal()

    old = [wpm.match_person(row, pool, rng=old_rng, age_band_edges=edges) for row in targets]
    prepared = wpm.prepare_person_pool(pool, age_band_edges=edges)
    new = [
        wpm.match_person(
            row, pool, rng=new_rng, age_band_edges=edges, prepared_pool=prepared)
        for row in targets
    ]

    assert old == new
    assert len({household_id for household_id, _person_id, _level in old}) > 1
    _assert_random_state_equal(old_rng.get_state(), new_rng.get_state())


def test_prepared_pool_preserves_missing_key_relaxation_and_uniform_weight_warning(caplog):
    pool = pd.DataFrame([
        _matching_row(H_ID=30, HP_ALTER=np.nan, P_GEW=np.nan),
        _matching_row(H_ID=20, HP_ALTER=np.nan, P_GEW=0.0),
    ], index=[12, 3])
    target = _target(HP_ALTER=np.nan)
    old_rng = np.random.RandomState(5)
    new_rng = np.random.RandomState(5)

    old = wpm.match_person(target, pool, rng=old_rng)
    new = wpm.match_person(
        target, pool, rng=new_rng, prepared_pool=wpm.prepare_person_pool(pool))

    assert old == new
    assert old[2] == 3  # age_band is relaxed before either missing age can match
    _assert_random_state_equal(old_rng.get_state(), new_rng.get_state())
    warnings = [r for r in caplog.records if "uniform draw" in r.getMessage()]
    assert len(warnings) == 2


def test_prepare_person_pool_does_not_mutate_input_and_rejects_stale_or_unrelated_pool():
    pool = _hard_key_pool().set_axis(np.arange(12) * 5 + 2)
    before = pool.copy(deep=True)
    prepared = wpm.prepare_person_pool(pool)
    pd.testing.assert_frame_equal(pool, before, check_exact=True)

    with pytest.raises(ValueError, match="different person pool"):
        wpm.match_person(
            _hard_key_targets(1)[0], pool.copy(), rng=np.random.RandomState(0),
            prepared_pool=prepared,
        )

    pool.loc[pool.index[0], "P_GEW"] += 1.0
    with pytest.raises(ValueError, match="changed since preparation"):
        wpm.match_person(
            _hard_key_targets(1)[0], pool, rng=np.random.RandomState(0),
            prepared_pool=prepared,
        )


def test_prepared_pool_rejects_age_edges_mismatch_and_preserves_empty_error():
    pool = _hard_key_pool()
    prepared = wpm.prepare_person_pool(pool)
    with pytest.raises(ValueError, match="age_band_edges"):
        wpm.match_person(
            _hard_key_targets(1)[0], pool, rng=np.random.RandomState(0),
            age_band_edges=wpm.FINE_CHILD_AGE_BAND_EDGES, prepared_pool=prepared,
        )

    empty = pd.DataFrame(columns=[
        "H_ID", "P_ID", "HP_ALTER", "HP_SEX", "P_FSCHEIN", "P_TAET", "P_FKARTE",
    ])
    prepared_empty = wpm.prepare_person_pool(empty)
    with pytest.raises(ValueError, match="empty weekday person pool") as old_error:
        wpm.match_person(_target(), empty, rng=np.random.RandomState(0))
    with pytest.raises(ValueError, match="empty weekday person pool") as new_error:
        wpm.match_person(
            _target(), empty, rng=np.random.RandomState(0), prepared_pool=prepared_empty)
    assert str(old_error.value) == str(new_error.value)


def test_prepared_pool_extracts_donor_features_once_for_multiple_real_matches(monkeypatch):
    pool = _hard_key_pool()
    targets = _hard_key_targets(8)
    original = wpm._person_keys
    donor_calls = 0

    def counted(persons, **kwargs):
        nonlocal donor_calls
        if persons is pool:
            donor_calls += 1
        return original(persons, **kwargs)

    monkeypatch.setattr(wpm, "_person_keys", counted)
    for target in targets:
        wpm.match_person(target, pool, rng=np.random.RandomState(3))
    assert donor_calls == len(targets)

    donor_calls = 0
    prepared = wpm.prepare_person_pool(pool)
    for target in targets:
        wpm.match_person(
            target, pool, rng=np.random.RandomState(3), prepared_pool=prepared)
    assert donor_calls == 1


def test_weekend_reassign_precomputed_path_is_exact_and_preserves_coarse_fallback():
    households, persons = _random_weekend_population()
    old_rng = np.random.RandomState(23)
    new_rng = np.random.RandomState(23)
    old_rng.normal()
    new_rng.normal()

    old = wpm.reassign_weekend_plan_sources(
        households, persons, rng=old_rng, fine_child_age_bands=True,
        precompute_matching=False,
    )
    new = wpm.reassign_weekend_plan_sources(
        households, persons, rng=new_rng, fine_child_age_bands=True,
        precompute_matching=True,
    )

    pd.testing.assert_frame_equal(old[0], new[0], check_exact=True)
    pd.testing.assert_frame_equal(old[1], new[1], check_exact=True)
    assert old[2] == new[2]
    _assert_random_state_equal(old_rng.get_state(), new_rng.get_state())


@pytest.mark.parametrize("fine_child_age_bands", [False, True])
def test_diary_reassign_precomputed_path_is_exact(fine_child_age_bands):
    donors = _child_donors()
    facts = diary_facts.compute_diary_facts(_child_wege())
    old_rng = np.random.RandomState(3)
    new_rng = np.random.RandomState(3)
    old_rng.normal()
    new_rng.normal()

    old = dpm.reassign_diaryless_plan_sources(
        donors, donors, facts, rng=old_rng, hard_employment=True,
        fine_child_age_bands=fine_child_age_bands, precompute_matching=False, **FLAGS,
    )
    new = dpm.reassign_diaryless_plan_sources(
        donors, donors, facts, rng=new_rng, hard_employment=True,
        fine_child_age_bands=fine_child_age_bands, precompute_matching=True, **FLAGS,
    )

    pd.testing.assert_frame_equal(old[0], new[0], check_exact=True)
    pd.testing.assert_frame_equal(old[1], new[1], check_exact=True)
    assert old[2] == new[2]
    _assert_random_state_equal(old_rng.get_state(), new_rng.get_state())


def test_diary_reassign_no_match_is_a_true_rng_and_frame_noop():
    donors = _donors()
    facts = diary_facts.compute_diary_facts(_wege())
    persons = donors.loc[donors["H_ID"] == 5].copy()
    before = persons.copy(deep=True)
    rng = np.random.RandomState(17)
    state_before = rng.get_state()

    out, trace, report = dpm.reassign_diaryless_plan_sources(
        persons, donors, facts, rng=rng, hard_employment=True,
        fine_child_age_bands=True, precompute_matching=True, **FLAGS,
    )

    pd.testing.assert_frame_equal(out, before, check_exact=True)
    assert list(trace["reason"]) == ["realisable"]
    assert report.n_remapped == 0
    _assert_random_state_equal(state_before, rng.get_state())


def test_completed_donor_declares_default_on_matching_performance_flag():
    class Recorder:
        def __init__(self):
            self.calls = {}

        def config(self, key, default=None):
            self.calls[key] = default

    context = Recorder()
    cd.configure(context)
    assert context.calls["braunschweig.performance.donor_matching"] is True


def test_completed_donor_execute_forwards_matching_performance_flag(monkeypatch, tmp_path):
    captured = {}

    def fake_build(*args, **kwargs):
        captured.update(kwargs)
        report = SimpleNamespace(n_households_filled=0, n_persons_added=0)
        completeness = SimpleNamespace(completeness_rate=1.0)
        return cd.CompletedDonor(
            households=pd.DataFrame(), persons=pd.DataFrame(),
            completeness_report=completeness, completion_report=report,
            weekend_report=None, diary_report=None,
        )

    class Context:
        values = {
            "braunschweig.population.popsim.mid_raw_path": str(tmp_path),
            "random_seed": 3,
            "braunschweig.population.popsim.seed_day_filter": "default",
            "braunschweig.population.popsim.weekend_plan_match": False,
            "braunschweig.population.popsim.diary_plan_match": False,
            "braunschweig.population.popsim.exclude_holiday_plan_sources": True,
            "braunschweig.population.popsim.exclude_rbw_legs": True,
            "braunschweig.population.popsim.drop_leading_arrive_home_leg": True,
            "braunschweig.population.popsim.diary_match_hard_employment": True,
            "braunschweig.population.popsim.donor_match_fine_child_age_bands": True,
            "braunschweig.performance.donor_matching": False,
        }

        def config(self, key):
            return self.values[key]

        def path(self):
            return str(tmp_path)

        def set_info(self, key, value):
            pass

    monkeypatch.setattr(cd, "build_completed_donor", fake_build)
    cd.execute(Context())
    assert captured["precompute_matching"] is False
