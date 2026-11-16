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
from tests.test_weekend_plan_match import (
    BASELINE_PERSON_DRAWS, _hard_key_pool, _hard_key_targets, _random_weekend_population,
)


def _assert_random_state_equal(left, right):
    assert left[0] == right[0]
    np.testing.assert_array_equal(left[1], right[1])
    assert left[2:] == right[2:]


def _captured_records(caplog, logger_name):
    return [
        (record.name, record.levelno, record.getMessage())
        for record in caplog.records if record.name == logger_name
    ]


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


def test_prepared_shared_seed_matches_off_and_frozen_baseline_with_exact_types():
    pool = _hard_key_pool()
    targets = _hard_key_targets()
    off_rng = np.random.RandomState(7)
    on_rng = np.random.RandomState(7)

    off = [wpm.match_person(target, pool, rng=off_rng) for target in targets]
    prepared = wpm.prepare_person_pool(pool)
    on = [
        wpm.match_person(target, pool, rng=on_rng, prepared_pool=prepared)
        for target in targets
    ]

    assert off == on == BASELINE_PERSON_DRAWS
    assert [[type(value) for value in draw] for draw in off] == [
        [type(value) for value in draw] for draw in BASELINE_PERSON_DRAWS
    ]
    assert [[type(value) for value in draw] for draw in on] == [
        [type(value) for value in draw] for draw in BASELINE_PERSON_DRAWS
    ]
    _assert_random_state_equal(off_rng.get_state(), on_rng.get_state())


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
    logger_name = "braunschweig.popsim.weekend_plan_match"
    caplog.set_level(logging.DEBUG, logger=logger_name)
    hard_only = pd.DataFrame([
        _matching_row(H_ID=20, HP_ALTER=8, HP_SEX=2, P_FSCHEIN=2, P_FKARTE=1),
    ], index=[91])
    whole_pool = hard_only.assign(P_TAET=11)

    for pool in (hard_only, whole_pool):
        caplog.clear()
        old_rng = np.random.RandomState(11)
        hard_keys = frozenset({"employed"})
        old = wpm.match_person(_target(), pool, rng=old_rng, hard_keys=hard_keys)
        old_records = _captured_records(caplog, logger_name)

        caplog.clear()
        new_rng = np.random.RandomState(11)
        new = wpm.match_person(
            _target(), pool, rng=new_rng, hard_keys=hard_keys,
            prepared_pool=wpm.prepare_person_pool(pool),
        )
        new_records = _captured_records(caplog, logger_name)
        assert old == new == (20, 1, 4)
        _assert_random_state_equal(old_rng.get_state(), new_rng.get_state())
        assert new_records == old_records

    assert new_records == [(
        logger_name,
        logging.DEBUG,
        "[weekend_plan_match] match_person hit the whole-pool size-only fallback "
        "(all soft person keys dropped and no donor shares the hard key(s) "
        "['employed']); drew weekday (20, 1).",
    )]


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
    logger_name = "braunschweig.popsim.sampling"
    caplog.set_level(logging.WARNING, logger=logger_name)
    pool = pd.DataFrame([
        _matching_row(H_ID=30, HP_ALTER=np.nan, P_GEW=np.nan),
        _matching_row(H_ID=20, HP_ALTER=np.nan, P_GEW=0.0),
    ], index=[12, 3])
    target = _target(HP_ALTER=np.nan)
    old_rng = np.random.RandomState(5)
    new_rng = np.random.RandomState(5)

    old = wpm.match_person(target, pool, rng=old_rng)
    old_records = _captured_records(caplog, logger_name)
    caplog.clear()
    new = wpm.match_person(
        target, pool, rng=new_rng, prepared_pool=wpm.prepare_person_pool(pool))
    new_records = _captured_records(caplog, logger_name)

    assert old == new
    assert old[2] == 3  # age_band is relaxed before either missing age can match
    _assert_random_state_equal(old_rng.get_state(), new_rng.get_state())
    assert new_records == old_records == [(
        logger_name,
        logging.WARNING,
        "[sampling.weighted_choice] all 2 candidate weights are NaN/<=0; "
        "falling back to a uniform draw.",
    )]


def test_prepare_person_pool_does_not_mutate_input_and_rejects_unrelated_pool():
    pool = _hard_key_pool().set_axis(np.arange(12) * 5 + 2)
    before = pool.copy(deep=True)
    prepared = wpm.prepare_person_pool(pool)
    pd.testing.assert_frame_equal(pool, before, check_exact=True)

    with pytest.raises(ValueError, match="different person pool"):
        wpm.match_person(
            _hard_key_targets(1)[0], pool.copy(), rng=np.random.RandomState(0),
            prepared_pool=prepared,
        )


@pytest.mark.parametrize("mutation", ["HP_ALTER", "H_ID", "P_ID", "P_GEW", "index"])
def test_prepared_pool_validate_rejects_each_relevant_same_object_mutation(mutation):
    pool = _hard_key_pool().set_axis(np.arange(12) * 5 + 2)
    prepared = wpm.prepare_person_pool(pool)
    prepared.validate(pool, wpm.AGE_BAND_EDGES)  # unchanged pool passes
    if mutation == "index":
        pool.index = pool.index + 100
    elif mutation == "HP_ALTER":
        pool.iat[0, pool.columns.get_loc(mutation)] = 8
    else:
        column = pool.columns.get_loc(mutation)
        pool.iat[0, column] = pool.iat[0, column] + 1

    with pytest.raises(ValueError, match="changed since preparation"):
        prepared.validate(pool, wpm.AGE_BAND_EDGES)


def _mutate_pool_after_first_call(monkeypatch, module):
    """Replace ``module.match_person`` by a wrapper that changes one P_GEW of the pool it was
    handed right after the first draw, as a buggy caller between two calls would."""
    original = wpm.match_person
    calls = []

    def mutating(target_row, weekday_persons, **kwargs):
        result = original(target_row, weekday_persons, **kwargs)
        if not calls:
            with pd.option_context("mode.chained_assignment", None):
                weekday_persons.iat[0, weekday_persons.columns.get_loc("P_GEW")] += 1.0
        calls.append(result)
        return result

    monkeypatch.setattr(module, "match_person", mutating)
    return calls


@pytest.mark.parametrize("caller", ["diary", "weekend"])
def test_both_callers_reject_a_pool_changed_during_their_match_loop(monkeypatch, caller):
    """match_person checks only the O(1) binding per call; the content check runs in the callers
    once after their loop, so a pool changed between calls must still fail the pass."""
    if caller == "diary":
        donors = _child_donors()
        facts = diary_facts.compute_diary_facts(_child_wege())
        calls = _mutate_pool_after_first_call(monkeypatch, dpm)
        run = lambda: dpm.reassign_diaryless_plan_sources(  # noqa: E731
            donors, donors, facts, rng=np.random.RandomState(3), hard_employment=True,
            fine_child_age_bands=True, precompute_matching=True, **FLAGS)
    else:
        households, persons = _random_weekend_population()
        calls = _mutate_pool_after_first_call(monkeypatch, wpm)
        run = lambda: wpm.reassign_weekend_plan_sources(  # noqa: E731
            households, persons, rng=np.random.RandomState(23), precompute_matching=True)
    with pytest.raises(ValueError, match="changed since preparation"):
        run()
    assert calls  # the loop really ran, so the after-loop check is what raised


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


@pytest.mark.parametrize("precompute_matching", [False, True])
@pytest.mark.parametrize("passenger_availability_enabled", [False, True])
def test_completed_donor_execute_forwards_matching_performance_flag(
    monkeypatch, tmp_path, precompute_matching, passenger_availability_enabled,
):
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
            "braunschweig.population.popsim.donor_match_passenger_access": True,
            "braunschweig.population.popsim.donor_match_lift_free_never_passenger": True,
            "braunschweig.performance.donor_matching": precompute_matching,
            "mid_passenger_availability": passenger_availability_enabled,
        }

        def config(self, key):
            return self.values[key]

        def path(self):
            return str(tmp_path)

        def set_info(self, key, value):
            pass

    monkeypatch.setattr(cd, "build_completed_donor", fake_build)
    cd.execute(Context())
    assert captured["precompute_matching"] is precompute_matching
    assert captured["passenger_availability_enabled"] is passenger_availability_enabled
    assert captured["donor_match_passenger_access"] is True
    assert captured["donor_match_lift_free_never_passenger"] is True


# ---------------------------------------------------------------------------
# Cached candidate cells (audit package H item C): the prepared path caches each key cell's
# sorted items and cumulative weights and keys targets without a one-row DataFrame. The
# unprepared path (precompute_matching=False / no prepared_pool) still runs the per-call
# weighted_choice and _person_keys, so it is the oracle of these randomised comparisons.
# ---------------------------------------------------------------------------

#: Ages on and next to every edge of both edge sets, outside the bands, and missing.
_EDGE_AGES = [-1, 0, 4, 5, 6, 9, 10, 13, 14, 17, 18, 45, 200, 201, np.nan]
_P_VAUTO_CODES = [1, 2, 3, 3, 9, 206, 402, np.nan]


def _random_person_columns(rng, n):
    return {
        "HP_ALTER": rng.choice(_EDGE_AGES, size=n),
        "HP_SEX": rng.choice([1, 2, 1.0, np.nan], size=n),
        "P_FSCHEIN": rng.choice([1, 2, 1.0, np.nan], size=n),
        "P_TAET": rng.choice([1, 5, 7, 8, 11, np.nan], size=n),
        "P_FKARTE": rng.choice([1, 3, 4, 8, np.nan], size=n),
        "P_VAUTO": rng.choice(_P_VAUTO_CODES, size=n),
    }


# Each arm costs ~1-2 s, mostly the uncached oracle; four arms cover both edge sets, no / one / two hard
# keys and the three passenger settings without the full cross product.
@pytest.mark.parametrize("edges, hard_keys, passenger, lift_free", [
    (wpm.AGE_BAND_EDGES, frozenset(), False, False),
    (wpm.FINE_CHILD_AGE_BAND_EDGES, frozenset({"employed"}), True, False),
    (wpm.FINE_CHILD_AGE_BAND_EDGES, frozenset({"has_license", "sex"}), True, True),
    (wpm.AGE_BAND_EDGES, frozenset({"employed"}), True, True),
])
def test_cached_cells_reproduce_the_uncached_draws_on_randomised_edge_values(
        caplog, edges, hard_keys, passenger, lift_free):
    """Catches a cell-cache or target-key defect: a wrong cell (key value, NaN, band edge, P_VAUTO
    code), a stale or shared cumulative weight, a lost NaN/<=0 weight rule or uniform-fallback
    warning, or a second rng draw per call. Duplicate (H_ID, P_ID) items and all-invalid cells
    are in the pool on purpose."""
    rng = np.random.RandomState(41)
    n_donors = 80
    pool = pd.DataFrame({
        "H_ID": rng.randint(1, 30, size=n_donors), "P_ID": rng.randint(1, 4, size=n_donors),
        **_random_person_columns(rng, n_donors),
        "P_GEW": rng.choice([0.5, 1.25, 3.0, 0.0, -1.0, np.nan], size=n_donors),
        wpm.DONOR_LIFT_COLUMN: rng.rand(n_donors) < 0.4,
    }, index=rng.permutation(np.arange(n_donors) * 3 + 7))
    # The text column makes every target row object-typed, the row shape the production callers pass.
    targets = pd.DataFrame(_random_person_columns(rng, 120)).assign(label="target")
    kwargs = dict(hard_keys=hard_keys, age_band_edges=edges, passenger_key=passenger, lift_free_never=lift_free)
    prepared = wpm.prepare_person_pool(pool, age_band_edges=edges, passenger_key=passenger,
                                       lift_free_never=lift_free)
    sampling_logger = "braunschweig.popsim.sampling"
    caplog.set_level(logging.WARNING, logger=sampling_logger)

    old_rng, new_rng = np.random.RandomState(5), np.random.RandomState(5)
    old_rng.normal()
    new_rng.normal()
    old = [wpm.match_person(targets.loc[i], pool, rng=old_rng, **kwargs) for i in targets.index]
    old_records = _captured_records(caplog, sampling_logger)
    caplog.clear()
    new = [wpm.match_person(targets.loc[i], pool, rng=new_rng, prepared_pool=prepared, **kwargs)
           for i in targets.index]

    assert new == old
    assert [[type(v) for v in draw] for draw in new] == [[type(v) for v in draw] for draw in old]
    _assert_random_state_equal(old_rng.get_state(), new_rng.get_state())
    assert _captured_records(caplog, sampling_logger) == old_records
    assert len({level for _h, _p, level in old}) >= 3  # the ladder really relaxed on this input
    prepared.validate(pool, edges, passenger, lift_free)


@pytest.mark.parametrize("age", [None, pd.NA, "30"])
def test_cached_and_uncached_paths_both_refuse_a_non_numeric_target_age(age):
    """A missing-as-object or text age must fail both paths alike, not become 'no band' in one."""
    pool = _hard_key_pool()
    target = _target(HP_ALTER=age, label="target")  # the text value keeps the row object-typed (None stays None)
    assert target["HP_ALTER"] is age
    with pytest.raises(TypeError):
        wpm.match_person(target, pool, rng=np.random.RandomState(0))
    with pytest.raises(TypeError):
        wpm.match_person(target, pool, rng=np.random.RandomState(0), prepared_pool=wpm.prepare_person_pool(pool))


def _lift_keys(rng, donors):
    chosen = donors.loc[rng.rand(len(donors)) < 0.4]
    return pd.MultiIndex.from_arrays([chosen["H_ID"], chosen["P_ID"]], names=["H_ID", "P_ID"])


def _random_diary_population(n_persons=240, seed=13):
    """Donors of which about 40 % have no diary (remapped) and the rest one realisable two-leg day."""
    rng = np.random.RandomState(seed)
    has_diary = rng.rand(n_persons) < 0.6
    persons = pd.DataFrame({
        "H_ID": np.arange(1, n_persons + 1), "P_ID": np.ones(n_persons, dtype=int),
        "anzwege1": np.where(has_diary, 2, 803), "mobil": np.ones(n_persons, dtype=int),
        "feiertag": np.zeros(n_persons, dtype=int), "kernwo": np.full(n_persons, 2),
        "member_imputed": np.zeros(n_persons, dtype=bool),
        "source_H_ID": np.arange(1, n_persons + 1), "source_P_ID": np.ones(n_persons, dtype=int),
        "P_GEW": rng.choice([0.5, 1.25, 3.0, 7.5], size=n_persons),
        "HP_ALTER": rng.choice([4, 5, 6, 9, 10, 13, 14, 17, 18, 45, 70], size=n_persons),
        "HP_SEX": rng.choice([1, 2], size=n_persons),
        "P_FSCHEIN": rng.choice([1, 2], size=n_persons),
        "P_TAET": rng.choice([1, 5, 8, 11], size=n_persons),
        "P_FKARTE": rng.choice([1, 3, 4, 8], size=n_persons),
        "P_VAUTO": rng.choice([1, 2, 3, 9], size=n_persons),
    })
    diary_ids = persons.loc[has_diary, "H_ID"].to_numpy()
    wege = pd.DataFrame({
        "H_ID": np.repeat(diary_ids, 2), "P_ID": 1, "W_ID": np.tile([1, 2], len(diary_ids)),
        "W_ZWECK": np.tile([1, 8], len(diary_ids)), "W_RBW": 0,
        "W_SO1": np.tile([1, 809], len(diary_ids)), "wegkm_imp": 3.0,
    })
    return persons, diary_facts.compute_diary_facts(wege), _lift_keys(rng, persons.loc[has_diary])


@pytest.mark.parametrize("hard_employment, fine_child_age_bands, passenger, lift_free", [
    (False, False, False, False), (True, True, True, False), (True, True, True, True), (False, True, True, True),
])
def test_diary_reassign_cached_path_is_exact_on_a_randomised_population(
        hard_employment, fine_child_age_bands, passenger, lift_free):
    """Whole-pass byte identity of the diary match, prepared (cached cells) vs unprepared."""
    persons, facts, lifts = _random_diary_population()
    flags = dict(hard_employment=hard_employment, fine_child_age_bands=fine_child_age_bands,
                 passenger_key=passenger, car_passenger_diaries=lifts if lift_free else None, **FLAGS)
    old_rng, new_rng = np.random.RandomState(29), np.random.RandomState(29)
    old = dpm.reassign_diaryless_plan_sources(persons, persons, facts, rng=old_rng, precompute_matching=False,
                                              **flags)
    new = dpm.reassign_diaryless_plan_sources(persons, persons, facts, rng=new_rng, precompute_matching=True,
                                              **flags)

    assert old[2].n_remapped > 50
    pd.testing.assert_frame_equal(old[0], new[0], check_exact=True)
    pd.testing.assert_frame_equal(old[1], new[1], check_exact=True)
    assert old[2] == new[2]
    _assert_random_state_equal(old_rng.get_state(), new_rng.get_state())


@pytest.mark.parametrize("fine_child_age_bands, passenger, lift_free", [
    (False, False, False), (True, True, False), (True, True, True),
])
def test_weekend_reassign_cached_path_is_exact_on_a_randomised_population(
        fine_child_age_bands, passenger, lift_free):
    """Whole-pass byte identity of the weekend match, prepared (cached cells) vs unprepared."""
    households, persons = _random_weekend_population()
    rng = np.random.RandomState(31)
    persons = persons.assign(P_VAUTO=rng.choice([1, 2, 3, 9], size=len(persons)))
    # Route persons through match_person, the code under test: no weekday household has size 6, so
    # these weekend households take the person-level fallback, and one weekend-reporting member of
    # each 3+-person weekday household is caught by the safety-net sweep.
    is_weekend = persons.groupby("H_ID")["kernwo"].first().isin([6, 7])
    weekend_ids = is_weekend.index[is_weekend][:15]
    households.loc[households["H_ID"].isin(weekend_ids), "H_GR"] = 6
    weekday_large = households.loc[households["H_ID"].isin(is_weekend.index[~is_weekend])
                                   & (households["H_GR"] >= 3), "H_ID"]
    persons.loc[persons["H_ID"].isin(weekday_large) & (persons["P_ID"] == 1), "kernwo"] = 6
    lifts = _lift_keys(rng, persons) if lift_free else None
    flags = dict(fine_child_age_bands=fine_child_age_bands, passenger_key=passenger, car_passenger_diaries=lifts)
    old_rng, new_rng = np.random.RandomState(37), np.random.RandomState(37)
    old = wpm.reassign_weekend_plan_sources(households, persons, rng=old_rng, precompute_matching=False, **flags)
    new = wpm.reassign_weekend_plan_sources(households, persons, rng=new_rng, precompute_matching=True, **flags)

    assert (old[1]["resolution"] == "person_fallback").sum() + old[2].n_swept > 10
    pd.testing.assert_frame_equal(old[0], new[0], check_exact=True)
    pd.testing.assert_frame_equal(old[1], new[1], check_exact=True)
    assert old[2] == new[2]
    _assert_random_state_equal(old_rng.get_state(), new_rng.get_state())
