"""Wiring of the large-site access rule and the departure insertion rule in matsim.simulation.prepare (#465).

ADR-0165: large work and university sites spread their car access over the parking lots around them, other large
sites get one high-capacity link (Java step ``RunAdaptLargeFacilityAccess`` between ``RunPreparation`` and the
population routing), and departing vehicles no longer enter a link before the vehicles already driving on it (qsim
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
LOTS_REL_PATH = "braunschweig/parking/parking_lots_osm_2026.csv"
LOTS_TEXT = "lot_id;x;y;spaces;capacity_source;access;parking;name\nway/1;600000.00;5800000.00;100;area;;surface;\n"


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


@pytest.fixture
def data_path(tmp_path):
    lots = tmp_path / LOTS_REL_PATH
    lots.parent.mkdir(parents=True)
    lots.write_text(LOTS_TEXT, encoding="utf-8")
    return tmp_path


def _base_config(data_path, **overrides):
    config = {"mode_choice": False, "sampling_rate": 0.01, "processes": 1, "matsim_threads": 1,
              "random_seed": 1234, "output_prefix": "bs_", "mid_passenger_availability": False,
              "braunschweig.population.method": None, "data_path": str(data_path)}
    config.update(overrides)
    return config


def _execute(monkeypatch, config):
    """Run prepare.execute with the Java boundary stubbed; return (java calls, set_param calls)."""
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


def test_configure_declares_the_rule_keys_with_their_defaults(data_path):
    context = _Context({"data_path": str(data_path)})
    prepare.configure(context)
    expected = {
        "large_facility_access_enabled": True,
        "large_facility_access_minimum_activities": 1000.0,
        "large_facility_access_minimum_capacity_veh_per_h": 1000.0,
        "large_facility_access_maximum_distance_m": 1000.0,
        "large_facility_access_maximum_fallback_share": 0.15,
        "large_facility_access_excluded_prefixes": "outside_,EXT,home_",
        "large_facility_access_parking_lots_path": LOTS_REL_PATH,
        "large_facility_access_parking_demand_share": 0.5,
        "large_facility_access_parking_maximum_distance_m": 1500.0,
        "large_facility_access_lot_link_maximum_distance_m": 300.0,
        "large_facility_access_maximum_no_lot_share": 0.10,
        "large_facility_access_parking_scope_work_share": 0.5,
        "large_facility_access_parking_scope_prefixes": "uni_loc_",
        "matsim_qsim_inserting_waiting_vehicles_first": False,
    }
    assert {key: context.declared[key] for key in expected} == expected


def test_the_access_step_runs_between_preparation_and_routing_with_every_option(monkeypatch, data_path):
    calls, _ = _execute(monkeypatch, _base_config(data_path))
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
    assert Path(_option(args, "parking-lots-path")) == (data_path / LOTS_REL_PATH)
    assert Path(_option(args, "parking-lots-path")).is_absolute()
    assert _option(args, "summary-path") == "large_facility_access_summary.csv"
    assert _option(args, "groups-path") == "large_facility_access_groups.csv"
    numbers = {"sample-size": 0.01, "minimum-activities": 1000.0, "minimum-capacity-veh-per-h": 1000.0,
               "maximum-distance-m": 1000.0, "maximum-fallback-share": 0.15, "parking-demand-share": 0.5,
               "parking-maximum-distance-m": 1500.0, "lot-link-maximum-distance-m": 300.0,
               "maximum-no-lot-share": 0.10, "scope-work-share": 0.5}
    assert {name: float(_option(args, name)) for name in numbers} == numbers
    assert _option(args, "excluded-facility-prefixes") == "outside_,EXT,home_"
    assert _option(args, "scope-facility-prefixes") == "uni_loc_"


def test_configured_values_reach_the_java_step(monkeypatch, data_path):
    calls, _ = _execute(monkeypatch, _base_config(
        data_path, large_facility_access_parking_demand_share=0.7, large_facility_access_parking_maximum_distance_m=900.0,
        large_facility_access_lot_link_maximum_distance_m=150.0, large_facility_access_maximum_no_lot_share=0.2,
        large_facility_access_parking_scope_work_share=0.6, large_facility_access_parking_scope_prefixes="uni_loc_,fh_",
        large_facility_access_excluded_prefixes="outside_,EXT,home_,ic_"))
    args = next(args for runner, args in calls if runner == ACCESS_RUNNER)
    assert float(_option(args, "parking-demand-share")) == 0.7
    assert float(_option(args, "parking-maximum-distance-m")) == 900.0
    assert float(_option(args, "lot-link-maximum-distance-m")) == 150.0
    assert float(_option(args, "maximum-no-lot-share")) == 0.2
    assert float(_option(args, "scope-work-share")) == 0.6
    assert _option(args, "scope-facility-prefixes") == "uni_loc_,fh_"
    assert _option(args, "excluded-facility-prefixes") == "outside_,EXT,home_,ic_"


def test_a_missing_lots_file_fails_before_any_java_step(monkeypatch, tmp_path):
    with pytest.raises(FileNotFoundError, match="large_facility_access_parking_lots_path"):
        _execute(monkeypatch, _base_config(tmp_path))


def test_disabling_the_rule_skips_the_java_step_and_needs_no_lots_file(monkeypatch, tmp_path):
    calls, _ = _execute(monkeypatch, _base_config(tmp_path, large_facility_access_enabled=False))
    assert ACCESS_RUNNER not in [runner for runner, _ in calls]


def test_departing_vehicles_wait_for_driving_vehicles_by_default(monkeypatch, data_path):
    _, params = _execute(monkeypatch, _base_config(data_path))
    assert params == [("work/bs_config.xml", "qsim", QSIM_PARAM, "false")]


def test_the_matsim_default_can_be_restored(monkeypatch, data_path):
    _, params = _execute(monkeypatch, _base_config(data_path, matsim_qsim_inserting_waiting_vehicles_first=True))
    assert params == [("work/bs_config.xml", "qsim", QSIM_PARAM, "true")]


@pytest.mark.parametrize("key, value", [
    ("large_facility_access_minimum_activities", -1.0),
    ("large_facility_access_minimum_capacity_veh_per_h", -1.0),
    ("large_facility_access_maximum_distance_m", 0.0),
    ("large_facility_access_maximum_fallback_share", 1.5),
    ("large_facility_access_parking_demand_share", 0.0),
    ("large_facility_access_parking_demand_share", 11.0),
    ("large_facility_access_parking_maximum_distance_m", 0.0),
    ("large_facility_access_lot_link_maximum_distance_m", -5.0),
    ("large_facility_access_maximum_no_lot_share", 1.5),
    ("large_facility_access_parking_scope_work_share", 1.5),
])
def test_invalid_rule_parameters_fail_at_configure(key, value, data_path):
    with pytest.raises(ValueError, match=key):
        prepare.configure(_Context(_base_config(data_path, **{key: value})))


def test_the_summary_rates_are_logged_from_the_java_summary_file(tmp_path, caplog):
    summary = tmp_path / "large_facility_access_summary.csv"
    rows = [("a", "true", "distributed", 4), ("b", "true", "distributed", 2), ("c", "true", "distributed_partial", 1),
            ("d", "true", "moved", 0), ("e", "false", "moved", 0), ("f", "false", "fallback", 0),
            ("g", "false", "adequate", 0)]
    summary.write_text("site_key;in_scope;outcome;lots_used\n" + "".join(
        f"{key};{scope};{outcome};{lots}\n" for key, scope, outcome, lots in rows), encoding="utf-8")
    with caplog.at_level("INFO"):
        counts = prepare._log_large_facility_access_summary(str(summary))
    assert counts == {"distributed": 2, "distributed_partial": 1, "moved": 2, "fallback": 1, "adequate": 1,
                      "in_scope": 4, "in_scope_without_lot": 1}
    assert ("7 large sites, 4 in scope; distributed over parking lots 3/4 (75.0 %), of them partly covered 1; "
            "in scope without a usable lot 1/4 (25.0 %)") in caplog.text
    assert "single-link rule: adequate 1, moved 2, fallback 1/3 (33.3 %)" in caplog.text
    assert any(record.levelname == "WARNING" for record in caplog.records)


def test_prefix_lists_reach_java_as_comma_text_and_other_types_fail(monkeypatch, data_path):
    calls, _ = _execute(monkeypatch, _base_config(
        data_path, large_facility_access_excluded_prefixes=["outside_", "EXT", "home_"],
        large_facility_access_parking_scope_prefixes=("uni_loc_",)))
    args = next(args for runner, args in calls if runner == ACCESS_RUNNER)
    assert _option(args, "excluded-facility-prefixes") == "outside_,EXT,home_"
    assert _option(args, "scope-facility-prefixes") == "uni_loc_"
    for bad in (7, ["outside_", 3], "outside_,,EXT"):
        with pytest.raises(ValueError, match="large_facility_access_excluded_prefixes"):
            prepare.configure(_Context(_base_config(data_path, large_facility_access_excluded_prefixes=bad)))


@pytest.mark.parametrize("key", ["large_facility_access_enabled", "matsim_qsim_inserting_waiting_vehicles_first"])
def test_switches_must_be_booleans(key, data_path):
    with pytest.raises(ValueError, match=key):
        prepare.configure(_Context(_base_config(data_path, **{key: "false"})))


def test_a_lots_file_without_the_required_columns_fails_before_any_java_step(monkeypatch, data_path):
    (data_path / LOTS_REL_PATH).write_text("lot_id;x;y\nway/1;1;2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="spaces"):
        _execute(monkeypatch, _base_config(data_path))


def test_the_production_stage_token_follows_the_lots_file(data_path):
    """The pipeline runs this stage through its alias braunschweig.matsim.simulation.prepare (configs/base_bs.yml),
    whose validate() is the token synpp uses; a rebuilt lots release must change it."""
    from braunschweig.matsim.simulation import prepare as production

    context = _Context(_base_config(data_path))
    prepare.configure(context)
    first = production.validate(context)
    (data_path / LOTS_REL_PATH).write_text(LOTS_TEXT + "way/2;600010.00;5800000.00;5;tag;;;\n", encoding="utf-8")
    second = production.validate(context)
    assert first["files"]["parking_lots_osm_2026"] != second["files"]["parking_lots_osm_2026"]
    assert first["modules"] == second["modules"]
    (data_path / LOTS_REL_PATH).unlink()
    assert production.validate(context)["files"]["parking_lots_osm_2026"] == "absent"
