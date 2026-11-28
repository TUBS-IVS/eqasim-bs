"""Every config that runs the PopulationSim stage passes the start-of-stage consistency refusals.

The code defaults of the student entries (ADR-0211: "on") and of ``kreis_batch_apportionment``
("population") refuse each other, so a config that sets neither key fails at the start of
``execute()``. This test resolves each such config the way ``execute()`` does (the real
``configure()`` declares the defaults, a one-argument ``config`` reads the resolved value) and runs
``active_kreis_entries`` and ``KreisConsistencySettings.require_run_support`` on it.
"""
from pathlib import Path

import pytest
import yaml

from braunschweig.config_compose import compose
from braunschweig.popsim import stage as popsim_stage
from braunschweig.popsim.kreis_attribute_control import STUDENT_ENTRY_NAMES
from braunschweig.popsim.stage import source_resolution

REPO_ROOT = Path(__file__).resolve().parents[1]
BASE = REPO_ROOT / "configs" / "base_bs.yml"


class _ConfigureContext:
    """The configure phase: ``config(key, default)`` declares a key and records its default."""

    def __init__(self, values):
        self.values = values
        self.resolved = {}

    def config(self, key, default=None, **_kwargs):
        self.resolved[key] = self.values[key] if key in self.values else default
        return self.resolved[key]

    def __getattr__(self, name):  # stage(), parameter() ... of the configure phase: no-ops
        return lambda *args, **kwargs: None


class _ExecuteContext:
    """The execute phase: ``config(key)`` takes ONE argument and only reads a declared key."""

    def __init__(self, resolved):
        self._resolved = resolved

    def config(self, key):
        return self._resolved[key]  # KeyError for a key configure() did not declare


def _popsim_configs():
    """(id, config values) of every config under configs/ whose population method is a popsim one."""
    composed = [(f"base+{p.name}", compose(str(BASE), str(p)))
                 for p in sorted((REPO_ROOT / "configs" / "overlays").glob("*.yml"))]
    for p in sorted((REPO_ROOT / "configs" / "fixtures").glob("*.yml")):
        with open(p, encoding="utf-8") as handle:
            composed.append((p.name, yaml.safe_load(handle)))
    return [(name, cfg["config"]) for name, cfg in composed
            if str(cfg.get("config", {}).get("braunschweig.population.method", "")).startswith("popsim")]


CONFIGS = _popsim_configs()


def test_popsim_configs_are_found():
    names = [name for name, _ in CONFIGS]
    for expected in ("base+test_100pct.yml", "config_popsim_mid_braunschweig.yml", "config_smoke_popsim_mid_mini.yml"):
        assert expected in names


@pytest.mark.parametrize("name,values", CONFIGS, ids=[name for name, _ in CONFIGS])
def test_config_passes_start_of_stage_refusals(name, values):
    declared = _ConfigureContext(values)
    popsim_stage.configure(declared)
    context = _ExecuteContext(declared.resolved)
    source_name = str(context.config(popsim_stage.KEY_SOURCE))
    active = source_resolution.active_kreis_entries(context, source_name)
    consistency = popsim_stage._read_kreis_consistency_config(context)
    consistency.require_run_support(
        has_kreis_table=bool(active),
        employment_grid_on=bool(context.config(popsim_stage.KEY_EMPLOYMENT_GRID)),
        student_entry_names=[e.name for e in active if e.name in STUDENT_ENTRY_NAMES])
