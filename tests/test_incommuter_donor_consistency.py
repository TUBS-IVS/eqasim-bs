"""Regressions for raw MiD demographics and chronological cordon plans (#397)."""
import json
import logging
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from braunschweig.data.cordon import plans
from braunschweig.data.hts import mid_donor
from braunschweig.synthesis import incommuters, student_incommuters
from tests.test_mid_donor import _synthetic_mid


def raw_mid():
    households, persons, trips = _synthetic_mid()
    persons = persons.rename(columns={"age": "HP_ALTER", "sex": "HP_SEX"})
    persons["HP_ALTER"] = [55, 25]
    persons["HP_SEX"] = [2, 2]
    return households, persons, trips


def test_raw_mid_demographics_reach_both_incommuter_person_builders(caplog):
    with caplog.at_level(logging.INFO):
        _, donors, _ = mid_donor.build_mid_donor_frames(
            *raw_mid(), rng=np.random.RandomState(1234))
    assert {"age", "sex"} <= set(donors)
    ids = pd.DataFrame({"person_id": [100], "household_id": [100]})
    worker = incommuters._build_persons(
        ids, donors.iloc[[0]], "person_id", ["car"], [3000.0])
    student = student_incommuters._build_student_persons(
        ids, donors.iloc[[1]], ["pt"])
    assert worker[["age", "sex"]].values.tolist() == [[55, "female"]]
    assert student[["age", "sex"]].values.tolist() == [[25, "female"]]
    assert "primary 2/2 (100.0%)" in caplog.text


@pytest.mark.parametrize("purpose", ["work", "education"])
def test_single_outbound_leg_produces_chronological_core_frames(purpose):
    donor_trips = pd.DataFrame({
        "departure_time": [8 * 3600], "arrival_time": [9 * 3600],
        "preceding_purpose": ["home"], "following_purpose": [purpose],
    })
    times = plans.extract_activity_times(donor_trips, purpose=purpose)
    frames = incommuters.assemble_incommuter_core_frames(
        [1], [0], [0], [1000], [1000], ["destination"],
        *([value] for value in times), ["pt"], "EPSG:25832",
        middle_purpose=purpose)
    trips, activities = frames["trips"], frames["activities"]
    assert (trips["trip_duration"] >= 0).all()
    assert np.isfinite(trips[["departure_time", "arrival_time"]]).all().all()
    assert trips.iloc[1].departure_time == activities.iloc[1].end_time
    assert trips.iloc[1].arrival_time == activities.iloc[2].start_time
    assert activities.iloc[1].duration > 0


@pytest.mark.parametrize("purpose", ["work", "education"])
def test_repaired_home_arrival_samples_a_valid_return_duration(purpose, caplog):
    # The second donor supplies an observed 30-minute return journey.
    times = ([28800, 28800], [32400, 32400], [28800, 61200], [32400, 63000])
    with caplog.at_level(logging.INFO):
        result = plans.impute_incommuter_times(*times, middle_purpose=purpose)
    dh, am, dm, ah = result
    assert (dh <= am).all() and (am < dm).all() and (dm <= ah).all()
    assert ah[0] - dm[0] == 1800
    assert "home arrival" in caplog.text
    assert "fallback 1/2 (50.0%)" in caplog.text


def test_legacy_off_outputs_match_frozen_pre_fix_baseline():
    baseline = json.loads((Path(__file__).parent / "fixtures" /
                           "incommuter_legacy_397.json").read_text())
    rng = np.random.RandomState(1234)
    frames = mid_donor.build_mid_donor_frames(
        *raw_mid(), rng=rng, preserve_demographics=False)
    assert [frame.to_csv(index=False, lineterminator="\n") for frame in frames] == baseline["frames"]
    assert rng.bytes(32).hex() == baseline["rng_after"]
    for case in baseline["times"]:
        result = plans.impute_incommuter_times(
            *case["input"], middle_purpose=case["purpose"], repair_chronology=False)
        assert [array.tobytes().hex() for array in result] == case["output_bytes"]


def test_complete_valid_times_are_byte_identical_and_inputs_are_not_mutated():
    arrays = tuple(np.array(values, dtype=float) for values in (
        [28800, 81000], [32400, 82800], [61200, 90000], [63000, 91800]))
    before = [a.tobytes() for a in arrays]
    result = plans.impute_incommuter_times(*arrays)
    assert [a.tobytes() for a in arrays] == before
    assert [a.tobytes() for a in result] == before


@pytest.mark.parametrize("bad_column", ["HP_ALTER", "HP_SEX"])
def test_missing_raw_demographic_column_fails_clearly(bad_column):
    h, p, t = raw_mid()
    with pytest.raises(ValueError, match=bad_column):
        mid_donor.build_mid_donor_frames(
            h, p.drop(columns=bad_column), t, rng=np.random.RandomState(1234))


def test_demographic_nonresponse_uses_observed_pool_and_is_deterministic(caplog):
    h, p, t = raw_mid()
    p["HP_SEX"] = [2, 9]
    with caplog.at_level(logging.INFO):
        first = mid_donor.build_mid_donor_frames(h, p, t, np.random.RandomState(1234))[1]
        second = mid_donor.build_mid_donor_frames(h, p, t, np.random.RandomState(1234))[1]
    pd.testing.assert_frame_equal(first, second)
    assert first.sex.tolist() == ["female", "female"]
    assert "fallback 1/2 (50.0%)" in caplog.text


def test_all_missing_sex_cannot_silently_become_all_male():
    h, p, t = raw_mid()
    p["HP_SEX"] = [9, 9]
    with pytest.raises(ValueError, match="HP_SEX.*observed"):
        mid_donor.build_mid_donor_frames(h, p, t, np.random.RandomState(1234))


def test_missing_age_band_uses_global_sex_pool_without_mutating_input():
    h, p, t = raw_mid()
    p.loc[1, ["HP_SEX", "alter_gr1"]] = [9, np.nan]
    before = p.copy(deep=True)
    donors = mid_donor.build_mid_donor_frames(h, p, t, np.random.RandomState(1234))[1]
    assert donors.sex.tolist() == ["female", "female"]
    pd.testing.assert_frame_equal(p, before)


@pytest.mark.parametrize("times", [
    ([np.nan], [np.nan], [np.nan], [np.nan]),
    ([-3600], [-1800], [0], [1800]),
    ([28800], [32400], [61200], [59000]),
    ([28800], [32400], [np.inf], [np.inf]),
    ([], [], [], []),
])
def test_time_repair_covers_missing_negative_and_empty_inputs(times):
    dh, am, dm, ah = plans.impute_incommuter_times(*times)
    assert np.isfinite(np.stack((dh, am, dm, ah))).all()
    assert ((0 <= dh) & (dh <= am) & (am < dm) & (dm <= ah)).all()


@pytest.mark.parametrize("times", [([1, 2], [3], [4], [5]), ([[1]], [[2]], [[3]], [[4]])])
def test_mismatched_time_arrays_fail_clearly(times):
    with pytest.raises(ValueError, match="one-dimensional and equally sized"):
        plans.impute_incommuter_times(*times)


class ConfigContext:
    """Record configure defaults, then enforce synpp's one-argument execute reads."""
    def __init__(self, cfg):
        self.cfg = dict(cfg)

    def config(self, key, *default):
        if default:
            self.cfg.setdefault(key, default[0])
        return self.cfg[key]

    def stage(self, *args, **kwargs):
        pass


@pytest.mark.parametrize("enabled", [True, False])
def test_mid_stage_executes_with_configured_demographic_flag(monkeypatch, enabled):
    from braunschweig.popsim.sources.mid import MidSource
    monkeypatch.setattr(MidSource, "load_donor", lambda self, path: raw_mid())
    ctx = ConfigContext({"random_seed": 1234, mid_donor.KEY_DEMOGRAPHICS: enabled})
    mid_donor.configure(ctx)
    ctx.config = lambda key: ctx.cfg[key]
    donors = mid_donor.execute(ctx)[1]
    assert ("age" in donors and "sex" in donors) == enabled


@pytest.mark.parametrize("module", [incommuters, student_incommuters])
def test_time_flag_is_declared_default_on(module):
    ctx = ConfigContext({
        "cordon_enabled": True, "random_seed": 1234, "data_path": "unused",
        "sampling_rate": 0.01, "braunschweig.political_prefix": ["03101"],
        "cordon_network_source_buffer_m": 45000,
    })
    module.configure(ctx)
    assert ctx.cfg["cordon_incommuter_time_chronology"] is True


def test_demographic_flag_is_declared_default_on():
    ctx = ConfigContext({"random_seed": 1234})
    mid_donor.configure(ctx)
    assert ctx.cfg[mid_donor.KEY_DEMOGRAPHICS] is True


@pytest.mark.parametrize("enabled", [True, False])
def test_worker_builder_forwards_time_flag(enabled):
    from tests.test_incommuter_assembly import _inputs
    gates, assignment, flows, work, persons, trips = _inputs()
    frames = incommuters.build_incommuter_frames(
        flows, {"03101"}, 0.001, gates, assignment, work,
        {">=10": {"car": 1.0}}, persons, trips.iloc[[0]], "person_id", 100, 40,
        np.random.default_rng(7), band_edges=(10,), repair_chronology=enabled)
    assert bool((frames["trips"].trip_duration >= 0).all()) == enabled


@pytest.mark.parametrize("enabled", [True, False])
def test_student_execute_forwards_time_flag(monkeypatch, enabled):
    from tests.test_student_incommuters_stage import _mocked_full_ctx
    ctx = _mocked_full_ctx()
    ctx._cfg["cordon_incommuter_time_chronology"] = enabled
    h, p, t = ctx._stages["hts"]
    ctx._stages["hts"] = h, p, t.iloc[[0]]
    monkeypatch.setattr("braunschweig.data.external_workplaces._load_gemeinden",
                        lambda context: ctx._stages["_fake_gemeinden"])
    monkeypatch.setattr(
        "braunschweig.data.education.student_origins.student_age_pop_by_kreis",
        lambda *args: pd.Series({"03402": 100.0}))
    # A real ExecuteContext rejects a default argument and undeclared keys.
    ctx.config = lambda key: ctx._cfg[key]
    frames = student_incommuters.execute(ctx)
    assert not frames["trips"].empty
    assert bool((frames["trips"].trip_duration >= 0).all()) == enabled


@pytest.mark.parametrize("stage,helper", [
    (mid_donor, "braunschweig.popsim.expand"),
    (incommuters, "braunschweig.data.cordon.plans"),
    (student_incommuters, "braunschweig.data.cordon.plans"),
    (student_incommuters, "braunschweig.synthesis.incommuters"),
])
def test_helper_source_changes_invalidate_stage_cache(monkeypatch, stage, helper):
    import inspect
    before = stage.validate(None)
    getsource = inspect.getsource
    monkeypatch.setattr(inspect, "getsource", lambda module: getsource(module) +
                        ("\n# changed helper\n" if module.__name__ == helper else ""))
    assert stage.validate(None) != before


@pytest.mark.parametrize("enabled", [True, False])
def test_worker_execute_forwards_time_flag(monkeypatch, enabled):
    from tests.test_incommuter_assembly import _inputs
    gates, assignment, flows, work, persons, trips = _inputs()
    ctx = ConfigContext({
        "cordon_enabled": True, "random_seed": 1234, "data_path": "unused",
        "sampling_rate": 0.001, "braunschweig.political_prefix": ["03101"],
        "cordon_incommuter_time_chronology": enabled,
        "cordon_incommuter_real_origin": False,
        "cordon_incommuter_mode_balance": False,
        "cordon_incommuter_mode_reference_by_bundesland": False,
    })
    incommuters.configure(ctx)
    stages = {
        "hts": (None, persons, trips.iloc[[0]]),
        "synthesis.population.enriched": pd.DataFrame({"person_id": [0], "household_id": [0]}),
        "braunschweig.synthesis.cordon_gates": {"gates": gates, "assignment": assignment},
        "braunschweig.data.census.pendler": flows,
        "braunschweig.locations.work": work,
        "braunschweig.data.cordon_pt_gates": None,
        "braunschweig.data.inkar.household_income": None,
    }
    ctx.stage = lambda key: stages[key]
    ctx.config = lambda key: ctx.cfg[key]
    monkeypatch.setattr("braunschweig.data.cordon.network.verify_clip_signature", lambda *args: None)
    monkeypatch.setattr("braunschweig.data.mikrozensus.reference.load_commute_mode_by_distance",
                        lambda path: {"20-50": {"car": 1.0}})
    frames = incommuters.execute(ctx)
    assert not frames["trips"].empty
    assert bool((frames["trips"].trip_duration >= 0).all()) == enabled


@pytest.mark.skipif(not os.environ.get("EQASIM_MID_SMOKE_DIR"),
                    reason="set EQASIM_MID_SMOKE_DIR to the restricted raw MiD directory")
def test_real_mid_sample_preserves_demographics_and_has_ordered_plans():
    """Adapter correctness smoke on 1,000 real households, not population validation."""
    from braunschweig.popsim.sources.mid import MidSource
    h, p, t = MidSource().load_donor(os.environ["EQASIM_MID_SMOKE_DIR"])
    h = h.sample(n=min(1000, len(h)), random_state=1234)
    p = p.loc[p.H_ID.isin(h.H_ID)].copy()
    t = t.loc[t.H_ID.isin(h.H_ID)].copy()
    _, persons, trips = mid_donor.build_mid_donor_frames(h, p, t, np.random.RandomState(1234))
    np.testing.assert_array_equal(persons.age, p.HP_ALTER)
    observed = p.HP_SEX.isin([1, 2]).to_numpy()
    assert persons.loc[observed, "sex"].tolist() == p.loc[observed, "HP_SEX"].map(
        {1: "male", 2: "female"}).tolist()
    for purpose, select in (("work", plans.select_commuter_donors),
                            ("education", plans.select_student_donors)):
        donors = select(persons, trips, "person_id")
        times = [plans.extract_activity_times(trips.loc[trips.person_id == pid], purpose)
                 for pid in donors.person_id]
        result = plans.impute_incommuter_times(*np.asarray(times).T, middle_purpose=purpose)
        dh, am, dm, ah = result
        assert np.isfinite(np.stack(result)).all()
        assert ((dh <= am) & (am < dm) & (dm <= ah)).all()
        print(f"MiD smoke {purpose}: {len(donors)} donors, no chronological violations")
    print(f"MiD smoke: {len(h)} households, {len(persons)} persons, {len(trips)} trips; "
          f"observed sex {observed.sum()}/{len(persons)}")
