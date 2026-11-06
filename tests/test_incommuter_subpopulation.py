"""The cross-cordon in-commuters keep their MATSim subpopulation (eqasim-bs#566).

The synthesis labels the SvB in-commuters ``incommuter`` and the student in-commuters ``student_incommuter``;
both labels must reach the prepared population as the person attribute ``subpopulation``, and the prepared
config must give each label its fixed-mode strategy block, or MATSim runs them as residents. Covered here:

* **The vendored writer** (``matsim.scenario.population``) writes the optional ``subpopulation`` column as the
  person attribute ``subpopulation`` (java.lang.String); a missing value writes NO attribute (the default
  subpopulation, i.e. a resident); a malformed value raises.
* **The regional wrapper** (``braunschweig.matsim.scenario.population.execute``, the primary path): with the
  cordon on, the label of every SvB and student in-commuter survives the merge onto the resident column set and
  reaches the plans, residents get none; an in-commuter row without a label raises; with the cordon off the
  plans carry no ``subpopulation`` at all.
* **The prepared config** (``braunschweig.matsim.simulation.cordon_subpopulation`` + the prepare stage): one
  KeepLastSelected + ReRoute block per in-commuter label, only strategies the DiscreteModeChoice
  mode-choice-in-the-loop check accepts, and the synthesis labels are exactly the labels the config wires.
"""
from __future__ import annotations

import contextlib
import gzip
import io
import sys
from pathlib import Path
from xml.etree import ElementTree

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import matsim.scenario.population as pop  # noqa: E402
import matsim.writers as writers  # noqa: E402
from braunschweig.matsim import initial_mode_choice as I  # noqa: E402
from braunschweig.matsim.scenario import population as POP  # noqa: E402
from braunschweig.matsim.simulation import cordon_subpopulation as CS  # noqa: E402
from braunschweig.matsim.simulation import plan_check as C  # noqa: E402
from braunschweig.matsim.simulation import prepare  # noqa: E402

CRS = "EPSG:25832"
SVB_ID = 900001
STUDENT_ID = 10_900_001


# --------------------------------------------------------------------------- helpers

def _person_values(**overrides):
    values = {
        "person_id": 1, "household_income": "3000", "car_availability": "all",
        "bicycle_availability": "none", "census_household_id": 1, "census_person_id": 1,
        "household_id": 1, "has_license": True, "has_pt_subscription": False, "hts_id": 1,
        "hts_household_id": 1, "age": 40, "employed": "yes", "sex": "male",
        "high_income": False, "is_urban_resident": False, "pt_subscription_type": "never_pt",
        "household_income_eur": 4321.0,
    }
    values.update(overrides)
    return values


def _write_one_person(subpopulation):
    """Write ONE person with a ``subpopulation`` field through the real ``add_person``; the XML text."""
    person_fields = pop.PERSON_FIELDS + ["subpopulation"]
    values = _person_values(subpopulation=subpopulation)
    person = tuple(values[field] for field in person_fields)
    activities = [tuple({"person_id": 1, "start_time": np.nan, "end_time": np.nan, "purpose": "home",
                         "geometry": Point(605000.0, 5790000.0), "location_id": -1}[field]
                        for field in pop.ACTIVITY_FIELDS)]
    buffer = io.BytesIO()
    writer = writers.PopulationWriter(buffer)
    writer.start_population()
    pop.add_person(writer, person, activities, [], [], person_fields=person_fields)
    writer.end_population()
    return buffer.getvalue().decode("utf-8")


def _person_attributes(root, person_id):
    person = root.find(f"person[@id='{person_id}']")
    assert person is not None, f"person {person_id} missing from the plans"
    return {attribute.get("name"): (attribute.get("class"), attribute.text)
            for attribute in person.findall("attributes/attribute")}


class _Progress:
    def update(self):
        pass


class _ConfigureRecorder:
    """synpp ConfigurationContext stand-in (contract of tests/test_population_parking_attributes.py)."""

    def __init__(self, config):
        self.stages = []
        self.config_keys = {}
        self._config = config

    def stage(self, name, **_kwargs):
        self.stages.append(name)

    def config(self, name, default=None):
        if name not in self.config_keys:
            self.config_keys[name] = default
        if name in self._config:
            return self._config[name]
        return self.config_keys[name]


class _ExecuteContext:
    """synpp ExecuteContext stand-in refusing any stage or key ``configure`` did not declare."""

    def __init__(self, declared, stages, config, path):
        self._declared, self._stages, self._config, self._path = declared, stages, config, path

    def stage(self, name):
        assert name in self._declared.stages, f"stage '{name}' was not declared in configure()"
        return self._stages[name]

    def config(self, name):
        assert name in self._declared.config_keys, f"config '{name}' was not declared"
        return self._config.get(name, self._declared.config_keys[name])

    def path(self):
        return str(self._path)

    @contextlib.contextmanager
    def progress(self, **_kwargs):
        yield _Progress()


def _one_day(person_id, subpopulation=None, home=Point(590000.0, 5770000.0)):
    """Frames of one person driving home -> work -> home; ``subpopulation`` None leaves the column out."""
    persons = pd.DataFrame([_person_values(person_id=person_id, household_id=person_id,
                                           census_household_id=person_id, census_person_id=person_id,
                                           hts_id=person_id, hts_household_id=person_id)])
    if subpopulation is not None:
        persons["subpopulation"] = subpopulation
    return {
        "persons": persons,
        "activities": pd.DataFrame({
            "person_id": [person_id] * 3, "activity_index": [0, 1, 2], "purpose": ["home", "work", "home"],
            "start_time": [np.nan, 28800.0, 63000.0], "end_time": [25200.0, 57600.0, np.nan]}),
        "locations": gpd.GeoDataFrame({
            "person_id": [person_id] * 3, "activity_index": [0, 1, 2],
            "location_id": [-1, f"work_{person_id}", -1],
            "geometry": [home, Point(605100.0, 5790200.0), home]}, crs=CRS),
        "trips": pd.DataFrame({
            "person_id": [person_id] * 2, "trip_index": [0, 1], "mode": ["car", "car"],
            "departure_time": [25200.0, 57600.0], "arrival_time": [28800.0, 63000.0]}),
        "vehicles": pd.DataFrame({"owner_id": [person_id], "vehicle_id": [f"{person_id}:car"], "mode": ["car"]}),
    }


def _wrapper_stages(svb_subpopulation="incommuter", student_subpopulation="student_incommuter"):
    residents = _one_day(1, home=Point(603250.0, 5791250.0))
    return {
        "synthesis.population.enriched": residents["persons"],
        "synthesis.population.spatial.locations": residents["locations"],
        "synthesis.vehicles.vehicles": (pd.DataFrame(), residents["vehicles"]),
        POP.DAY_TRIPS_STAGE: residents["trips"],
        POP.DAY_ACTIVITIES_STAGE: residents["activities"],
        "braunschweig.synthesis.incommuters": _one_day(SVB_ID, svb_subpopulation),
        "braunschweig.synthesis.student_incommuters": _one_day(STUDENT_ID, student_subpopulation),
    }


def _run_wrapper(tmp_path, cordon_enabled=True, **stage_options):
    config = {"cordon_enabled": cordon_enabled, "commute_day_state_enabled": False, "day_absence_enabled": False,
              "enable_urban_parking": False, "write_income_eur": False, "parking_zones_enabled": False,
              "remode_carless_car_legs": False}
    declared = _ConfigureRecorder(config)
    POP.configure(declared)
    context = _ExecuteContext(declared, _wrapper_stages(**stage_options), config, tmp_path)
    POP.execute(context)
    with gzip.open(tmp_path / "population.xml.gz", "rb") as handle:
        return ElementTree.fromstring(handle.read())


# --------------------------------------------------------------------------- writer

def test_subpopulation_is_an_optional_person_field():
    assert "subpopulation" in pop.OPTIONAL_PERSON_FIELDS
    assert "subpopulation" not in pop.PERSON_FIELDS


def test_the_writer_writes_the_subpopulation_as_a_string_person_attribute():
    xml = _write_one_person("incommuter")
    assert '<attribute name="subpopulation" class="java.lang.String">incommuter</attribute>' in xml


@pytest.mark.parametrize("missing", [None, np.nan, pd.NA])
def test_a_missing_subpopulation_writes_no_attribute(missing):
    assert 'name="subpopulation"' not in _write_one_person(missing)


@pytest.mark.parametrize("value", ["", "nan", "None", "<NA>", 3, 1.0])
def test_a_malformed_subpopulation_raises(value):
    with pytest.raises(ValueError, match="subpopulation"):
        _write_one_person(value)


# --------------------------------------------------------------------------- wrapper: the primary path

def test_both_incommuter_groups_keep_their_subpopulation_in_the_written_plans(tmp_path):
    root = _run_wrapper(tmp_path)
    assert _person_attributes(root, SVB_ID)["subpopulation"] == ("java.lang.String", "incommuter")
    assert _person_attributes(root, STUDENT_ID)["subpopulation"] == ("java.lang.String", "student_incommuter")
    assert "subpopulation" not in _person_attributes(root, 1)


@pytest.mark.parametrize("group", ["svb_subpopulation", "student_subpopulation"])
def test_an_incommuter_without_a_subpopulation_raises(tmp_path, group):
    with pytest.raises(ValueError, match="subpopulation"):
        _run_wrapper(tmp_path, **{group: np.nan})


def test_without_the_cordon_no_person_carries_a_subpopulation(tmp_path):
    root = _run_wrapper(tmp_path, cordon_enabled=False)
    assert [person.get("id") for person in root.findall("person")] == ["1"]
    assert "subpopulation" not in _person_attributes(root, 1)


# --------------------------------------------------------------------------- the labels match the synthesis

def test_the_synthesis_labels_are_the_subpopulations_the_config_wires():
    from braunschweig.synthesis import incommuters, student_incommuters

    ids = pd.DataFrame({"person_id": [5], "household_id": [5]})
    svb = incommuters._build_persons(ids, pd.DataFrame({"person_id": [7]}), "person_id", ["car"], [3000.0])
    students = student_incommuters._build_student_persons(ids, pd.DataFrame({"person_id": [7]}), ["pt"])
    assert set(svb["subpopulation"]) == {CS.INCOMMUTER_SUBPOPULATION}
    assert set(students["subpopulation"]) == {CS.STUDENT_INCOMMUTER_SUBPOPULATION}
    assert CS.INCOMMUTER_SUBPOPULATIONS == (CS.INCOMMUTER_SUBPOPULATION, CS.STUDENT_INCOMMUTER_SUBPOPULATION)


# --------------------------------------------------------------------------- the prepared config

_CONFIG = """<?xml version="1.0" ?>
<config>
  <module name="replanning" >
    <param name="maxAgentPlanMemorySize" value="1" />
    <parameterset type="strategysettings" >
      <param name="strategyName" value="DiscreteModeChoice" />
      <param name="weight" value="0.05" />
    </parameterset>
    <parameterset type="strategysettings" >
      <param name="strategyName" value="KeepLastSelected" />
      <param name="weight" value="0.95" />
    </parameterset>
  </module>
</config>
"""


def _strategies(config_xml):
    """(strategyName, weight, subpopulation or None) per strategysettings block of the replanning module."""
    module = ElementTree.fromstring(config_xml.encode("utf-8")).find("module[@name='replanning']")
    blocks = []
    for block in module.findall("parameterset[@type='strategysettings']"):
        params = {param.get("name"): param.get("value") for param in block.findall("param")}
        blocks.append((params["strategyName"], float(params["weight"]), params.get("subpopulation")))
    return blocks


def test_each_incommuter_label_gets_keep_last_selected_and_reroute():
    out = CS.add_incommuter_fixed_mode_strategies(_CONFIG, reroute_weight=0.1)
    blocks = _strategies(out)
    for subpopulation in CS.INCOMMUTER_SUBPOPULATIONS:
        assert sorted((name, weight) for name, weight, sub in blocks if sub == subpopulation) == [
            ("KeepLastSelected", 0.9), ("ReRoute", 0.1)]
    # The residents' blocks are untouched.
    assert [(name, weight) for name, weight, sub in blocks if sub is None] == [
        ("DiscreteModeChoice", 0.05), ("KeepLastSelected", 0.95)]


def test_only_strategies_the_mode_choice_in_the_loop_check_accepts_are_written():
    """DiscreteModeChoiceConfigurator.checkModeChoiceInTheLoop aborts the run on any other enabled strategy."""
    out = CS.add_incommuter_fixed_mode_strategies(_CONFIG, reroute_weight=0.1)
    assert {name for name, _, _ in _strategies(out)} <= CS.MODE_CHOICE_IN_THE_LOOP_STRATEGIES
    assert CS.MODE_CHOICE_IN_THE_LOOP_STRATEGIES == frozenset({"KeepLastSelected", "DiscreteModeChoice", "ReRoute"})


def test_the_transform_is_idempotent_per_subpopulation():
    once = CS.add_incommuter_fixed_mode_strategies(_CONFIG, reroute_weight=0.1)
    assert CS.add_incommuter_fixed_mode_strategies(once, reroute_weight=0.1) == once


def test_a_subpopulation_param_outside_the_replanning_strategies_does_not_suppress_the_blocks():
    """A per-subpopulation scoring parameter set must not count as an existing strategy block."""
    scoring = ('  <module name="scoring" >\n    <parameterset type="scoringParameters" >\n'
               '      <param name="subpopulation" value="incommuter" />\n    </parameterset>\n  </module>\n')
    config = _CONFIG.replace("</config>", scoring + "</config>")
    blocks = _strategies(CS.add_incommuter_fixed_mode_strategies(config, reroute_weight=0.1))
    assert ("KeepLastSelected", 0.9, CS.INCOMMUTER_SUBPOPULATION) in blocks


def test_a_config_without_a_replanning_module_raises():
    with pytest.raises(ValueError, match="replanning"):
        CS.add_incommuter_fixed_mode_strategies("<config></config>")


@pytest.mark.parametrize("weight", [0.0, 1.0, -0.1, 1.5, float("nan"), True, "0.1"])
def test_an_invalid_reroute_weight_raises(weight):
    with pytest.raises(ValueError, match=CS.KEY_REROUTE_WEIGHT):
        CS.add_incommuter_fixed_mode_strategies(_CONFIG, reroute_weight=weight)


def test_prepare_declares_the_reroute_weight_only_with_the_cordon():
    class _Recorder:
        def __init__(self, cordon):
            self.calls, self._cordon = {}, cordon

        def config(self, key, default=None, **_kwargs):
            self.calls[key] = default
            return {"cordon_enabled": self._cordon, "freight_enabled": False}.get(key, default)

        def stage(self, name, **_kwargs):
            pass

    on, off = _Recorder(True), _Recorder(False)
    prepare.configure(on)
    prepare.configure(off)
    assert on.calls[CS.KEY_REROUTE_WEIGHT] == CS.DEFAULT_REROUTE_WEIGHT == 0.1
    assert CS.KEY_REROUTE_WEIGHT not in off.calls


def test_the_strategy_module_is_part_of_the_prepare_token():
    assert CS in prepare._HELPER_MODULES


def test_prepare_writes_the_incommuter_strategies_into_the_final_config(tmp_path, monkeypatch):
    """With the cordon on, the blocks go into the config after the cut and the freight injection."""
    seen = []
    (tmp_path / "config.xml").write_text(_CONFIG, encoding="utf-8")
    monkeypatch.setattr(prepare.delegate, "execute", lambda context: "uncut_config.xml")
    monkeypatch.setattr(prepare, "_verify_gate_facility_links", lambda context: None)
    monkeypatch.setattr(prepare, "_attach_vrb_tariff_zones", lambda context: None)
    monkeypatch.setattr(prepare, "_cut_to_cordon", lambda context: seen.append("cut") or "config.xml")
    monkeypatch.setattr(prepare, "_inject_freight", lambda context, result: seen.append(("freight", result)))
    monkeypatch.setattr(prepare, "_summarise_final_schedule", lambda context: {})
    monkeypatch.setattr(prepare, "_write_vrb_fare_inputs", lambda context, result, summary: None)
    monkeypatch.setattr(I, "run", lambda context, result, excluded: seen.append(("initial", _strategies(
        (tmp_path / result).read_text(encoding="utf-8")), excluded)))

    class _Context:
        _values = {prepare.VRB_FARES_KEY: True, "cordon_enabled": True, "freight_enabled": True,
                   prepare.PARKING_KEY: False, C.KEY_ENABLED: False, I.KEY_ENABLED: True,
                   CS.KEY_REROUTE_WEIGHT: 0.1, "braunschweig.frozen_pt_walk_lift.enabled": False}

        def config(self, key):
            return self._values[key]

        def path(self):
            return str(tmp_path)

    assert prepare.execute(_Context()) == "config.xml"
    assert seen[:2] == ["cut", ("freight", "config.xml")]
    _, initial_blocks, excluded = seen[2]
    for subpopulation in CS.INCOMMUTER_SUBPOPULATIONS:
        assert ("ReRoute", 0.1, subpopulation) in initial_blocks
    # the standalone mode choice leaves the same subpopulations unchanged as the loop does
    assert excluded == CS.INCOMMUTER_SUBPOPULATIONS == ("incommuter", "student_incommuter")


def test_prepare_leaves_the_config_alone_without_the_cordon(monkeypatch):
    monkeypatch.setattr(prepare.delegate, "execute", lambda context: "config.xml")
    monkeypatch.setattr(prepare, "_attach_vrb_tariff_zones", lambda context: None)
    monkeypatch.setattr(prepare, "_summarise_final_schedule", lambda context: {})
    monkeypatch.setattr(prepare, "_write_vrb_fare_inputs", lambda context, result, summary: None)
    monkeypatch.setattr(prepare, "_add_incommuter_strategies",
                        lambda context, result: pytest.fail("strategies written without the cordon"))

    class _Context:
        _values = {prepare.VRB_FARES_KEY: True, "cordon_enabled": False, "freight_enabled": False,
                   prepare.PARKING_KEY: False, C.KEY_ENABLED: False, I.KEY_ENABLED: False,
                   "braunschweig.frozen_pt_walk_lift.enabled": False}

        def config(self, key):
            return self._values[key]

    assert prepare.execute(_Context()) == "config.xml"
