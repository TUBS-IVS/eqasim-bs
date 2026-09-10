"""Tests for the four commute-day-state synpp stages (Phase B Task 4, issue #244, ADR-0104).

Every stage is driven through a stub context that mirrors synpp's real contract (``stage(name)``
and SINGLE-argument ``config(name)``, both refusing anything ``configure`` did not declare), so a
stage reading an undeclared key or stage fails here rather than first on the run server; the
complementary static guard lives in ``tests/test_execute_context_config_contract.py``.

All model-side frames are synthetic. Two real inputs are used deliberately rather than faked:

* the COMMITTED MiD workday-location table under ``eqasim-data/data/braunschweig/mid/`` -- the
  keep probabilities are read from it, so a schema drift between that file and the state stage
  fails here (the raw MiD it was extracted from is server-only);
* tiny raw-MiD-shaped CSVs written to ``tmp_path`` for the donor stage's ON path, so the real
  ``read_csv``/``usecols`` path and the real pure builder are exercised, not a mock.

The synthetic worker population (see :func:`_work_points`) is built so that every branch of the
state draw is represented: an eligible re-draw, a not-eligible worker, a worker with no donor
distance at all, a far worker and an escort-protected far worker.
"""
from __future__ import annotations

import logging
import os

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

import synthesis.population.activities as base_activities
from braunschweig.calibration.commute_day_state_reference import load_workday_location_table
from braunschweig.popsim.trips_stage import CONTRACT
from braunschweig.synthesis.commute_day import activities_day_stage as ACT
from braunschweig.synthesis.commute_day import home_office_donors_stage as DONORS
from braunschweig.synthesis.commute_day import state_stage as STATE
from braunschweig.synthesis.commute_day import trips_day_stage as TRIPS

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_PATH = os.path.join(REPO, "eqasim-data", "data")
MID_REFERENCE_DIR = os.path.join(DATA_PATH, "braunschweig", "mid")

RANDOM_SEED = 1234
#: Seed of the STATE draw in these tests. Pinned (rather than reusing ``RANDOM_SEED``) because
#: the seeded draw over this tiny 6-worker fixture must actually visit every branch the model
#: has: with this seed persons 1 and 5 end up ``home`` (person 5 via escort protection) and
#: person 4 ``absent``. The model adds ``state.COMMUTE_DAY_SEED_OFFSET`` on top of it.
STATE_DRAW_SEED = 4
CRS = "EPSG:25832"

DONOR_STAGE = "braunschweig.synthesis.commute_day.home_office_donors_stage"
STATE_STAGE = "braunschweig.synthesis.commute_day.state_stage"


# --------------------------------------------------------------------------- stub contexts

class _ConfigureRecorder:
    """Records what ``configure`` declares, with synpp's two-argument config() signature."""

    def __init__(self):
        self.stages = []
        self.config_keys = {}

    def stage(self, name, **_kwargs):
        self.stages.append(name)

    def config(self, name, default=None):
        self.config_keys[name] = default


class _StubContext:
    """synpp's ExecuteContext/ValidateContext contract: ``stage(name)``, SINGLE-arg ``config(name)``.

    Both accessors fail loudly on anything ``configure`` did not declare, so a stage that reads an
    undeclared key or stage cannot pass the way a permissive stub would.
    """

    def __init__(self, declared, stages=None, config=None):
        self._declared = declared
        self._stages = stages or {}
        self._config = config or {}

    def stage(self, name):
        assert name in self._declared.stages, f"stage '{name}' was not declared in configure()"
        assert name in self._stages, f"no stub output for stage '{name}'"
        return self._stages[name]

    def config(self, name):
        assert name in self._declared.config_keys, f"config '{name}' was not declared in configure()"
        return self._config[name]


def _context(module, stages=None, config=None):
    """A stub context for ``module``, declared by running the module's own ``configure``."""
    recorder = _ConfigureRecorder()
    module.configure(recorder)
    return _StubContext(recorder, stages=stages, config=config)


# --------------------------------------------------------------------------- synthetic model input

def _trip(person_id, trip_index, preceding, following, wegkm, departure, arrival, is_last):
    return {
        "person_id": person_id, "trip_index": trip_index,
        "departure_time": float(departure), "arrival_time": float(arrival),
        "preceding_purpose": preceding, "following_purpose": following,
        "is_first_trip": trip_index == 0, "is_last_trip": is_last,
        "trip_duration": float(arrival - departure), "activity_duration": 3600.0,
        "mode": "car", "wegkm_imp": wegkm,
    }


def _trips():
    """Pre-assignment trips: persons 1, 2, 4, 5, 6 work; person 3 and 7 do not.

    Person 5 additionally makes an escort leg, which protects them from the ``absent`` state
    (ADR-0104 Assumption 4). The MiD work-trip length ``wegkm_imp`` is the DONOR distance the
    state model reads (ADR-0104 Amendment 1).
    """
    rows = []
    for person_id, work_km in ((1, 5.0), (2, 30.0), (4, 5.0), (6, 5.0)):
        rows.append(_trip(person_id, 0, "home", "work", work_km, 28800, 30000, False))
        rows.append(_trip(person_id, 1, "work", "home", work_km, 57600, 58800, True))
    rows.append(_trip(3, 0, "home", "shop", 2.0, 28800, 29400, False))
    rows.append(_trip(3, 1, "shop", "home", 2.0, 32400, 33000, True))
    rows.append(_trip(5, 0, "home", "escort", 1.0, 27000, 27600, False))
    rows.append(_trip(5, 1, "escort", "work", 5.0, 28800, 30000, False))
    rows.append(_trip(5, 2, "work", "home", 5.0, 57600, 58800, True))
    rows.append(_trip(7, 0, "home", "shop", 2.0, 36000, 36600, False))
    rows.append(_trip(7, 1, "shop", "home", 2.0, 39600, 40200, True))
    return pd.DataFrame(rows)


def _persons():
    """Enriched population: workers 1-6 plus non-worker 7, one household each, all car owners."""
    return pd.DataFrame({
        "person_id": [1, 2, 3, 4, 5, 6, 7],
        "household_id": [1, 2, 3, 4, 5, 6, 7],
        "sex": ["male", "female", "male", "female", "male", "female", "male"],
        "age": [40, 35, 45, 50, 30, 42, 38],
        "household_size": [2, 2, 2, 2, 2, 2, 2],
        "number_of_cars": [1, 1, 1, 1, 1, 1, 1],
        "employed": [True, True, True, True, True, True, False],
    })


def _home_locations(without_household=None):
    """Home points for every household; ``without_household`` omits one (a broken home join)."""
    household_ids = [h for h in (1, 2, 3, 4, 5, 6, 7) if h != without_household]
    return gpd.GeoDataFrame(
        {"household_id": household_ids},
        geometry=[Point(0.0, 0.0)] * len(household_ids), crs=CRS)


def _work_points():
    """Workplaces chosen so that every branch of the state draw is represented.

    Routed km = euclidean m / 1000 * ``cds_detour_factor`` (1.3):

    ==========  =============  ==============  =============  =====================================
    person      routed km      assigned class  donor class    branch
    ==========  =============  ==============  =============  =====================================
    1           60.0           50_100          lt10           eligible re-draw
    2           3.9            lt10            25_50          not eligible (assigned rank lower)
    3           19.5           10_25           None           no work trip -> donor_class_missing
    4           260.0          gt200           lt10           eligible, FAR, not escort
    5           260.0          gt200           lt10           eligible, FAR, escort-protected
    6           60.0           50_100          lt10           eligible re-draw
    ==========  =============  ==============  =============  =====================================
    """
    distances_m = {1: 46_153.85, 2: 3_000.0, 3: 15_000.0, 4: 200_000.0, 5: 200_000.0,
                   6: 46_153.85}
    return gpd.GeoDataFrame(
        {"person_id": list(distances_m), "location_id": [f"work_{p}" for p in distances_m]},
        geometry=[Point(x, 0.0) for x in distances_m.values()], crs=CRS)


def _donor_attributes(has_car=True, has_education_leg=False):
    """A donor pool that can serve every worker of :func:`_persons`.

    ``has_car=False`` breaks the ``has_car`` HARD criterion for the whole (car-owning) worker
    population, which is what the not-replaceable guard test needs. ``has_education_leg=True``
    does the same through the ruling-R7 criterion for every worker without an education location.
    ``n_trips`` matches :func:`_donor_trips` (two trips each) and ``is_immobile`` is False for
    every donor, so the plan replacement can tell an immobile donor from a join failure
    (ruling R9).
    """
    return pd.DataFrame({
        "donor_id": ["d1", "d2", "d3", "d4"],
        "sex": ["male", "female", "male", "female"],
        "age": [40, 35, 30, 50],
        "age_class": [3, 3, 2, 4],
        "household_size": [2, 2, 2, 2],
        "has_children_u14": [False, False, False, False],
        "has_active_escort": [False, False, True, False],
        "has_car": [has_car, has_car, has_car, has_car],
        "distance_class": ["lt10", "50_100", "gt200", "unknown"],
        "distance_source": ["trip_length"] * 4,
        "distance_km": [5.0, 60.0, 250.0, np.nan],
        "employed": [True, True, True, True],
        "n_trips": [2, 2, 2, 2],
        "is_immobile": [False, False, False, False],
        "has_education_leg": [has_education_leg] * 4,
        "has_work_leg": [False, False, False, False],
    })


def _donor_trips():
    """One two-trip home-office day per donor, in the donor-trips schema."""
    rows = []
    for donor_id in ("d1", "d2", "d3", "d4"):
        for trip_index, (preceding, following, departure, arrival) in enumerate(
                (("home", "shop", 36000, 36600), ("shop", "home", 39600, 40200))):
            rows.append({
                "donor_id": donor_id, "trip_index": trip_index,
                "departure_time": float(departure), "arrival_time": float(arrival),
                "preceding_purpose": preceding, "following_purpose": following,
                "is_first_trip": trip_index == 0, "is_last_trip": trip_index == 1,
                "trip_duration": float(arrival - departure), "activity_duration": 3600.0,
                "mode": "walk", "euclidean_distance": 1000.0,
                "trip_key": f"{donor_id}_{trip_index}",
            })
    return pd.DataFrame(rows)


def _education_points(person_ids=(3, 7)):
    """The EDUCATION half of ``synthesis.population.spatial.primary.locations``.

    Ruling R7: its person_ids are exactly the persons who can anchor an education activity. The
    default names the two NON-workers, so no worker of :func:`_work_points` has an education
    location and an education-leg donor is ineligible for all of them.
    """
    return gpd.GeoDataFrame(
        {"person_id": list(person_ids),
         "location_id": [f"education_{p}" for p in person_ids]},
        geometry=[Point(1000.0, 0.0)] * len(person_ids), crs=CRS)


def _state_stage_stages(donor_attributes=None, donor_trips=None, home_without_household=None,
                        education_person_ids=(3, 7)):
    return {
        "synthesis.population.trips": _trips(),
        "synthesis.population.enriched": _persons(),
        "synthesis.population.spatial.home.locations": _home_locations(home_without_household),
        "synthesis.population.spatial.primary.locations": (
            _work_points(), _education_points(education_person_ids)),
        DONOR_STAGE: (_donor_attributes() if donor_attributes is None else donor_attributes,
                      _donor_trips() if donor_trips is None else donor_trips,
                      {"enabled": True}),
    }


def _state_stage_config(enabled=True, max_not_replaceable_share=0.5):
    return {
        "random_seed": STATE_DRAW_SEED,
        STATE.KEY_ENABLED: enabled,
        STATE.KEY_FAR_THRESHOLD_KM: STATE.DEFAULT_FAR_THRESHOLD_KM,
        STATE.KEY_ABSENT_SHARE_FAR: STATE.DEFAULT_ABSENT_SHARE_FAR,
        STATE.KEY_MAX_NOT_REPLACEABLE_SHARE: max_not_replaceable_share,
        STATE.KEY_DETOUR: STATE.DEFAULT_DETOUR_FACTOR,
        "data_path": DATA_PATH,
    }


# --------------------------------------------------------------------------- raw MiD fixture

def _write_raw_mid(directory):
    """Write tiny raw-MiD-shaped CSVs (same layout as ``tests/test_commute_day_donor_pool.py``).

    5 persons in 2 households; donors are the weekday/worked/at-home persons ``1_1``, ``2_1``,
    ``2_2``, ``2_3`` (``2_3`` immobile). Returns the directory.
    """
    persons = pd.DataFrame({
        "H_ID": [1, 1, 2, 2, 2],
        "P_ID": [1, 2, 1, 2, 3],
        "HP_ID": ["1_1", "1_2", "2_1", "2_2", "2_3"],
        "HP_SEX": [1, 2, 2, 9, 1],
        "HP_ALTER": [40, 10, 35, 50, 30],
        "arbwo": [1, 2, 1, 1, 1],
        "P_STARB1": [1, 9, 1, 1, 1],
        "starb2": [1, 9, 1, 1, 1],
        "M_HOFF": [1, 0, 2, 1, 1],
        "P_ARB_ENTF": [15.0, np.nan, 200.0, 999.0, np.nan],
        "P_GEW": [1.0, 1.0, 2.0, 2.0, 2.0],
        # The three columns the issue-#374 donor filters read. Every fixture person has a
        # collected, non-holiday diary, so the default-ON filters drop nobody and the donor pool
        # assertions below stay the pre-#374 ones; "2_3" is genuinely immobile (0 trips, mobil 0),
        # which is NOT a no-diary code and must therefore keep it in the pool.
        "anzwege1": [2, 0, 2, 2, 0],
        "mobil": [1, 0, 1, 1, 0],
        "feiertag": [0, 0, 0, 0, 0],
    })
    wege = pd.DataFrame({
        "H_ID": [1, 1, 2, 2, 2, 2],
        "P_ID": [1, 1, 1, 1, 2, 2],
        "W_ID": [1, 2, 1, 2, 1, 2],
        "W_ZWECK": [6, 8, 1, 8, 1, 8],
        "hvm_imp": [4, 4, 4, 4, 4, 4],
        "W_SZS": [7, 7, 8, 17, 8, 17],
        "W_SZM": [0, 30, 0, 0, 0, 0],
        "W_AZS": [7, 7, 8, 17, 8, 17],
        "W_AZM": [15, 45, 30, 30, 40, 40],
        "wegkm": [3.0, 3.0, 100.0, 100.0, 200.0, 5.0],
        "wegkm_imp": [3.0, 3.0, 100.0, 100.0, 200.0, 5.0],
        "wegmin_imp1": [15.0, 15.0, 30.0, 30.0, 40.0, 40.0],
        # W_RBW / W_SO1 are part of braunschweig.popsim.mid.donor.MID_WEGE_REQUIRED_COLS
        # (the single committed definition this stage's WEGE_COLUMNS derives from), so the
        # fixture delivery must carry them like the real MiD 2023 B1 Wege file does.
        # All fixture legs are direct diary legs (W_RBW 0, i.e. no rbW summary record) and
        # every diary starts at home (W_SO1 1 on the first leg; 809 = not applicable on the
        # later ones, mirroring tests/test_completed_donor_stage.py).
        "W_RBW": [0, 0, 0, 0, 0, 0],
        "W_SO1": [1, 809, 1, 809, 1, 809],
        # The MiD Wege file repeats the household member's age on every leg; the stage loads it
        # (WEGE_COLUMNS) because the passive-escort pairing needs it to decide who counts as the
        # accompanying ADULT (issue #372). Matches the persons frame above leg by leg.
        "HP_ALTER": [40, 40, 35, 35, 50, 50],
    })
    households = pd.DataFrame({"H_ID": [1, 2], "H_GR": [2, 3], "H_ANZAUTO": [1, 0]})
    persons.to_csv(os.path.join(directory, DONORS.PERSONS_FILE), index=False)
    wege.to_csv(os.path.join(directory, DONORS.WEGE_FILE), index=False)
    households.to_csv(os.path.join(directory, DONORS.HOUSEHOLDS_FILE), index=False)
    return directory


def _donor_stage_config(mid_dir, enabled=True, **overrides):
    """The donor stage's config, at the defaults ``configure()`` itself declares.

    The plan-structure / donor-filter values are read from the recorder rather than re-typed, so
    this helper can never drift from the stage's own declared defaults (issue #374).
    """
    declared = _ConfigureRecorder()
    DONORS.configure(declared)
    config = {
        DONORS.KEY_MID_DIR: str(mid_dir),
        DONORS.KEY_ESCORT_PURPOSE: DONORS.DEFAULT_ESCORT_PURPOSE,
        DONORS.KEY_ESCORT_PASSIVE_EDUCATION: DONORS.DEFAULT_ESCORT_PASSIVE_EDUCATION,
        DONORS.KEY_EXPLICIT_ROUND_TRIP_PURPOSES: DONORS.DEFAULT_EXPLICIT_ROUND_TRIP_PURPOSES,
        DONORS.KEY_ENABLED: enabled,
        "random_seed": RANDOM_SEED,
    }
    for key, default in declared.config_keys.items():
        config.setdefault(key, default)
    config.update(overrides)
    return config


# --------------------------------------------------------------------------- configure contracts

def test_configure_declares_the_documented_stages_and_defaults():
    donors = _ConfigureRecorder()
    DONORS.configure(donors)
    assert donors.config_keys[DONORS.KEY_MID_DIR] is None
    assert donors.config_keys[DONORS.KEY_ENABLED] is DONORS.DEFAULT_ENABLED
    # The trip flags must carry the IDENTICAL defaults braunschweig.popsim.trips_stage declares,
    # or the donor's day would be built by different rules than the day it replaces.
    trips_stage_recorder = _ConfigureRecorder()
    from braunschweig.popsim import trips_stage
    trips_stage.configure(trips_stage_recorder)
    from braunschweig.popsim.stage.config_keys import (
        KEY_CLOSURE_DWELL_MIN_OBS, KEY_CLOSURE_DWELL_MODEL, KEY_DIARY_PLAN_MATCH,
        KEY_DROP_LEADING_ARRIVE_HOME_LEG, KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES, KEY_EXCLUDE_RBW_LEGS,
    )
    for key in (DONORS.KEY_ESCORT_PURPOSE, DONORS.KEY_ESCORT_PASSIVE_EDUCATION,
                DONORS.KEY_EXPLICIT_ROUND_TRIP_PURPOSES,
                # Issue #374: the plan-structure flags must be declared with trips_stage's
                # defaults too, or the donor day would be built by different rules again.
                KEY_EXCLUDE_RBW_LEGS, KEY_DROP_LEADING_ARRIVE_HOME_LEG,
                KEY_CLOSURE_DWELL_MODEL, KEY_CLOSURE_DWELL_MIN_OBS):
        assert donors.config_keys[key] == trips_stage_recorder.config_keys[key]

    # The two donor-filter keys are the plan-source realisability flags completed_donor declares;
    # the same value must steer both stages, so the declared defaults must agree there as well.
    completed_donor_recorder = _ConfigureRecorder()
    from braunschweig.popsim import completed_donor
    completed_donor.configure(completed_donor_recorder)
    for key in (KEY_DIARY_PLAN_MATCH, KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES, KEY_EXCLUDE_RBW_LEGS,
                KEY_DROP_LEADING_ARRIVE_HOME_LEG):
        assert donors.config_keys[key] == completed_donor_recorder.config_keys[key]

    state = _ConfigureRecorder()
    STATE.configure(state)
    assert set(state.stages) == {
        "synthesis.population.trips", "synthesis.population.enriched",
        "synthesis.population.spatial.home.locations",
        "synthesis.population.spatial.primary.locations", DONOR_STAGE}
    assert state.config_keys[STATE.KEY_FAR_THRESHOLD_KM] == 200.0
    assert state.config_keys[STATE.KEY_ABSENT_SHARE_FAR] == 1.0
    assert state.config_keys[STATE.KEY_MAX_NOT_REPLACEABLE_SHARE] == 0.5
    assert state.config_keys[STATE.KEY_DETOUR] == 1.3

    trips = _ConfigureRecorder()
    TRIPS.configure(trips)
    # Ruling A-R13 (issue #123 Task 4): the departure-time model reuses the ALREADY declared
    # synthesis.population.enriched persons -- this stage must NOT gain a
    # synthesis.population.sampled dependency for it, so the stage set stays exactly this one.
    assert set(trips.stages) == {"synthesis.population.trips", STATE_STAGE, DONOR_STAGE,
                                 TRIPS.ABSENCE_STAGE, "synthesis.population.enriched"}
    assert trips.config_keys[TRIPS.KEY_ENABLED] is TRIPS.DEFAULT_ENABLED
    assert trips.config_keys[TRIPS.KEY_DAY_ABSENCE_ENABLED] is TRIPS.DEFAULT_DAY_ABSENCE_ENABLED
    from braunschweig.popsim.stage.config_keys import (
        DEFAULT_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS, DEFAULT_DEPARTURE_TIME_MIN_MODEL_N,
        DEFAULT_DEPARTURE_TIME_MIN_REFERENCE_N, DEFAULT_DEPARTURE_TIME_MODEL,
        KEY_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS, KEY_DEPARTURE_TIME_MIN_MODEL_N,
        KEY_DEPARTURE_TIME_MIN_REFERENCE_N, KEY_DEPARTURE_TIME_MODEL,
    )
    assert trips.config_keys[KEY_DEPARTURE_TIME_MODEL] == DEFAULT_DEPARTURE_TIME_MODEL
    assert (trips.config_keys[KEY_DEPARTURE_TIME_MIN_REFERENCE_N]
            == DEFAULT_DEPARTURE_TIME_MIN_REFERENCE_N)
    assert trips.config_keys[KEY_DEPARTURE_TIME_MIN_MODEL_N] == DEFAULT_DEPARTURE_TIME_MIN_MODEL_N
    assert (trips.config_keys[KEY_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS]
            == DEFAULT_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS)
    # The committed SrV reference is read from data_path when the model is srv_mapped.
    assert "data_path" in trips.config_keys

    activities = _ConfigureRecorder()
    ACT.configure(activities)
    assert set(activities.stages) == {"synthesis.population.trips.final",
                                      "synthesis.population.enriched"}
    assert activities.config_keys == {}


# --------------------------------------------------------------------------- donor stage

def test_donor_stage_off_returns_empty_frames_with_the_on_path_columns(tmp_path):
    context = _context(DONORS, config=_donor_stage_config(tmp_path, enabled=False))
    attributes, trips, diagnostics = DONORS.execute(context)

    assert len(attributes) == 0 and len(trips) == 0
    assert tuple(attributes.columns) == DONORS.ATTRIBUTE_COLUMNS
    assert tuple(trips.columns) == DONORS.TRIP_COLUMNS
    assert diagnostics == {"enabled": False}
    # The OFF path must not touch the raw delivery at all (tmp_path holds no MiD files here).
    assert os.listdir(tmp_path) == []


def test_donor_stage_validate_returns_zero_when_disabled(tmp_path):
    context = _context(DONORS, config=_donor_stage_config(tmp_path, enabled=False))
    assert DONORS.validate(context) == 0


def test_donor_stage_validate_raises_naming_the_missing_raw_file(tmp_path):
    _write_raw_mid(str(tmp_path))
    os.remove(os.path.join(str(tmp_path), DONORS.WEGE_FILE))
    context = _context(DONORS, config=_donor_stage_config(tmp_path))

    with pytest.raises(RuntimeError) as error:
        DONORS.validate(context)
    assert DONORS.WEGE_FILE in str(error.value)


def test_donor_stage_validate_token_tracks_the_raw_input_size(tmp_path):
    _write_raw_mid(str(tmp_path))
    context = _context(DONORS, config=_donor_stage_config(tmp_path))
    total_size = sum(os.path.getsize(os.path.join(str(tmp_path), name))
                     for name in DONORS.RAW_FILES)

    token = DONORS.validate(context)
    assert token.startswith(f"{total_size}-")

    # A re-delivered (changed) MiD extract must change the token, or the stage would silently
    # reuse a donor pool built from the previous delivery.
    with open(os.path.join(str(tmp_path), DONORS.HOUSEHOLDS_FILE), "a", encoding="utf-8") as handle:
        handle.write("3,2,1\n")
    assert DONORS.validate(context) != token


def test_donor_stage_on_builds_the_pool_from_the_raw_delivery(tmp_path):
    _write_raw_mid(str(tmp_path))
    context = _context(DONORS, config=_donor_stage_config(tmp_path))

    attributes, trips, diagnostics = DONORS.execute(context)

    assert set(attributes["donor_id"]) == {"1_1", "2_1", "2_2", "2_3"}
    assert tuple(attributes.columns) == DONORS.ATTRIBUTE_COLUMNS
    assert set(trips.columns) == set(DONORS.TRIP_COLUMNS)
    assert set(trips["donor_id"].unique()) == {"1_1", "2_1", "2_2"}
    assert diagnostics["enabled"] is True
    assert diagnostics["n_donors"] == 4
    assert diagnostics["n_immobile"] == 1
    assert diagnostics["n_mid_persons"] == 5
    assert diagnostics["donor_share_unweighted"] == pytest.approx(4 / 5)
    # P_GEW-weighted: donors 1_1 (1.0) + 2_1/2_2/2_3 (2.0 each) of the 8.0 total person weight.
    assert diagnostics["donor_share_weighted"] == pytest.approx(7.0 / 8.0)


def test_donor_stage_on_raises_when_a_required_mid_column_is_absent(tmp_path):
    _write_raw_mid(str(tmp_path))
    path = os.path.join(str(tmp_path), DONORS.HOUSEHOLDS_FILE)
    pd.read_csv(path).drop(columns=["H_ANZAUTO"]).to_csv(path, index=False)
    context = _context(DONORS, config=_donor_stage_config(tmp_path))

    with pytest.raises(RuntimeError) as error:
        DONORS.execute(context)
    assert "H_ANZAUTO" in str(error.value)



def test_donor_stage_forwards_the_plan_structure_flags_and_the_donor_filters(tmp_path, monkeypatch):
    """Issue #374: every declared flag reaches the pure builder with the configured value.

    The pure builder's own defaults are the pre-#374 (OFF) ones, so a flag the stage forgets to
    forward silently reverts the donor day to the old rules -- exactly the defect this closes.
    """
    _write_raw_mid(str(tmp_path))
    captured = {}
    real_builder = DONORS.build_home_office_donor_pool

    def capturing_builder(persons, wege, households, **kwargs):
        captured.update(kwargs)
        return real_builder(persons, wege, households, **kwargs)

    monkeypatch.setattr(DONORS, "build_home_office_donor_pool", capturing_builder)
    context = _context(DONORS, config=_donor_stage_config(tmp_path))

    DONORS.execute(context)

    from braunschweig.popsim import trips_stage
    from braunschweig.popsim.stage.config_keys import (
        DEFAULT_CLOSURE_DWELL_MODEL, DEFAULT_DROP_LEADING_ARRIVE_HOME_LEG,
        DEFAULT_EXCLUDE_RBW_LEGS,
    )
    assert captured["exclude_rbw_legs"] is DEFAULT_EXCLUDE_RBW_LEGS
    assert captured["drop_leading_arrive_home_leg"] is DEFAULT_DROP_LEADING_ARRIVE_HOME_LEG
    assert captured["closure_dwell_model"] == DEFAULT_CLOSURE_DWELL_MODEL
    assert captured["closure_dwell_min_obs"] == trips_stage.DEFAULT_CLOSURE_DWELL_MIN_OBS
    # diary_plan_match is on by default and gates all three donor filters.
    assert captured["exclude_no_diary"] is True
    assert captured["exclude_holidays"] is True
    assert captured["exclude_only_rbw"] is True


def test_donor_stage_forwards_the_purpose_correctness_flags_to_the_builder(tmp_path, monkeypatch):
    """Issue #373 cleanup wave, item 3: w_zweck_10_as_leisure, escort_passive_from_adult and
    passive_pair_max_gap_minutes reach build_home_office_donor_pool, but no test asserted the
    VALUE arrives (the sibling test above pins the plan-structure flags and donor filters, not
    these three). Non-default values are used (the pure builder's own keyword defaults are
    False / False / DEFAULT_PASSIVE_PAIR_MAX_GAP_MINUTES) so a forwarding regression that
    silently falls back to the callee's own default would be caught here."""
    from braunschweig.popsim.stage.config_keys import (
        KEY_ESCORT_PASSIVE_FROM_ADULT, KEY_PASSIVE_PAIR_MAX_GAP_MINUTES,
        KEY_W_ZWECK_10_AS_LEISURE,
    )

    _write_raw_mid(str(tmp_path))
    captured = {}
    real_builder = DONORS.build_home_office_donor_pool

    def capturing_builder(persons, wege, households, **kwargs):
        captured["w_zweck_10_as_leisure"] = kwargs.get("w_zweck_10_as_leisure")
        captured["escort_passive_from_adult"] = kwargs.get("escort_passive_from_adult")
        captured["passive_pair_max_gap_minutes"] = kwargs.get("passive_pair_max_gap_minutes")
        return real_builder(persons, wege, households, **kwargs)

    monkeypatch.setattr(DONORS, "build_home_office_donor_pool", capturing_builder)
    # escort_passive_from_adult=True requires escort_purpose=True (map_purpose's own guard).
    context = _context(DONORS, config=_donor_stage_config(
        tmp_path,
        **{
            DONORS.KEY_ESCORT_PURPOSE: True,
            KEY_W_ZWECK_10_AS_LEISURE: True,
            KEY_ESCORT_PASSIVE_FROM_ADULT: True,
            KEY_PASSIVE_PAIR_MAX_GAP_MINUTES: 22.0,
        }))

    DONORS.execute(context)

    assert captured["w_zweck_10_as_leisure"] is True
    assert captured["escort_passive_from_adult"] is True
    assert captured["passive_pair_max_gap_minutes"] == 22.0


def test_donor_stage_donor_filters_follow_the_plan_source_flags(tmp_path, monkeypatch):
    """The master switch turns every donor filter off; the two sub-keys gate one filter each."""
    from braunschweig.popsim.stage.config_keys import (
        KEY_DIARY_PLAN_MATCH, KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES, KEY_EXCLUDE_RBW_LEGS,
    )
    _write_raw_mid(str(tmp_path))
    captured = {}
    real_builder = DONORS.build_home_office_donor_pool

    def capturing_builder(persons, wege, households, **kwargs):
        captured.clear()
        captured.update(kwargs)
        return real_builder(persons, wege, households, **kwargs)

    monkeypatch.setattr(DONORS, "build_home_office_donor_pool", capturing_builder)

    DONORS.execute(_context(DONORS, config=_donor_stage_config(
        tmp_path, **{KEY_DIARY_PLAN_MATCH: False})))
    assert (captured["exclude_no_diary"], captured["exclude_holidays"],
            captured["exclude_only_rbw"]) == (False, False, False)

    DONORS.execute(_context(DONORS, config=_donor_stage_config(
        tmp_path, **{KEY_EXCLUDE_HOLIDAY_PLAN_SOURCES: False})))
    assert (captured["exclude_no_diary"], captured["exclude_holidays"],
            captured["exclude_only_rbw"]) == (True, False, True)

    DONORS.execute(_context(DONORS, config=_donor_stage_config(
        tmp_path, **{KEY_EXCLUDE_RBW_LEGS: False})))
    assert (captured["exclude_no_diary"], captured["exclude_holidays"],
            captured["exclude_only_rbw"]) == (True, True, False)


@pytest.mark.parametrize("missing_column", ["anzwege1", "mobil", "feiertag"])
def test_donor_stage_raises_when_a_live_filter_column_is_absent(tmp_path, missing_column):
    """A filter is never silently skipped because the delivery lacks its column."""
    _write_raw_mid(str(tmp_path))
    path = os.path.join(str(tmp_path), DONORS.PERSONS_FILE)
    pd.read_csv(path).drop(columns=[missing_column]).to_csv(path, index=False)
    context = _context(DONORS, config=_donor_stage_config(tmp_path))

    with pytest.raises(RuntimeError) as error:
        DONORS.execute(context)
    assert missing_column in str(error.value)


def test_donor_stage_off_filters_load_without_the_filter_columns(tmp_path):
    """With the filters off the same delivery loads fine -- the columns are optional to LOAD."""
    from braunschweig.popsim.stage.config_keys import KEY_DIARY_PLAN_MATCH
    _write_raw_mid(str(tmp_path))
    path = os.path.join(str(tmp_path), DONORS.PERSONS_FILE)
    pd.read_csv(path).drop(columns=["anzwege1", "mobil", "feiertag"]).to_csv(path, index=False)
    context = _context(DONORS, config=_donor_stage_config(
        tmp_path, **{KEY_DIARY_PLAN_MATCH: False}))

    attributes, _trips, diagnostics = DONORS.execute(context)
    assert set(attributes["donor_id"]) == {"1_1", "2_1", "2_2", "2_3"}
    assert diagnostics["n_dropped_no_diary"] == 0


def test_donor_stage_drops_a_no_diary_and_a_holiday_donor_from_the_delivery(tmp_path):
    """End to end through the real CSV path: the counts reach the stage diagnostics."""
    _write_raw_mid(str(tmp_path))
    path = os.path.join(str(tmp_path), DONORS.PERSONS_FILE)
    persons = pd.read_csv(path)
    persons.loc[persons["HP_ID"] == "2_1", "anzwege1"] = 804    # diary not collected
    persons.loc[persons["HP_ID"] == "2_2", "feiertag"] = 1      # public-holiday diary
    persons.to_csv(path, index=False)
    context = _context(DONORS, config=_donor_stage_config(tmp_path))

    attributes, trips, diagnostics = DONORS.execute(context)

    assert set(attributes["donor_id"]) == {"1_1", "2_3"}
    assert set(trips["donor_id"].unique()) == {"1_1"}
    assert diagnostics["n_donors"] == 2
    assert diagnostics["n_donors_before_diary_filters"] == 4
    assert diagnostics["n_dropped_no_diary"] == 1
    assert diagnostics["n_dropped_holiday"] == 1
    assert diagnostics["n_dropped_only_rbw"] == 0


def test_donor_stage_validate_token_tracks_the_declared_option_surface(tmp_path, monkeypatch):
    """The deferred config-key module is hashed: a changed declared default must invalidate.

    Without it, flipping a plan-structure default would serve a donor pool built under the old
    option surface -- the same stale-cache hazard the helper-module hash exists to close.
    """
    _write_raw_mid(str(tmp_path))
    context = _context(DONORS, config=_donor_stage_config(tmp_path))
    token = DONORS.validate(context)

    from braunschweig.popsim.stage import config_keys
    real_getsource = DONORS.inspect.getsource

    def patched_getsource(module):
        if module is config_keys:
            return real_getsource(module) + "\n# a changed declared default\n"
        return real_getsource(module)

    monkeypatch.setattr(DONORS.inspect, "getsource", patched_getsource)
    assert DONORS.validate(context) != token

# --------------------------------------------------------------------------- state stage

def test_state_stage_off_marks_every_worker_at_workplace():
    context = _context(STATE, stages=_state_stage_stages(),
                       config=_state_stage_config(enabled=False))
    result = STATE.execute(context)

    states = result["states"]
    assert tuple(states.columns) == STATE.STATE_COLUMNS
    assert list(states["person_id"]) == [1, 2, 3, 4, 5, 6]
    assert set(states["commute_day_state"]) == {"at_workplace"}
    assert set(states["reason"]) == {STATE.REASON_DISABLED}
    assert states["donor_id"].isna().all()
    assert result["diagnostics"] == {"enabled": False}


def test_state_stage_on_draws_every_branch_of_the_model():
    context = _context(STATE, stages=_state_stage_stages(), config=_state_stage_config())
    result = STATE.execute(context)

    states = result["states"].set_index("person_id")
    diagnostics = result["diagnostics"]
    table = load_workday_location_table(MID_REFERENCE_DIR).set_index("distance_class")

    assert tuple(result["states"].columns) == STATE.STATE_COLUMNS
    assert sorted(states.index) == [1, 2, 3, 4, 5, 6]

    # Assigned/donor classes come out as the fixture table documents.
    assert states.loc[1, "assigned_distance_class"] == "50_100"
    assert states.loc[1, "donor_distance_class"] == "lt10"
    assert states.loc[2, "assigned_distance_class"] == "lt10"
    assert states.loc[4, "assigned_distance_class"] == "gt200"
    assert states.loc[1, "distance_km"] == pytest.approx(60.0, abs=1e-3)

    # Person 1 IS eligible (assigned rank > donor rank): p_keep is the committed table's ratio.
    expected_keep = (table.loc["50_100", "share_at_workplace"]
                     / table.loc["lt10", "share_at_workplace"])
    assert bool(states.loc[1, "redraw_eligible"]) is True
    assert states.loc[1, "p_keep"] == pytest.approx(expected_keep)

    # Person 2 is NOT eligible (a shorter assigned than donor distance) and person 3 has no
    # donor distance at all: both keep the donor's own day, p_keep 1.0, at_workplace.
    assert bool(states.loc[2, "redraw_eligible"]) is False
    assert states.loc[2, "commute_day_state"] == "at_workplace"
    assert states.loc[2, "p_keep"] == 1.0
    assert states.loc[3, "reason"] == "donor_class_missing"
    assert states.loc[3, "commute_day_state"] == "at_workplace"
    assert pd.isna(states.loc[3, "donor_distance_class"])

    # absent_share_far = 1.0: a FAR, not-escort worker that is not kept is ALWAYS absent, never
    # home; the escort-protected far worker (person 5) is never absent (ADR-0104 Assumption 4).
    assert states.loc[4, "commute_day_state"] in {"at_workplace", "absent"}
    assert states.loc[5, "commute_day_state"] in {"at_workplace", "home"}
    # A near worker can never be absent.
    assert states.loc[6, "commute_day_state"] in {"at_workplace", "home"}

    # Under the pinned STATE_DRAW_SEED the draw actually reaches those branches (not merely
    # "could"): person 4 is absent, and person 5 -- far and not kept, but escorting -- is home
    # BECAUSE of the escort protection, which is the branch that would otherwise go untested.
    assert states.loc[4, "commute_day_state"] == "absent"
    assert states.loc[5, "reason"] == "home_escort_protected"
    assert diagnostics["n_escort_protected"] == 1

    # Every person actually left in the 'home' state has a donor and a coarsening level.
    home = result["states"][result["states"]["commute_day_state"] == "home"]
    assert len(home) > 0, "the seeded draw must put at least one worker home for this fixture"
    assert home["donor_id"].notna().all()
    assert home["coarsening_level"].notna().all()
    # The escort HARD criterion is never coarsened: the escorting person 5 can only be matched
    # to the one donor that also escorts (d3).
    assert states.loc[5, "donor_id"] == "d3"

    # ADR-0104 Amendment 1: the per-class primary-donor-source rate is reported.
    source = diagnostics["donor_source_by_assigned_class"]
    assert source["10_25"]["n_donor_missing"] == 1  # person 3 has no work trip
    assert source["10_25"]["share_primary"] == 0.0
    assert source["50_100"]["share_primary"] == 1.0
    assert diagnostics["share_donor_source_primary"] == pytest.approx(5 / 6)

    for key in ("enabled", "matching", "n_workers", "n_redraw_eligible", "n_donor_class_missing",
                "by_assigned_class", "n_home_drawn", "n_home_matched", "n_home_not_replaceable",
                "share_home_not_replaceable", "final_state_counts"):
        assert key in diagnostics
    assert diagnostics["n_workers"] == 6
    assert sum(diagnostics["final_state_counts"].values()) == 6


def test_state_stage_is_reproducible_under_the_same_seed():
    first = STATE.execute(_context(STATE, stages=_state_stage_stages(),
                                   config=_state_stage_config()))["states"]
    second = STATE.execute(_context(STATE, stages=_state_stage_stages(),
                                    config=_state_stage_config()))["states"]
    pd.testing.assert_frame_equal(first, second)


def test_escort_person_ids_warns_when_nobody_escorts_but_donors_do(caplog):
    trips = _trips()
    without_escort = trips[~((trips["following_purpose"] == STATE.ESCORT_PURPOSE)
                             | (trips["preceding_purpose"] == STATE.ESCORT_PURPOSE))]

    with caplog.at_level(logging.WARNING, logger=STATE.logger.name):
        assert STATE._escort_person_ids(without_escort, _donor_attributes()) == set()
    # has_active_escort is a HARD criterion, so escorting donors would be unusable for everyone;
    # that is an escort_purpose: false run, not a population that escorts nobody.
    assert any("escort_purpose" in record.getMessage() for record in caplog.records)

    caplog.clear()
    with caplog.at_level(logging.WARNING, logger=STATE.logger.name):
        assert STATE._escort_person_ids(trips, _donor_attributes()) == {5}
    assert caplog.records == []


def test_state_stage_keeps_one_row_per_worker_without_a_home_geometry():
    # Household 6 has no home point, so state.assigned_distance_class cannot measure person 6's
    # commute and drops them from the draw -- they must still appear in the states frame, or a
    # consumer joining on person_id would find no commute_day_state for them at all.
    context = _context(STATE, stages=_state_stage_stages(home_without_household=6),
                       config=_state_stage_config())
    result = STATE.execute(context)

    states = result["states"]
    assert len(states) == 6 and not states["person_id"].duplicated().any()
    row = states.set_index("person_id").loc[6]
    assert row["commute_day_state"] == "at_workplace"
    assert row["reason"] == STATE.REASON_NO_HOME_GEOMETRY
    assert pd.isna(row["distance_km"]) and pd.isna(row["assigned_distance_class"])
    assert result["diagnostics"]["n_workers_without_home_geometry"] == 1
    # The draw's own n_workers counts only the workers it actually saw.
    assert result["diagnostics"]["n_workers"] == 5
    assert sum(result["diagnostics"]["final_state_counts"].values()) == 6


def test_persons_home_frame_requires_exactly_one_enriched_row_per_worker():
    """The matching input must be 1:1 with the ``home`` cohort.

    Driven directly rather than through ``execute``: a person absent from the enriched frame is
    already dropped upstream (no household_id -> no home geometry -> not a worker), so this
    defensive guard protects against a DUPLICATED or otherwise broken enriched frame reaching
    the matching, where a duplicate would silently match one person twice.
    """
    persons = _persons()
    workers = pd.DataFrame({"person_id": [1, 99], "assigned_distance_class": ["lt10", "lt10"]})

    with pytest.raises(RuntimeError) as error:
        STATE._persons_home_frame(pd.Series([1, 99]), workers, persons, set(), set())
    assert "99" in str(error.value)

    duplicated = pd.concat([persons, persons.query("person_id == 1")], ignore_index=True)
    with pytest.raises(RuntimeError) as error:
        STATE._persons_home_frame(pd.Series([1]), workers, duplicated, set(), set())
    assert "duplicated [1]" in str(error.value)


def test_persons_home_frame_nans_has_car_when_number_of_cars_is_nan(caplog):
    """A NaN ``number_of_cars`` must resolve to has_car = NaN ("unresolved"), never to False via
    a bare ``> 0`` (fix round 2 item M1) -- mirrors donor_pool.donor_attributes' has_car / MiD
    H_ANZAUTO treatment on the donor side."""
    persons = _persons()
    persons.loc[persons["person_id"] == 1, "number_of_cars"] = np.nan
    workers = pd.DataFrame({"person_id": [1, 2], "assigned_distance_class": ["lt10", "lt10"]})

    with caplog.at_level(logging.WARNING, logger=STATE.logger.name):
        frame = STATE._persons_home_frame(pd.Series([1, 2]), workers, persons, set(), set())

    by_id = frame.set_index("person_id")
    assert pd.isna(by_id.loc[1, "has_car"])
    assert bool(by_id.loc[2, "has_car"]) is True
    assert any("number_of_cars" in record.getMessage() for record in caplog.records)


def test_state_stage_downgrades_unreplaceable_home_persons_below_the_threshold():
    # No donor shares the workers' car ownership -> the has_car HARD criterion can never be
    # satisfied, so every 'home' person is downgraded to at_workplace (threshold 1.0 = never raise).
    context = _context(STATE, stages=_state_stage_stages(donor_attributes=_donor_attributes(has_car=False)),
                       config=_state_stage_config(max_not_replaceable_share=1.0))
    result = STATE.execute(context)

    states = result["states"]
    diagnostics = result["diagnostics"]
    assert diagnostics["n_home_drawn"] > 0, "the fixture must draw at least one home person"
    assert diagnostics["n_home_not_replaceable"] == diagnostics["n_home_drawn"]
    assert set(states["commute_day_state"]) <= {"at_workplace", "absent"}
    downgraded = states[states["reason"] == STATE.REASON_NOT_REPLACEABLE]
    assert len(downgraded) == diagnostics["n_home_drawn"]
    assert set(downgraded["commute_day_state"]) == {"at_workplace"}


def test_state_stage_raises_above_the_not_replaceable_threshold():
    context = _context(STATE, stages=_state_stage_stages(donor_attributes=_donor_attributes(has_car=False)),
                       config=_state_stage_config(max_not_replaceable_share=0.1))
    with pytest.raises(RuntimeError) as error:
        STATE.execute(context)
    assert STATE.KEY_MAX_NOT_REPLACEABLE_SHARE in str(error.value)


def test_state_stage_downgrades_home_persons_when_every_donor_carries_an_education_leg():
    """Ruling R7, end to end: the blocker of the 2026-09-05 proof run cannot recur.

    No worker of the fixture has an education location (:func:`_education_points` names the two
    non-workers), so a pool in which every donor carries an education activity can serve none of
    them: the ``home`` persons are downgraded to ``at_workplace`` instead of receiving a chain
    with an activity they cannot anchor.
    """
    stages = _state_stage_stages(donor_attributes=_donor_attributes(has_education_leg=True))
    context = _context(STATE, stages=stages,
                       config=_state_stage_config(max_not_replaceable_share=1.0))
    result = STATE.execute(context)

    diagnostics = result["diagnostics"]
    assert diagnostics["n_home_drawn"] > 0, "the fixture must draw at least one home person"
    assert diagnostics["n_home_not_replaceable"] == diagnostics["n_home_drawn"]
    assert diagnostics["matching"]["n_donors_with_education_leg"] == 4
    assert (diagnostics["matching"]["n_persons_without_education_location"]
            == diagnostics["n_home_drawn"])
    assert result["states"]["donor_id"].isna().all()


def test_state_stage_lets_a_person_with_an_education_location_keep_such_a_donor():
    """The same pool, but the drawn home persons DO have an education location."""
    baseline = STATE.execute(_context(
        STATE, stages=_state_stage_stages(), config=_state_stage_config()))
    home_ids = baseline["states"].loc[
        baseline["states"]["commute_day_state"] == "home", "person_id"].tolist()
    assert home_ids, "the fixture must draw at least one home person"

    stages = _state_stage_stages(donor_attributes=_donor_attributes(has_education_leg=True),
                                 education_person_ids=tuple(home_ids))
    result = STATE.execute(_context(STATE, stages=stages, config=_state_stage_config()))
    assert result["diagnostics"]["n_home_not_replaceable"] == 0
    assert result["diagnostics"]["matching"]["n_persons_without_education_location"] == 0


# --------------------------------------------------------------------------- trips day stage

def _trips_day_stages(states, trips=None):
    return {
        "synthesis.population.trips": _trips() if trips is None else trips,
        "synthesis.population.enriched": _persons(),
        STATE_STAGE: {"states": states, "diagnostics": {"enabled": True}},
        DONOR_STAGE: (_donor_attributes(), _donor_trips(), {"enabled": True}),
    }


def _trips_day_config(**overrides):
    """The trips day stage's config at its own declared defaults, with overrides.

    Written as a helper (rather than a literal dict per test) since issue #123 Task 4 added the
    four departure-time keys plus ``data_path``: the stub context refuses any key ``configure``
    did not declare AND any key the caller did not supply, so every execute-path test must carry
    the full surface.
    """
    from braunschweig.popsim.stage.config_keys import (
        DEFAULT_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS, DEFAULT_DEPARTURE_TIME_MIN_MODEL_N,
        DEFAULT_DEPARTURE_TIME_MIN_REFERENCE_N, DEFAULT_DEPARTURE_TIME_MODEL,
        KEY_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS, KEY_DEPARTURE_TIME_MIN_MODEL_N,
        KEY_DEPARTURE_TIME_MIN_REFERENCE_N, KEY_DEPARTURE_TIME_MODEL,
    )
    config = {
        "random_seed": RANDOM_SEED,
        "data_path": DATA_PATH,
        TRIPS.KEY_ENABLED: True,
        TRIPS.KEY_DAY_ABSENCE_ENABLED: False,
        KEY_DEPARTURE_TIME_MODEL: DEFAULT_DEPARTURE_TIME_MODEL,
        KEY_DEPARTURE_TIME_MIN_REFERENCE_N: DEFAULT_DEPARTURE_TIME_MIN_REFERENCE_N,
        KEY_DEPARTURE_TIME_MIN_MODEL_N: DEFAULT_DEPARTURE_TIME_MIN_MODEL_N,
        KEY_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS: DEFAULT_DEPARTURE_TIME_MAX_MEDIAN_SHIFT_HOURS,
    }
    config.update(overrides)
    return config


def _states_frame(rows):
    """A states frame in the state stage's own schema, for driving the trips day stage."""
    frame = pd.DataFrame(rows)
    for column in STATE.STATE_COLUMNS:
        if column not in frame.columns:
            frame[column] = np.nan
    return frame[list(STATE.STATE_COLUMNS)]


def test_trips_day_stage_off_returns_the_identical_object():
    trips = _trips()
    context = _context(TRIPS, stages=_trips_day_stages(_states_frame([]), trips=trips),
                       config=_trips_day_config(**{TRIPS.KEY_ENABLED: False,
                                                   TRIPS.KEY_DAY_ABSENCE_ENABLED: False}))
    assert TRIPS.execute(context) is trips


def test_trips_day_stage_off_off_returns_the_identical_object():
    """Both flags false, driven the way the SDD brief spells the case out (issue #370, Task 4)."""
    trips = _trips()
    context = _context(TRIPS, stages={"synthesis.population.trips": trips},
                       config=_trips_day_config(**{TRIPS.KEY_ENABLED: False,
                                                   TRIPS.KEY_DAY_ABSENCE_ENABLED: False}))
    assert TRIPS.execute(context) is trips


def test_trips_day_stage_absence_only_removes_absent_persons():
    """commute_day_state_enabled OFF, day_absence_enabled ON: only the general-absence removal
    applies, through EMPTY commute-day placeholders (no state/donor stage output is touched)."""
    trips = _trips()
    person_ids = sorted(trips["person_id"].unique())
    absence = pd.DataFrame({
        "person_id": person_ids,
        "day_absence_state": ["absent_individual"] + ["present"] * (len(person_ids) - 1),
    })
    context = _context(TRIPS, stages={"synthesis.population.trips": trips,
                                      "synthesis.population.enriched": _persons(),
                                      TRIPS.ABSENCE_STAGE: {"absence": absence,
                                                            "diagnostics": {"enabled": True}}},
                       config=_trips_day_config(**{TRIPS.KEY_ENABLED: False,
                                                   TRIPS.KEY_DAY_ABSENCE_ENABLED: True}))

    out = TRIPS.execute(context)

    removed_person = absence["person_id"].iloc[0]
    assert removed_person not in set(out["person_id"])
    assert len(out) == len(trips) - int((trips["person_id"] == removed_person).sum())


def test_trips_day_stage_on_replaces_only_the_home_persons():
    trips = _trips()
    states = _states_frame([
        {"person_id": 1, "commute_day_state": "home", "donor_id": "d1", "coarsening_level": 0},
        {"person_id": 2, "commute_day_state": "at_workplace"},
        {"person_id": 4, "commute_day_state": "absent"},
        # A home person WITHOUT a donor must never be handed to the replacement (the state stage
        # downgrades such persons; this row guards the matches_from_states filter).
        {"person_id": 6, "commute_day_state": "home", "donor_id": None},
    ])
    context = _context(TRIPS, stages=_trips_day_stages(states, trips=trips),
                       config=_trips_day_config())

    day_trips = TRIPS.execute(context)

    # Person 1's day is the donor's own two-trip chain, renumbered onto the person.
    person_1 = day_trips[day_trips["person_id"] == 1]
    assert list(person_1["following_purpose"]) == ["shop", "home"]
    assert list(person_1["trip_index"]) == [0, 1]
    # Person 4 is absent: no rows at all. Persons 2, 3, 5, 6, 7 keep their original day.
    assert len(day_trips[day_trips["person_id"] == 4]) == 0
    for person_id in (2, 3, 5, 6, 7):
        pd.testing.assert_frame_equal(
            day_trips[day_trips["person_id"] == person_id].reset_index(drop=True),
            trips[trips["person_id"] == person_id][list(day_trips.columns)].reset_index(drop=True))
    # The CONTRACT columns stay first, as the pre-assignment view has them.
    assert list(day_trips.columns)[:len(CONTRACT)] == CONTRACT


def test_trips_day_stage_both_on_removes_commute_and_general_absent_persons():
    """Both flags True (issue #370): the commute-absent and generally-absent removals compose,
    each removing its own person while leaving everyone else's row count unchanged."""
    trips = _trips()
    states = _states_frame([{"person_id": 4, "commute_day_state": "absent"}])
    absence = pd.DataFrame({"person_id": [7], "day_absence_state": ["absent_household"]})
    stages = dict(_trips_day_stages(states, trips=trips))
    stages[TRIPS.ABSENCE_STAGE] = {"absence": absence, "diagnostics": {"enabled": True}}
    context = _context(TRIPS, stages=stages,
                       config=_trips_day_config(**{TRIPS.KEY_DAY_ABSENCE_ENABLED: True}))

    out = TRIPS.execute(context)

    assert 4 not in set(out["person_id"])   # commute-absent
    assert 7 not in set(out["person_id"])   # generally absent
    for person_id in (1, 2, 3, 5, 6):
        assert (len(out[out["person_id"] == person_id])
                == len(trips[trips["person_id"] == person_id]))


def test_trips_day_stage_reports_an_immobile_donor_rather_than_a_join_failure(caplog):
    """Ruling R9 wiring: the stage must hand the donor ATTRIBUTES to the replacement.

    Donor "d4" is matched but has no rows in ``donor_trips`` because its own day is immobile
    (``is_immobile``). Without the attributes the replacement cannot know that and warns about a
    donor_id key/dtype mismatch for 100 % of the replaced persons -- which is exactly the
    unreadable 27.3 % signal of the 2026-09-05 proof run.
    """
    attributes = _donor_attributes()
    attributes.loc[attributes["donor_id"] == "d4", ["n_trips", "is_immobile"]] = [0, True]
    donor_trips = _donor_trips()
    donor_trips = donor_trips[donor_trips["donor_id"] != "d4"].reset_index(drop=True)
    states = _states_frame([
        {"person_id": 1, "commute_day_state": "home", "donor_id": "d4", "coarsening_level": 0},
    ])
    stages = {"synthesis.population.trips": _trips(),
              "synthesis.population.enriched": _persons(),
              STATE_STAGE: {"states": states, "diagnostics": {"enabled": True}},
              DONOR_STAGE: (attributes, donor_trips, {"enabled": True})}
    context = _context(TRIPS, stages=stages,
                       config=_trips_day_config())

    with caplog.at_level("WARNING",
                         logger="braunschweig.synthesis.commute_day.plan_replacement"):
        day_trips = TRIPS.execute(context)

    assert len(day_trips[day_trips["person_id"] == 1]) == 0   # a valid trip-less home day
    assert not any("donor_id key/dtype mismatch" in message for message in caplog.messages)


def _home_person_states():
    """Person 1 replaced by donor d1's own (home-office) day; nobody else has a state."""
    return _states_frame([
        {"person_id": 1, "commute_day_state": "home", "donor_id": "d1", "coarsening_level": 0},
    ])


def test_trips_day_stage_srv_mapped_maps_the_spliced_chain_onto_the_srv_reference():
    """Issue #123 Task 4: with ``departure_time_model=srv_mapped`` the stage loads the COMMITTED
    SrV reference from ``data_path`` and hands the plan replacement the RECEIVING persons
    (ruling A-R7), so the spliced donor day starts where the SrV distribution of the receiving
    person's own (purpose, group) cell says -- not where the donor's diary happened to start.

    ``min_model_n`` is lowered to 1 because this fixture has a single replaced person; the
    reference threshold keeps its production default (the ``(employed, shop)`` cell carries 538
    unweighted observations).
    """
    from braunschweig.popsim.departure_time_model import BIN_MINUTES, OFFSET_COLUMN
    from braunschweig.popsim.stage.config_keys import (KEY_DEPARTURE_TIME_MIN_MODEL_N,
                                                       KEY_DEPARTURE_TIME_MODEL)

    # The real pre-assignment table always carries the recorded offset (trips_stage.run writes
    # it); build_day_trips only keeps output columns the INPUT frame has, so a fixture without
    # it would silently drop the very column this test reads.
    trips = _trips().assign(**{OFFSET_COLUMN: 0.0})
    stages = _trips_day_stages(_home_person_states(), trips=trips)
    uniform = TRIPS.execute(_context(TRIPS, stages=stages, config=_trips_day_config()))
    mapped = TRIPS.execute(_context(TRIPS, stages=stages, config=_trips_day_config(
        **{KEY_DEPARTURE_TIME_MODEL: "srv_mapped", KEY_DEPARTURE_TIME_MIN_MODEL_N: 1})))

    person_1 = mapped[mapped["person_id"] == 1].sort_values("trip_index").reset_index(drop=True)
    assert list(person_1["following_purpose"]) == ["shop", "home"]
    assert person_1[OFFSET_COLUMN].nunique() == 1          # the whole chain moves by one offset
    donor = _donor_trips()
    donor = donor[donor["donor_id"] == "d1"].sort_values("trip_index").reset_index(drop=True)
    offset = float(person_1[OFFSET_COLUMN].iloc[0])
    assert np.allclose(person_1["departure_time"], np.round(donor["departure_time"] + offset))
    # The model actually ran: the mapped day starts in a different quarter hour than the eqasim
    # jitter's (which can only move the donor's own 10:00 start by at most 30 minutes).
    uniform_1 = uniform[uniform["person_id"] == 1].sort_values("trip_index").reset_index(drop=True)
    assert (int(person_1["departure_time"].iloc[0] // (BIN_MINUTES * 60))
            != int(uniform_1["departure_time"].iloc[0] // (BIN_MINUTES * 60)))
    # Everyone else keeps their pre-assignment day under BOTH models.
    for person_id in (2, 3, 5, 6, 7):
        pd.testing.assert_frame_equal(
            mapped[mapped["person_id"] == person_id].reset_index(drop=True),
            trips[trips["person_id"] == person_id][list(mapped.columns)].reset_index(drop=True))


def test_trips_day_stage_srv_mapped_raises_naming_the_key_when_the_reference_is_missing(tmp_path):
    """No silent fallback to the eqasim jitter: a configured ``srv_mapped`` reporting day whose
    reference is not under ``data_path`` must abort naming the path and the config key."""
    from braunschweig.popsim.stage.config_keys import KEY_DEPARTURE_TIME_MODEL

    context = _context(TRIPS, stages=_trips_day_stages(_home_person_states()),
                       config=_trips_day_config(**{KEY_DEPARTURE_TIME_MODEL: "srv_mapped",
                                                   "data_path": str(tmp_path)}))
    with pytest.raises(FileNotFoundError) as excinfo:
        TRIPS.execute(context)
    assert KEY_DEPARTURE_TIME_MODEL in str(excinfo.value)
    assert str(tmp_path) in str(excinfo.value)


def test_matches_from_states_keeps_only_home_persons_with_a_donor():
    states = _states_frame([
        {"person_id": 1, "commute_day_state": "home", "donor_id": "d1", "coarsening_level": 2},
        {"person_id": 2, "commute_day_state": "at_workplace", "donor_id": "d2"},
        {"person_id": 3, "commute_day_state": "home", "donor_id": None},
    ])
    matches = TRIPS.matches_from_states(states)
    assert list(matches["person_id"]) == [1]
    assert list(matches.columns) == list(TRIPS.MATCH_COLUMNS)


# --------------------------------------------------------------------------- activities day stage

class _BaseActivitiesContext:
    """Context for the vendored ``synthesis.population.activities`` reference run."""

    def __init__(self, trips, persons):
        self._stages = {"synthesis.population.trips": trips,
                        "synthesis.population.enriched": persons}

    def stage(self, name):
        return self._stages[name]


def test_activities_day_stage_matches_the_vendored_activities_stage():
    trips, persons = _trips(), _persons()
    expected = base_activities.execute(_BaseActivitiesContext(trips.copy(), persons))

    context = _context(ACT, stages={"synthesis.population.trips.final": trips,
                                    "synthesis.population.enriched": persons})
    actual = ACT.execute(context)

    pd.testing.assert_frame_equal(actual.reset_index(drop=True), expected.reset_index(drop=True))


def test_activities_day_stage_does_not_mutate_the_trips_stage_output():
    trips = _trips()
    columns_before = list(trips.columns)
    context = _context(ACT, stages={"synthesis.population.trips.final": trips,
                                    "synthesis.population.enriched": _persons()})
    ACT.execute(context)
    # synthesis.population.activities.execute adds columns to the frame it receives; the shim
    # must hand it a COPY, or the cached reporting-day trips would be corrupted for every other
    # consumer of that stage.
    assert list(trips.columns) == columns_before


def test_activities_day_shim_rejects_an_undeclared_stage():
    shim = ACT._ActivitiesShimContext(_trips(), _persons())
    with pytest.raises(KeyError):
        shim.stage("synthesis.population.spatial.home.locations")
