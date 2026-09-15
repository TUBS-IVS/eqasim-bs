"""Performance-equivalence tests for population trip validation."""

from __future__ import annotations

from collections import Counter

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import plan_validation
from braunschweig.popsim import trips as popsim_trips
from braunschweig.popsim import trips_stage
from braunschweig.popsim.sources.mid import MidSource
from braunschweig.synthesis.commute_day import donor_pool
from braunschweig.synthesis.commute_day import home_office_donors_stage


def _mixed_trip_rows() -> pd.DataFrame:
    """Return unsorted rows covering every reachable validation diagnostic."""
    rows = [
        {"person_id": "valid", "departure_time": 1000.0, "arrival_time": 1500.0,
         "preceding_purpose": "home", "following_purpose": "work"},
        {"person_id": "valid", "departure_time": 2000.0, "arrival_time": 2500.0,
         "preceding_purpose": "work", "following_purpose": "home"},
        {"person_id": "bad", "departure_time": 4000.0, "arrival_time": 4500.0,
         "preceding_purpose": "work", "following_purpose": "work"},
        {"person_id": "bad", "departure_time": 1000.0, "arrival_time": 500.0,
         "preceding_purpose": "leisure", "following_purpose": "work"},
        {"person_id": "overlap", "departure_time": 400.0, "arrival_time": 600.0,
         "preceding_purpose": "work", "following_purpose": "home"},
        {"person_id": "overlap", "departure_time": 100.0, "arrival_time": 500.0,
         "preceding_purpose": "home", "following_purpose": "work"},
        {"person_id": "nan", "departure_time": np.nan, "arrival_time": np.nan,
         "preceding_purpose": "work", "following_purpose": "leisure"},
        {"person_id": "nan", "departure_time": 100.0, "arrival_time": 200.0,
         "preceding_purpose": "home", "following_purpose": "work"},
        {"person_id": "late", "departure_time": 130000.0, "arrival_time": 130001.0,
         "preceding_purpose": "home", "following_purpose": "home"},
        # pandas groupby drops missing identifiers, and nunique excludes them.
        {"person_id": None, "departure_time": 0.0, "arrival_time": -1.0,
         "preceding_purpose": "work", "following_purpose": "work"},
    ]
    return pd.DataFrame(rows).sample(frac=1.0, random_state=4).reset_index(drop=True)


@pytest.mark.parametrize(
    "require_home_closure, expected_issues",
    [
        (True, [
            ("bad", "departure_after_arrival", "a trip arrives before it departs"),
            ("bad", "no_home_start", "the day does not start at home"),
            ("bad", "no_home_end", "the day does not end at home"),
            ("late", "time_exceeds_bound",
             "a trip time exceeds the 129600s (~36h) plan bound"),
            ("nan", "nan_times",
             "a trip has a NaN departure or arrival time (MiD coded time 99/701)"),
            ("nan", "no_home_end", "the day does not end at home"),
            ("overlap", "negative_activity_duration",
             "a between-trip activity has negative duration"),
            ("overlap", "trip_overlap", "a trip arrives after the next trip departs"),
        ]),
        (False, [
            ("bad", "departure_after_arrival", "a trip arrives before it departs"),
            ("late", "time_exceeds_bound",
             "a trip time exceeds the 129600s (~36h) plan bound"),
            ("nan", "nan_times",
             "a trip has a NaN departure or arrival time (MiD coded time 99/701)"),
            ("overlap", "negative_activity_duration",
             "a between-trip activity has negative duration"),
            ("overlap", "trip_overlap", "a trip arrives after the next trip departs"),
        ]),
    ],
)
def test_vectorized_validation_preserves_exact_report(require_home_closure, expected_issues):
    """Array checks must preserve person/code order, messages, counts, and NaN chronology."""
    trips = _mixed_trip_rows()
    reference = plan_validation.PlanValidator(
        require_home_closure=require_home_closure, vectorized=False,
    ).validate_trips(trips)
    actual = plan_validation.PlanValidator(
        require_home_closure=require_home_closure, vectorized=True,
    ).validate_trips(trips)

    assert actual == reference
    assert actual.n_persons == 5
    assert actual.n_invalid_persons == 4
    assert [(issue.person_id, issue.code, issue.message) for issue in actual.issues] == expected_issues
    assert actual.issue_counts == dict(Counter(code for _, code, _ in expected_issues))
    assert "not_time_sorted" not in actual.issue_counts


@pytest.mark.parametrize("identifier_kind", ["categorical", "nullable_string", "mixed_object"])
def test_vectorized_validation_preserves_supported_identifier_dtypes(identifier_kind):
    """Categorical/string identifiers and dropped missing IDs keep baseline semantics."""
    if identifier_kind == "categorical":
        person_ids = pd.Categorical(["beta", "alpha"], categories=["beta", "alpha"])
        expected_person_order = ["beta", "alpha"]
    elif identifier_kind == "mixed_object":
        person_ids = ["beta", 1]
        expected_person_order = [1, "beta"]
    else:
        person_ids = pd.Series(["beta", pd.NA, "alpha"], dtype="string")
        expected_person_order = ["alpha", "beta"]
    size = len(person_ids)
    trips = pd.DataFrame({
        "person_id": person_ids,
        "departure_time": np.arange(size, dtype=float),
        "arrival_time": np.arange(size, dtype=float),
        "preceding_purpose": ["home"] * size,
        "following_purpose": ["work"] * size,
    })

    old = plan_validation.PlanValidator(vectorized=False).validate_trips(trips)
    new = plan_validation.PlanValidator(vectorized=True).validate_trips(trips)
    assert new == old
    assert [issue.person_id for issue in new.issues] == expected_person_order


@pytest.mark.parametrize(
    "person_ids",
    [
        pd.Series([2, 1], dtype="int64"),
        pd.Series([2, 1], dtype="Int64"),
        pd.Series(pd.Categorical([2, 1], categories=[2, 1])),
        pd.Series(["beta", "alpha"], dtype="string"),
    ],
    ids=["ordinary_int", "nullable_int", "categorical_int", "nullable_string"],
)
def test_vectorized_issue_ids_preserve_group_iterator_scalar_types(person_ids):
    """Issue identifiers must use pandas GroupBy's scalar types, not NumPy coercion."""
    trips = pd.DataFrame({
        "person_id": person_ids,
        "departure_time": [1.0, 1.0],
        "arrival_time": [2.0, 2.0],
        "preceding_purpose": ["home", "home"],
        "following_purpose": ["work", "work"],
    })
    sorted_trips = trips.sort_values(["person_id", "departure_time"])
    reference_ids = [
        person_id for person_id, _group
        in sorted_trips.groupby("person_id", sort=False)
    ]

    old = plan_validation.PlanValidator(vectorized=False).validate_trips(trips)
    new = plan_validation.PlanValidator(vectorized=True).validate_trips(trips)
    old_ids = [issue.person_id for issue in old.issues]
    new_ids = [issue.person_id for issue in new.issues]

    assert old_ids == reference_ids
    assert new_ids == reference_ids
    assert [type(person_id) for person_id in new_ids] == [
        type(person_id) for person_id in reference_ids
    ]


def test_vectorized_validation_without_home_closure_does_not_require_purpose_columns():
    """The reference time-only contract remains valid when closure checks are disabled."""
    trips = pd.DataFrame({
        "person_id": ["person"], "departure_time": [1.0], "arrival_time": [2.0],
    })
    old = plan_validation.PlanValidator(
        require_home_closure=False, vectorized=False,
    ).validate_trips(trips)
    new = plan_validation.PlanValidator(
        require_home_closure=False, vectorized=True,
    ).validate_trips(trips)
    assert new == old


def test_vectorized_validation_preserves_unused_categorical_identifier_error():
    """An unobserved category still exposes the reference empty-group error."""
    trips = pd.DataFrame({
        "person_id": pd.Categorical(["beta", "alpha"], categories=["beta", "alpha", "unused"]),
        "departure_time": [0.0, 0.0],
        "arrival_time": [1.0, 1.0],
        "preceding_purpose": ["home", "home"],
        "following_purpose": ["home", "home"],
    })

    assert plan_validation.PlanValidator(
        require_home_closure=False, vectorized=True,
    ).validate_trips(trips) == plan_validation.PlanValidator(
        require_home_closure=False, vectorized=False,
    ).validate_trips(trips)
    with pytest.raises(IndexError) as old_error:
        plan_validation.PlanValidator(vectorized=False).validate_trips(trips)
    with pytest.raises(type(old_error.value)) as new_error:
        plan_validation.PlanValidator(vectorized=True).validate_trips(trips)
    assert str(new_error.value) == str(old_error.value)


def test_vectorized_validation_preserves_empty_and_single_trip_reports():
    """Boundary offsets must handle zero rows and one-row person chains."""
    columns = ["person_id", "departure_time", "arrival_time",
               "preceding_purpose", "following_purpose"]
    empty = pd.DataFrame(columns=columns)
    single = pd.DataFrame([{
        "person_id": "solo", "departure_time": 1.0, "arrival_time": 2.0,
        "preceding_purpose": "home", "following_purpose": "work",
    }])
    for trips in (empty, single):
        old = plan_validation.PlanValidator(vectorized=False).validate_trips(trips)
        new = plan_validation.PlanValidator(vectorized=True).validate_trips(trips)
        assert new == old


def test_vectorized_validation_eliminates_per_person_frame_checks():
    """The default path must not construct and check one DataFrame per person."""
    class CountingValidator(plan_validation.PlanValidator):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.person_checks = 0

        def _check_person(self, person_id, group):
            self.person_checks += 1
            return super()._check_person(person_id, group)

    trips = _mixed_trip_rows()
    vectorized = CountingValidator()
    reference = CountingValidator(vectorized=False)
    assert vectorized.validate_trips(trips) == reference.validate_trips(trips)
    assert vectorized.person_checks == 0
    assert reference.person_checks == 5


def test_vectorized_repair_preserves_every_pass_and_global_rng_state():
    """Both repair validations keep exact rows/reports without consuming global RNG."""
    trips = pd.DataFrame({
        "person_id": ["open", "valid", "valid"],
        "departure_time": [1000.0, 1000.0, 2000.0],
        "arrival_time": [1100.0, 1100.0, 2100.0],
        "preceding_purpose": ["home", "home", "work"],
        "following_purpose": ["work", "work", "home"],
        "is_first_trip": [True, True, False],
        "is_last_trip": [True, False, True],
    })
    np.random.seed(808)
    state_before = np.random.get_state()
    old_table, old_report = plan_validation.PlanValidator(
        vectorized=False,
    ).repair_trips(trips)
    state_after_old = np.random.get_state()
    new_table, new_report = plan_validation.PlanValidator(
        vectorized=True,
    ).repair_trips(trips)
    state_after_new = np.random.get_state()

    pd.testing.assert_frame_equal(new_table, old_table, check_exact=True)
    assert new_report == old_report
    for after in (state_after_old, state_after_new):
        assert after[0] == state_before[0]
        np.testing.assert_array_equal(after[1], state_before[1])
        assert after[2:] == state_before[2:]


def _trip_build_fixture() -> tuple[pd.DataFrame, pd.DataFrame]:
    persons = pd.DataFrame({
        "person_id": ["person"], "H_ID": [1], "P_ID": [1],
    })
    wege = pd.DataFrame({
        "H_ID": [1, 1], "P_ID": [1, 1], "W_ID": [1, 2],
        "W_ZWECK": [1, 8], "hvm_imp": [4, 4],
        "W_SZS": [8, 17], "W_SZM": [0, 0],
        "W_AZS": [8, 17], "W_AZM": [30, 30],
    })
    return persons, wege


def test_build_validated_trip_table_forwards_explicit_off_switch(monkeypatch):
    """The public table builder must pass its rollback switch to every validation pass."""
    observed = []
    real_validator = plan_validation.PlanValidator

    class CapturingValidator(real_validator):
        def __init__(self, **kwargs):
            observed.append(kwargs["vectorized"])
            super().__init__(**kwargs)

    monkeypatch.setattr(plan_validation, "PlanValidator", CapturingValidator)
    persons, wege = _trip_build_fixture()
    table, report = popsim_trips.build_validated_trip_table(
        persons, wege, vectorized_validation=False,
    )

    assert not table.empty and report.is_valid
    assert observed == [False]


def test_validated_trip_table_resample_is_exact_on_off_and_rng_neutral():
    """Repair plus seeded replacement must preserve rows, dtypes, report, and global RNG."""
    persons = pd.DataFrame({
        "person_id": ["coded", "valid"], "H_ID": [1, 2], "P_ID": [1, 1],
        "ZENSUS100m": ["cell", "cell"],
    })
    wege = pd.DataFrame({
        "H_ID": [1, 2], "P_ID": [1, 1], "W_ID": [1, 1],
        "W_ZWECK": [1, 1], "hvm_imp": [4, 4],
        "W_SZS": [701, 8], "W_SZM": [701, 0],
        "W_AZS": [701, 9], "W_AZM": [701, 0],
        "wegkm_imp": [5.0, 5.0],
    })
    np.random.seed(909)
    state_before = np.random.get_state()
    off_table, off_report = popsim_trips.build_validated_trip_table(
        persons, wege, resample=True, resample_cell_col="ZENSUS100m",
        random_seed=1234, vectorized_validation=False,
    )
    on_table, on_report = popsim_trips.build_validated_trip_table(
        persons, wege, resample=True, resample_cell_col="ZENSUS100m",
        random_seed=1234, vectorized_validation=True,
    )
    state_after = np.random.get_state()

    pd.testing.assert_frame_equal(on_table, off_table, check_exact=True)
    assert on_report == off_report
    assert state_after[0] == state_before[0]
    np.testing.assert_array_equal(state_after[1], state_before[1])
    assert state_after[2:] == state_before[2:]


def test_trips_stage_registers_default_on_and_run_forwards_off(monkeypatch):
    """The main production stage must declare and forward the performance switch."""
    configured = {}

    class ConfigureContext:
        def stage(self, *_args, **_kwargs):
            pass

        def config(self, key, default=None):
            configured[key] = default
            return default

    trips_stage.configure(ConfigureContext())
    assert configured["braunschweig.performance.plan_validation"] is True

    observed = []
    real_builder = popsim_trips.build_validated_trip_table

    def capturing_builder(*args, **kwargs):
        observed.append(kwargs["vectorized_validation"])
        return real_builder(*args, **kwargs)

    monkeypatch.setattr(popsim_trips, "build_validated_trip_table", capturing_builder)
    persons, wege = _trip_build_fixture()
    trips_stage.run(persons, wege, random_seed=1234, vectorized_validation=False)
    assert observed == [False]


def test_mid_source_and_trips_execute_forward_off_switch(monkeypatch):
    """The source adapter and execute plumbing keep the configured OFF path executable."""
    observed = []
    sentinel = pd.DataFrame({"sentinel": [1]})

    def fake_run(*_args, **kwargs):
        observed.append(kwargs["vectorized_validation"])
        return sentinel

    monkeypatch.setattr(trips_stage, "run", fake_run)
    persons, wege = _trip_build_fixture()
    assert MidSource().build_trips(
        persons, wege, random_seed=1, vectorized_validation=False,
    ) is sentinel

    class FakeSource:
        name = "fake"

        def load_donor(self, _mid_dir):
            return None, None, wege

        def build_trips(self, *_args, **kwargs):
            observed.append(kwargs["vectorized_validation"])
            return sentinel

    monkeypatch.setattr("braunschweig.popsim.sources.get_source", lambda _name: FakeSource())

    class ExecuteContext:
        def stage(self, name):
            assert name == "persons"
            return persons

        def config(self, key):
            values = {
                "random_seed": 1,
                "data_path": "unused",
                "braunschweig.population.popsim.mid_dir": "unused",
                "braunschweig.population.popsim.source": "mid",
                "escort_purpose": False,
                "explicit_round_trip_purposes": True,
                "braunschweig.performance.plan_validation": False,
            }
            defaults = {
                "escort_passive_education": False,
                "braunschweig.population.popsim.exclude_rbw_legs": False,
                "braunschweig.population.popsim.drop_leading_arrive_home_leg": False,
                "braunschweig.population.popsim.closure_dwell_model": "fixed_1h",
                "braunschweig.population.popsim.closure_dwell_min_obs": 30,
                "w_zweck_10_as_leisure": False,
                "escort_passive_from_adult": False,
                "escort_passive_pair_max_gap_minutes": 15.0,
                "departure_time_model": "eqasim_uniform",
                "departure_time_mapping_min_reference_n": 200,
                "departure_time_mapping_min_model_n": 50,
                "departure_time_mapping_max_median_shift_hours": 2.0,
            }
            return values[key] if key in values else defaults[key]

    assert trips_stage.execute(ExecuteContext()) is sentinel
    assert observed == [False, False]


def test_donor_builder_and_stage_forward_off_switch(monkeypatch):
    """The home-office donor path must use the same configured validation mode."""
    configured = {}

    class ConfigureContext:
        def stage(self, *_args, **_kwargs):
            pass

        def config(self, key, default=None):
            configured[key] = default
            return default

    home_office_donors_stage.configure(ConfigureContext())
    assert configured["braunschweig.performance.plan_validation"] is True

    observed = []
    empty_trips = pd.DataFrame(columns=list(trips_stage.CONTRACT) + [
        "euclidean_distance", "trip_key",
    ])

    def fake_validated_builder(*_args, **kwargs):
        observed.append(kwargs["vectorized_validation"])
        return empty_trips.copy(), plan_validation.ValidationReport(0, 0, [], {})

    monkeypatch.setattr(donor_pool, "build_validated_trip_table", fake_validated_builder)
    donors = pd.DataFrame({"HP_ID": [1], "H_ID": [1], "P_ID": [1]})
    attributes = pd.DataFrame({
        "donor_id": [1], "sex": ["female"], "age": [40], "employed": [True],
    })
    donor_pool.donor_trips(
        donors, attributes, pd.DataFrame(), random_seed=1,
        escort_purpose=False, escort_passive_education=False,
        explicit_round_trip_purposes=True, vectorized_validation=False,
    )
    assert observed == [False]


def test_home_office_execute_forwards_off_through_real_donor_builder(tmp_path, monkeypatch):
    """The donor stage carries OFF through the builder to donor trip validation."""
    from tests.test_commute_day_stages import (
        _context, _donor_stage_config, _write_raw_mid,
    )

    _write_raw_mid(str(tmp_path))
    observed = []
    real_donor_trips = donor_pool.donor_trips

    def capturing_donor_trips(*args, **kwargs):
        observed.append(kwargs["vectorized_validation"])
        return real_donor_trips(*args, **kwargs)

    monkeypatch.setattr(donor_pool, "donor_trips", capturing_donor_trips)
    context = _context(
        home_office_donors_stage,
        config=_donor_stage_config(
            tmp_path,
            **{plan_validation.KEY_VECTORIZED_PLAN_VALIDATION: False},
        ),
    )
    home_office_donors_stage.execute(context)
    assert observed == [False]
