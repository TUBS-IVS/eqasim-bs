"""Capability-gated Java config adaptation for MiD passenger availability."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from matsim.simulation import prepare


class _Context:
    def __init__(self, config):
        self.values = dict(config)
        self.declared = set()

    def config(self, key, default=None, **kwargs):
        self.declared.add(key)
        return self.values.get(key, default)

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


def _config(method, enabled):
    return {
        "mid_passenger_availability": enabled,
        "braunschweig.population.method": method,
        "mode_choice": False, "sampling_rate": 1.0, "processes": 1,
        "matsim_threads": 1, "random_seed": 1234, "output_prefix": "bs_",
    }


@pytest.mark.parametrize(
    ("method", "enabled", "expected"),
    [
        ("popsim_mid", True, "org.eqasim.braunschweig.scenario.RunAdaptPassengerAvailabilityConfig"),
        ("popsim_mid", False, "org.eqasim.braunschweig.scenario.RunAdaptConfig"),
        ("popsim_open", True, "org.eqasim.braunschweig.scenario.RunAdaptConfig"),
    ],
)
def test_prepare_selects_config_adapter_at_real_java_boundary(monkeypatch, method, enabled, expected):
    context = _Context(_config(method, enabled))
    calls = []
    monkeypatch.setattr(prepare.eqasim, "run", lambda _ctx, runner, args: calls.append((runner, args)))
    monkeypatch.setattr(prepare.shutil, "copy", lambda *args: None)
    monkeypatch.setattr(prepare.os.path, "exists", lambda *args: True)
    monkeypatch.setattr(prepare.os, "remove", lambda *args: None)

    prepare.execute(context)

    adapters = [runner for runner, _ in calls if runner.startswith("org.eqasim.braunschweig.scenario.RunAdapt")]
    assert adapters == [expected]


def test_prepare_declares_passenger_gate_inputs():
    context = _Context({})
    prepare.configure(context)
    assert {"mid_passenger_availability", "braunschweig.population.method"} <= context.declared
