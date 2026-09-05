"""Tests for the reporting-day CONSUMERS of the commute-day-state model (Phase B Task 5, #244).

Task 4 built the reporting-day view (``synthesis.population.trips.final`` /
``...activities.final``) and the state draw; this task switches the four consumers that need
the FINISHED day onto that view, exports ``commute_day_state`` and adapts the two analysis
stages plus the cordon out-commuter expectation (ADR-0104 checks 1 and 3).

What is covered here, and why in this shape:

* **The two vendored-module overrides** (``spatial_locations_day``, ``output_day``) are driven
  with the vendored ``execute`` replaced by a recorder, so the assertion is exactly the
  contract the override owns -- WHICH frame the vendored code is handed under WHICH stage name
  -- without dragging a full location/iris/output fixture into a unit test. The vendored logic
  itself is unchanged and stays covered by its own tests.
* **The declarations** of the three modules that only swap a stage name
  (``secondary_chainsolvers``, ``braunschweig.matsim.scenario.population``,
  ``work_participation_by_kreis``) are checked STATICALLY on the source, the way
  ``tests/test_runtime_config_declares.py`` checks the runtime consumers: importing the
  chainsolvers package pulls in optional native dependencies, and a source-level guard catches
  a re-introduced pre-assignment read regardless of whether the module can be imported here.
* **The two new arithmetic paths** (the check-1 state-share table, the check-3 gate scaling)
  are tested as pure functions on synthetic frames.
* **The MATSim attribute** is asserted through the real ``add_person`` writer path.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from braunschweig.analysis import cordon_validation as CORDON  # noqa: E402
from braunschweig.analysis.synthesis import work_participation_by_kreis as WP  # noqa: E402
from braunschweig.synthesis.commute_day import output_day as OUTPUT  # noqa: E402
from braunschweig.synthesis.commute_day import spatial_locations_day as LOCATIONS  # noqa: E402
from synthesis.output import select_person_output_columns  # noqa: E402

TRIPS_STAGE = "synthesis.population.trips"
ACTIVITIES_STAGE = "synthesis.population.activities"
ENRICHED_STAGE = "synthesis.population.enriched"
DAY_TRIPS_STAGE = "synthesis.population.trips.final"
DAY_ACTIVITIES_STAGE = "synthesis.population.activities.final"
STATE_STAGE = "braunschweig.synthesis.commute_day.state_stage"


# --------------------------------------------------------------------------- stub contexts

class _ConfigureRecorder:
    """Records what ``configure`` declares, with synpp's two-argument config() signature.

    ``config()`` also RETURNS the effective value, because a synpp ``configure`` may branch on
    an option it just declared (``context.config(key, default)`` followed by
    ``if context.config(key):`` -- the pattern the cordon block and the reporting-day block of
    ``braunschweig.matsim.scenario.population`` both use). The declared default is therefore
    remembered on the first call and returned by the later single-argument read, exactly as
    synpp's ``ConfigurationContext`` does; a recorder that returned ``None`` there would make
    every such branch look disabled.
    """

    def __init__(self, config=None):
        self.stages = []
        self.config_keys = {}
        self._config = config or {}

    def stage(self, name, **_kwargs):
        self.stages.append(name)

    def config(self, name, default=None):
        if name not in self.config_keys:
            self.config_keys[name] = default
        if name in self._config:
            return self._config[name]
        return self.config_keys[name]


class _StubContext:
    """synpp's ExecuteContext contract: ``stage(name)`` and SINGLE-argument ``config(name)``.

    Both accessors fail loudly on anything ``configure`` did not declare, so a shim that
    forwards an unknown stage name to the real context fails here instead of being silently
    answered.
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
        assert name in self._declared.config_keys, f"config '{name}' was not declared"
        return self._config[name]


def _recorder(module, config=None):
    recorder = _ConfigureRecorder(config=config)
    module.configure(recorder)
    return recorder


class _ExecuteRecorder:
    """Stands in for a vendored ``execute``; keeps the context it was handed."""

    def __init__(self, result=None):
        self.context = None
        self.result = result if result is not None else pd.DataFrame(
            {"person_id": [1], "activity_index": [0]})

    def __call__(self, context):
        self.context = context
        return self.result


# --------------------------------------------------------------------------- spatial locations

def test_spatial_locations_day_declares_the_reporting_day_activities():
    recorder = _recorder(LOCATIONS)
    assert DAY_ACTIVITIES_STAGE in recorder.stages
    assert ACTIVITIES_STAGE not in recorder.stages
    # The vendored stage's remaining dependencies must survive the substitution untouched.
    for name in ("synthesis.population.spatial.home.locations",
                 "synthesis.population.spatial.primary.locations",
                 "synthesis.population.spatial.secondary.locations",
                 "synthesis.population.sampled", "data.spatial.iris"):
        assert name in recorder.stages


def test_spatial_locations_day_hands_the_day_activities_to_the_vendored_stage(monkeypatch):
    day_activities = pd.DataFrame({"person_id": [1, 2], "activity_index": [0, 0],
                                   "purpose": ["home", "work"]})
    sampled = pd.DataFrame({"person_id": [1, 2], "household_id": [1, 2]})
    recorder = _recorder(LOCATIONS)
    context = _StubContext(recorder, stages={DAY_ACTIVITIES_STAGE: day_activities,
                                             "synthesis.population.sampled": sampled})

    execute_recorder = _ExecuteRecorder()
    monkeypatch.setattr(LOCATIONS.base, "execute", execute_recorder)
    LOCATIONS.execute(context)

    shim = execute_recorder.context
    assert shim.stage(ACTIVITIES_STAGE) is day_activities
    # Everything else is forwarded to the real context, which still answers its own stages...
    assert shim.stage("synthesis.population.sampled") is sampled
    # ... and still refuses what configure() did not declare.
    with pytest.raises(AssertionError):
        shim.stage("synthesis.population.trips")


# --------------------------------------------------------------------------- synthesis output

def _enriched_persons():
    """Three persons; only 1 and 3 are workers in the state frame below."""
    return pd.DataFrame({
        "person_id": [1, 2, 3],
        "household_id": [1, 1, 2],
        "age": [40, 8, 33],
        "employed": [True, False, True],
        "sex": ["male", "female", "female"],
        "socioprofessional_class": [1, 1, 1],
        "has_license": [True, False, True],
        "has_pt_subscription": [False, False, True],
        "pt_subscription_type": ["never_pt", "never_pt", "deutschlandticket"],
        "census_person_id": [11, 12, 13],
        "hts_id": [21, 22, 23],
        "is_urban_resident": [True, True, False],
    })


def _states(values=("at_workplace", "home")):
    return pd.DataFrame({"person_id": [1, 3], "commute_day_state": list(values),
                         "reason": ["kept", "redrawn"]})


def _output_context(enabled, states=None):
    recorder = _recorder(OUTPUT, config={"mode_choice": False})
    stages = {
        DAY_TRIPS_STAGE: pd.DataFrame({"person_id": [1], "trip_index": [0]}),
        DAY_ACTIVITIES_STAGE: pd.DataFrame({"person_id": [1], "activity_index": [0]}),
        ENRICHED_STAGE: _enriched_persons(),
        STATE_STAGE: {"states": states if states is not None else _states()},
    }
    return _StubContext(recorder, stages=stages,
                        config={OUTPUT.KEY_ENABLED: enabled, "mode_choice": False})


def test_output_day_declares_the_reporting_day_view_and_the_state_stage():
    recorder = _recorder(OUTPUT, config={"mode_choice": False})
    assert DAY_TRIPS_STAGE in recorder.stages
    assert DAY_ACTIVITIES_STAGE in recorder.stages
    assert STATE_STAGE in recorder.stages
    assert TRIPS_STAGE not in recorder.stages
    assert ACTIVITIES_STAGE not in recorder.stages
    assert recorder.config_keys[OUTPUT.KEY_ENABLED] is True
    # The vendored writer's own dependencies stay declared.
    for name in (ENRICHED_STAGE, "synthesis.vehicles.vehicles",
                 "synthesis.population.spatial.locations", "documentation.meta_output"):
        assert name in recorder.stages


def test_output_day_off_hands_the_enriched_frame_through_untouched(monkeypatch):
    context = _output_context(enabled=False)
    execute_recorder = _ExecuteRecorder(result=None)
    monkeypatch.setattr(OUTPUT.base, "execute", execute_recorder)
    OUTPUT.execute(context)

    persons = execute_recorder.context.stage(ENRICHED_STAGE)
    assert OUTPUT.STATE_COLUMN not in persons.columns
    # The vendored column selection therefore returns the legacy list -> byte-identical CSV.
    columns = select_person_output_columns(persons.columns, "is_urban_resident")
    assert OUTPUT.STATE_COLUMN not in columns


def test_output_day_on_appends_the_state_column_last(monkeypatch):
    context = _output_context(enabled=True)
    execute_recorder = _ExecuteRecorder(result=None)
    monkeypatch.setattr(OUTPUT.base, "execute", execute_recorder)
    OUTPUT.execute(context)

    shim = execute_recorder.context
    persons = shim.stage(ENRICHED_STAGE)
    assert list(persons["person_id"]) == [1, 2, 3]
    assert list(persons[OUTPUT.STATE_COLUMN][:1]) == ["at_workplace"]
    # Person 2 is no worker: no state, written as an empty CSV field.
    assert pd.isna(persons[OUTPUT.STATE_COLUMN].iloc[1])
    columns = select_person_output_columns(persons.columns, "is_urban_resident")
    assert columns[-1] == OUTPUT.STATE_COLUMN
    # The day view reaches the vendored writer under the PRE-ASSIGNMENT names it reads.
    assert shim.stage(TRIPS_STAGE) is context.stage(DAY_TRIPS_STAGE)
    assert shim.stage(ACTIVITIES_STAGE) is context.stage(DAY_ACTIVITIES_STAGE)


def test_output_day_shim_forwards_an_unknown_name_to_the_real_context(monkeypatch):
    context = _output_context(enabled=True)
    execute_recorder = _ExecuteRecorder(result=None)
    monkeypatch.setattr(OUTPUT.base, "execute", execute_recorder)
    OUTPUT.execute(context)
    with pytest.raises(AssertionError):
        execute_recorder.context.stage("synthesis.vehicles.vehicles")


def test_attach_commute_day_state_raises_when_no_person_matches():
    persons = _enriched_persons()
    orphans = pd.DataFrame({"person_id": [901, 902],
                            "commute_day_state": ["home", "at_workplace"]})
    with pytest.raises(ValueError, match="broken person_id join"):
        OUTPUT.attach_commute_day_state(persons, orphans)


def test_attach_commute_day_state_refuses_an_existing_column():
    persons = _enriched_persons()
    persons["commute_day_state"] = "at_workplace"
    with pytest.raises(ValueError, match="already carries"):
        OUTPUT.attach_commute_day_state(persons, _states())


# --------------------------------------------------------------------------- static declarations

#: Modules that only swap the trips stage name; checked on the SOURCE (see the module docstring).
_FINAL_TRIPS_CONSUMERS = (
    "braunschweig/synthesis/locations/secondary_chainsolvers/__init__.py",
    "braunschweig/matsim/scenario/population.py",
    "braunschweig/analysis/synthesis/work_participation_by_kreis.py",
)

_PRE_ASSIGNMENT_READ = re.compile(r"""context\.stage\(\s*["']synthesis\.population\.trips["']""")


@pytest.mark.parametrize("relative_path", _FINAL_TRIPS_CONSUMERS)
def test_consumer_reads_the_reporting_day_trips(relative_path):
    source = (REPO / relative_path).read_text(encoding="utf-8")
    assert "synthesis.population.trips.final" in source, (
        f"{relative_path} must read the reporting-day trips (ADR-0104)")
    assert not _PRE_ASSIGNMENT_READ.search(source), (
        f"{relative_path} still reads the PRE-ASSIGNMENT synthesis.population.trips; the "
        "finished day is what these stages need")


def test_matsim_population_declares_the_day_view_and_the_state_stage():
    from braunschweig.matsim.scenario import population as POP

    recorder = _ConfigureRecorder(config={"cordon_enabled": False})
    POP.configure(recorder)
    assert POP.DAY_TRIPS_STAGE in recorder.stages
    assert POP.DAY_ACTIVITIES_STAGE in recorder.stages
    assert POP.STATE_STAGE in recorder.stages
    assert recorder.config_keys[POP.KEY_COMMUTE_DAY_STATE_ENABLED] is True

    # OFF (every configs/fixtures/* config): the whole donor/state chain stays out of the DAG,
    # while the reporting-day aliases -- pass-throughs there -- are still declared.
    disabled = _ConfigureRecorder(config={"cordon_enabled": False,
                                          POP.KEY_COMMUTE_DAY_STATE_ENABLED: False})
    POP.configure(disabled)
    assert POP.STATE_STAGE not in disabled.stages
    assert POP.DAY_TRIPS_STAGE in disabled.stages


def test_matsim_writer_emits_commute_day_state_only_for_persons_that_have_one():
    """``commuteDayState`` is additive: absent column -> no attribute, NaN -> no attribute."""
    from matsim.scenario import population as pop

    df_off = pd.DataFrame({field: [0] for field in pop.PERSON_FIELDS})
    assert pop.effective_person_fields(df_off) == pop.PERSON_FIELDS

    df_on = df_off.copy()
    df_on["commute_day_state"] = "home"
    fields_on = pop.effective_person_fields(df_on)
    assert fields_on == pop.PERSON_FIELDS + ["commute_day_state"]

    class _StubWriter:
        def __init__(self):
            self.attributes = {}

        def start_person(self, *args, **kwargs):
            pass

        def start_attributes(self):
            pass

        def end_attributes(self):
            pass

        def end_person(self, *args, **kwargs):
            pass

        def start_plan(self, *args, **kwargs):
            pass

        def end_plan(self, *args, **kwargs):
            pass

        def add_attribute(self, key, _type, value):
            self.attributes[key] = value

        def yes_no(self, value):
            return "yes" if value else "no"

        def location(self, *args, **kwargs):
            return None

        def add_activity(self, *args, **kwargs):
            pass

        def add_leg(self, *args, **kwargs):
            pass

    class _Geometry:
        x = 0.0
        y = 0.0

    def _person(fields, state=None):
        row = {field: 0 for field in fields}
        row.update(person_id=1, household_id=1, household_income="2600-3000", sex="female",
                   employed="yes", high_income=False, is_urban_resident=False,
                   has_pt_subscription=False, has_license=True,
                   pt_subscription_type="never_pt", household_income_eur=3000.0)
        if "commute_day_state" in fields:
            row["commute_day_state"] = state
        return tuple(row[field] for field in fields)

    activity = {field: 0 for field in pop.ACTIVITY_FIELDS}
    activity.update(person_id=1, purpose="home", start_time=float("nan"),
                    end_time=float("nan"), location_id=-1, geometry=_Geometry())
    activity = tuple(activity[field] for field in pop.ACTIVITY_FIELDS)

    writer_on = _StubWriter()
    pop.add_person(writer_on, _person(fields_on, "home"), [activity], [], [],
                   person_fields=fields_on)
    assert writer_on.attributes.get("commuteDayState") == "home"

    writer_nan = _StubWriter()
    pop.add_person(writer_nan, _person(fields_on, float("nan")), [activity], [], [],
                   person_fields=fields_on)
    assert "commuteDayState" not in writer_nan.attributes

    writer_off = _StubWriter()
    pop.add_person(writer_off, _person(pop.PERSON_FIELDS), [activity], [], [],
                   person_fields=pop.PERSON_FIELDS)
    assert "commuteDayState" not in writer_off.attributes


# --------------------------------------------------------------------------- check 1 arithmetic

def _participation_table(share_no_work_trip):
    """Compared-participation rows for the two Kreise used below plus the zgb row."""
    rows = []
    for code in WP.ZGB_KREISE + (WP.ZGB_ROW_CODE,):
        rows.append({
            "code": code, "n_employed": 10, "n_with_work_trip": 6,
            "share_work_trip": 0.6, "share_no_work_trip": share_no_work_trip,
            "srv_n_persons": 1000, "srv_share_work_trip": 0.6511,
            "srv_share_home_office_day": 0.1418, "srv_share_neither": 0.2071,
            "delta_work_trip_pp": float("nan"),
        })
    return pd.DataFrame(rows, columns=list(WP.PARTICIPATION_COLUMNS))


def test_commute_day_state_shares_computes_shares_and_the_srv_delta():
    # Four workers in Kreis 03101: 2 at_workplace, 1 home, 1 absent.
    states = pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "commute_day_state": ["at_workplace", "at_workplace", "home", "absent"],
    })
    persons = pd.DataFrame({"person_id": [1, 2, 3, 4], "household_id": [1, 2, 3, 4]})
    homes = pd.DataFrame({"household_id": [1, 2, 3, 4], "ars5": ["03101"] * 4})
    # Model: 35 % of the employed make no work trip; SrV remainder = 0.1418 + 0.2071 = 0.3489.
    participation = _participation_table(share_no_work_trip=0.35)

    table = WP.commute_day_state_shares(states, persons, homes, participation)
    assert list(table.columns) == list(WP.STATE_SHARE_COLUMNS)
    assert len(table) == len(WP.ZGB_KREISE) + 1

    kreis = table[table["code"] == "03101"].iloc[0]
    assert kreis["n_workers"] == 4
    assert kreis["share_at_workplace"] == pytest.approx(0.5)
    assert kreis["share_home"] == pytest.approx(0.25)
    assert kreis["share_absent"] == pytest.approx(0.25)
    # The zgb row is exactly the union of the Kreise -- here that is the same four workers.
    zgb = table[table["code"] == WP.ZGB_ROW_CODE].iloc[0]
    assert zgb["n_workers"] == 4
    assert zgb["share_at_workplace"] == pytest.approx(0.5)
    assert zgb["delta_no_work_trip_pp"] == pytest.approx(100.0 * (0.35 - (0.1418 + 0.2071)))
    # A Kreis without workers keeps NaN shares rather than a substituted zero.
    empty = table[table["code"] == "03102"].iloc[0]
    assert empty["n_workers"] == 0
    assert np.isnan(empty["share_home"])


def test_commute_day_state_shares_excludes_workers_outside_the_zgb():
    states = pd.DataFrame({"person_id": [1, 2],
                           "commute_day_state": ["at_workplace", "home"]})
    persons = pd.DataFrame({"person_id": [1, 2], "household_id": [1, 2]})
    homes = pd.DataFrame({"household_id": [1, 2], "ars5": ["03101", "09162"]})
    stats = {}
    table = WP.commute_day_state_shares(states, persons, homes,
                                        _participation_table(0.35), stats=stats)
    assert stats["n_outside_zgb"] == 1
    assert table[table["code"] == WP.ZGB_ROW_CODE].iloc[0]["n_workers"] == 1


def test_commute_day_state_shares_rejects_an_unknown_state():
    states = pd.DataFrame({"person_id": [1], "commute_day_state": ["teleporting"]})
    persons = pd.DataFrame({"person_id": [1], "household_id": [1]})
    homes = pd.DataFrame({"household_id": [1], "ars5": ["03101"]})
    with pytest.raises(ValueError, match="unknown state"):
        WP.commute_day_state_shares(states, persons, homes, _participation_table(0.35))


def test_check_1_section_reports_the_regional_row_and_the_pre_registered_band():
    states = pd.DataFrame({
        "person_id": [1, 2, 3, 4],
        "commute_day_state": ["at_workplace", "at_workplace", "home", "absent"]})
    persons = pd.DataFrame({"person_id": [1, 2, 3, 4], "household_id": [1, 2, 3, 4]})
    homes = pd.DataFrame({"household_id": [1, 2, 3, 4], "ars5": ["03101"] * 4})
    table = WP.commute_day_state_shares(states, persons, homes, _participation_table(0.35))

    section = "\n".join(WP._state_shares_section(table))
    assert "Check 1 (ADR-0104)" in section
    assert "+/- 3 pp on the regional aggregate only" in section
    assert "ASSUMPTION" in section
    assert "never gated" in section
    # 0.35 vs 0.3489 -> 0.11 pp, inside the pre-registered band.
    assert "within" in section
    # No section at all when the model produced no table (OFF path).
    assert WP._state_shares_section(None) == []


def test_check_1_verdict_uses_the_pre_registered_tolerance():
    assert WP.CHECK_1_TOLERANCE_PP == 3.0
    assert WP._check_1_verdict(2.9) == "within"
    assert WP._check_1_verdict(-3.0) == "within"
    assert WP._check_1_verdict(3.1) == "outside"
    assert WP._check_1_verdict(float("nan")) == "n/a"


# --------------------------------------------------------------------------- check 3 arithmetic

def _cordon_frames():
    states = pd.DataFrame({
        "person_id": [1, 2, 3, 4, 5],
        # Externals are persons 1-4 (3 at_workplace, 1 home); person 5 works internally.
        "commute_day_state": ["at_workplace", "at_workplace", "at_workplace", "home",
                              "home"],
    })
    work_locations = pd.DataFrame({"person_id": [1, 2, 3, 4, 5],
                                   "location_id": [101, 102, 103, 104, 105]})
    workplaces = pd.DataFrame({
        "location_id": [101, 102, 103, 104, 105],
        "commune_id": ["EXT03151000", "EXT03151000", "EXT03154000", "EXT03154000",
                       "03101000"],
    })
    return states, work_locations, workplaces


def test_external_at_workplace_share_counts_only_external_workers():
    states, work_locations, workplaces = _cordon_frames()
    stats = {}
    share = CORDON.external_at_workplace_share(states, work_locations, workplaces, stats=stats)
    assert share == pytest.approx(0.75)          # 3 of 4 external workers travel
    assert stats["n_external"] == 4
    assert stats["n_external_at_workplace"] == 3


def test_external_at_workplace_share_raises_without_a_single_external_worker():
    states, work_locations, workplaces = _cordon_frames()
    workplaces = workplaces.assign(commune_id="03101000")
    with pytest.raises(ValueError, match="external workplace"):
        CORDON.external_at_workplace_share(states, work_locations, workplaces)


def test_scaled_gate_volumes_scales_outbound_only_and_keeps_the_factor():
    assignment = pd.DataFrame({
        "ars5": ["03151", "03154", "03151"],
        "gate_id": ["g1", "g1", "g2"],
        "inbound": [100, 200, 50],
        "outbound": [400, 400, 100],
    })
    scaled = CORDON.scaled_gate_volumes(assignment, 0.75)
    assert list(scaled.columns) == list(CORDON.SCALED_GATE_COLUMNS)

    g1 = scaled[scaled["gate_id"] == "g1"].iloc[0]
    assert g1["inbound"] == 300           # inbound is NOT scaled
    assert g1["outbound"] == 800          # the register expectation is kept
    assert g1["outbound_at_workplace"] == 600
    assert g1["at_workplace_share_external"] == pytest.approx(0.75)
    g2 = scaled[scaled["gate_id"] == "g2"].iloc[0]
    assert g2["outbound_at_workplace"] == 75


def test_scaled_gate_volumes_rejects_a_share_outside_the_unit_interval():
    assignment = pd.DataFrame({"ars5": ["03151"], "gate_id": ["g1"],
                               "inbound": [10], "outbound": [10]})
    with pytest.raises(ValueError, match=r"\[0, 1\]"):
        CORDON.scaled_gate_volumes(assignment, 1.5)


def test_cordon_validation_declares_the_state_stage_only_on_a_cordon_run():
    off = _ConfigureRecorder(config={"cordon_enabled": False})
    CORDON.configure(off)
    assert CORDON.STATE_STAGE not in off.stages

    on = _ConfigureRecorder(config={"cordon_enabled": True,
                                    CORDON.KEY_COMMUTE_DAY_STATE_ENABLED: True})
    CORDON.configure(on)
    assert CORDON.STATE_STAGE in on.stages
    assert "synthesis.population.spatial.primary.locations" in on.stages
    assert "braunschweig.locations.work" in on.stages

    disabled = _ConfigureRecorder(config={"cordon_enabled": True,
                                          CORDON.KEY_COMMUTE_DAY_STATE_ENABLED: False})
    CORDON.configure(disabled)
    assert CORDON.STATE_STAGE not in disabled.stages
