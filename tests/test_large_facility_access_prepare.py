"""Wiring of the large-facility access rule and the departure insertion rule in matsim.simulation.prepare (#465).

ADR-0165: large facilities on low-capacity links get a high-capacity access link (Java step
``RunAdaptLargeFacilityAccess`` between ``RunPreparation`` and the population routing), and departing vehicles
no longer enter a link before the vehicles already driving on it (qsim
``insertingWaitingVehiclesBeforeDrivingVehicles`` = false).
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matsim.simulation import prepare

ACCESS_RUNNER = "org.eqasim.braunschweig.scenario.RunAdaptLargeFacilityAccess"
QSIM_PARAM = "insertingWaitingVehiclesBeforeDrivingVehicles"


class _Context:
    def __init__(self, config):
        self.values = dict(config)
        self.declared = {}

    def config(self, key, default=None, **kwargs):
        # Like synpp: the first call declares the default, later calls without a default return it.
        if key not in self.declared:
            self.declared[key] = default
        return self.values.get(key, self.declared[key])

    def stage(self, name):
        if name == "matsim.scenario.supply.processed":
            return {"network_path": "network.xml.gz", "schedule_path": "schedule.xml.gz"}
        if name == "matsim.scenario.supply.gtfs":
            return {"vehicles_path": "transit_vehicles.xml.gz"}
        return "input.xml.gz"

    def path(self, *args):
        return "work"

    @property
    def cache_path(self):
        return "cache"


def _base_config(**overrides):
    config = {"mode_choice": False, "sampling_rate": 0.01, "processes": 1, "matsim_threads": 1,
              "random_seed": 1234, "output_prefix": "bs_", "mid_passenger_availability": False,
              "braunschweig.population.method": None}
    config.update(overrides)
    return config


def _execute(monkeypatch, config):
    """Run prepare.execute with the Java boundary and the file system stubbed; return (java calls, set_param calls)."""
    context = _Context(config)
    prepare.configure(context)
    calls, params = [], []
    monkeypatch.setattr(prepare.eqasim, "run", lambda _ctx, runner, args: calls.append((runner, list(args))))
    monkeypatch.setattr(prepare.shutil, "copy", lambda *args: None)
    monkeypatch.setattr(prepare.os.path, "exists", lambda *args: True)
    monkeypatch.setattr(prepare.os, "remove", lambda *args: None)
    monkeypatch.setattr(prepare.config_modules, "set_param",
                        lambda path, module, name, value: params.append((str(path), module, name, value)) or "true")
    monkeypatch.setattr(prepare, "_log_large_facility_access_summary", lambda path: None)
    prepare.execute(context)
    return calls, params


def _option(args, name):
    return args[args.index("--" + name) + 1]


def test_configure_declares_the_rule_keys_with_the_measured_defaults():
    context = _Context({})
    prepare.configure(context)
    assert context.declared["large_facility_access_enabled"] is True
    assert context.declared["large_facility_access_minimum_activities"] == 1000.0
    assert context.declared["large_facility_access_minimum_capacity_veh_per_h"] == 1000.0
    assert context.declared["large_facility_access_maximum_distance_m"] == 1000.0
    assert context.declared["large_facility_access_maximum_fallback_share"] == 0.15
    assert context.declared["large_facility_access_excluded_prefixes"] == "outside_"
    assert context.declared["matsim_qsim_inserting_waiting_vehicles_first"] is False


def test_the_access_step_runs_between_preparation_and_routing_on_the_prepared_files(monkeypatch):
    calls, _ = _execute(monkeypatch, _base_config())
    runners = [runner for runner, _ in calls]
    preparation = runners.index("org.eqasim.core.scenario.preparation.RunPreparation")
    access = runners.index(ACCESS_RUNNER)
    routing = runners.index("org.eqasim.core.scenario.routing.RunPopulationRouting")
    assert preparation < access < routing

    args = calls[access][1]
    assert _option(args, "network-path") == "bs_network.xml.gz"
    assert _option(args, "input-facilities-path") == _option(args, "output-facilities-path") == "bs_facilities.xml.gz"
    assert (_option(args, "input-population-path") == _option(args, "output-population-path")
            == "prepared_population.xml.gz")
    assert _option(args, "summary-path") == "large_facility_access_summary.csv"
    assert float(_option(args, "sample-size")) == 0.01
    assert float(_option(args, "minimum-activities")) == 1000.0
    assert float(_option(args, "minimum-capacity-veh-per-h")) == 1000.0
    assert float(_option(args, "maximum-distance-m")) == 1000.0
    assert float(_option(args, "maximum-fallback-share")) == 0.15
    assert _option(args, "excluded-facility-prefixes") == "outside_"


def test_configured_values_reach_the_java_step(monkeypatch):
    calls, _ = _execute(monkeypatch, _base_config(
        large_facility_access_minimum_activities=2000.0, large_facility_access_minimum_capacity_veh_per_h=1500.0,
        large_facility_access_maximum_distance_m=500.0, large_facility_access_maximum_fallback_share=0.05,
        large_facility_access_excluded_prefixes="outside_,ic_"))
    args = next(args for runner, args in calls if runner == ACCESS_RUNNER)
    assert float(_option(args, "minimum-activities")) == 2000.0
    assert float(_option(args, "minimum-capacity-veh-per-h")) == 1500.0
    assert float(_option(args, "maximum-distance-m")) == 500.0
    assert float(_option(args, "maximum-fallback-share")) == 0.05
    assert _option(args, "excluded-facility-prefixes") == "outside_,ic_"


def test_disabling_the_rule_skips_the_java_step(monkeypatch):
    calls, _ = _execute(monkeypatch, _base_config(large_facility_access_enabled=False))
    assert ACCESS_RUNNER not in [runner for runner, _ in calls]


def test_departing_vehicles_wait_for_driving_vehicles_by_default(monkeypatch):
    _, params = _execute(monkeypatch, _base_config())
    assert params == [("work/bs_config.xml", "qsim", QSIM_PARAM, "false")]


def test_the_matsim_default_can_be_restored(monkeypatch):
    _, params = _execute(monkeypatch, _base_config(matsim_qsim_inserting_waiting_vehicles_first=True))
    assert params == [("work/bs_config.xml", "qsim", QSIM_PARAM, "true")]


@pytest.mark.parametrize("key, value", [
    ("large_facility_access_minimum_activities", -1.0),
    ("large_facility_access_minimum_capacity_veh_per_h", -1.0),
    ("large_facility_access_maximum_distance_m", 0.0),
    ("large_facility_access_maximum_fallback_share", 1.5),
])
def test_invalid_rule_parameters_fail_at_configure(key, value):
    with pytest.raises(ValueError, match=key):
        prepare.configure(_Context(_base_config(**{key: value})))


def test_the_summary_rates_are_logged_from_the_java_summary_file(tmp_path, caplog):
    summary = tmp_path / "large_facility_access_summary.csv"
    summary.write_text(
        "facility_id;activities_in_sample;activities_100pct;old_link_id;old_link_capacity_veh_per_h;outcome;"
        "new_link_id;new_link_capacity_veh_per_h;distance_meters\n"
        "a;10;1000.0;1;600.0;moved;2;1200.0;100.0\n"
        "b;10;1000.0;1;600.0;moved;3;1500.0;300.0\n"
        "c;10;1000.0;1;600.0;moved;4;1000.0;50.0\n"
        "d;10;1000.0;5;600.0;fallback;;;\n"
        "e;10;1000.0;6;1000.0;adequate;;;\n"
        "outside_1;10;1000.0;7;600.0;excluded;;;\n", encoding="utf-8")
    with caplog.at_level("INFO"):
        counts = prepare._log_large_facility_access_summary(str(summary))
    assert counts == {"moved": 3, "fallback": 1, "adequate": 1, "excluded": 1}
    assert "moved 3/4 (75.0 %), fallback 1/4 (25.0 %)" in caplog.text
    assert any(record.levelname == "WARNING" for record in caplog.records)
