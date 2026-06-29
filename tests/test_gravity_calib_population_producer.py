"""Unit tests for gravity-calibration population-producer resolution.

The gravity model (``braunschweig/gravity/model.py``) reads the synpp alias
``data.census.filtered``, which each run config redirects to the active
population producer: ``braunschweig.ipf.attributed`` for the IPF workflow and
``braunschweig.popsim.stage`` for the popsim_mid / popsim_open workflows. The
calibration script must measure the SAME population the model + demand use, so
it resolves that alias from the config's top-level ``aliases`` block rather than
hard-coding a single producer stage (the previous bug: it always loaded the IPF
producer ``braunschweig.data.census.population``, which the popsim_mid caches do
not contain).
"""
import importlib.util
import pathlib

import pytest

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
_SCRIPT_PATH = _REPO_ROOT / "scripts" / "calibrate_gravity_distribution.py"


def _load_script_module():
    spec = importlib.util.spec_from_file_location(
        "calibrate_gravity_distribution", _SCRIPT_PATH
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


calib = _load_script_module()


def test_resolves_popsim_producer_from_aliases():
    aliases = {"data.census.filtered": "braunschweig.popsim.stage"}
    assert calib.resolve_population_producer(aliases) == "braunschweig.popsim.stage"


def test_resolves_ipf_producer_from_aliases():
    aliases = {"data.census.filtered": "braunschweig.ipf.attributed"}
    assert calib.resolve_population_producer(aliases) == "braunschweig.ipf.attributed"


def test_falls_back_to_legacy_producer_when_no_alias():
    # No alias configured -> keep the legacy IPF census producer (with a warning,
    # emitted by the resolver) so an alias-less config still resolves to a stage.
    assert calib.resolve_population_producer({}) == calib.DEFAULT_POPULATION_PRODUCER
    assert calib.resolve_population_producer(None) == calib.DEFAULT_POPULATION_PRODUCER


def test_script_does_not_hardcode_census_population_load():
    # Regression guard: the population producer must be resolved via the alias,
    # never hard-coded to the IPF census stage (which popsim_mid caches lack).
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert '_load_stage(wd, "braunschweig.data.census.population")' not in src


def test_load_aliases_resolves_popsim_mid_config_to_popsim_stage():
    # End-to-end: the committed popsim_mid config must resolve to popsim.stage.
    pytest.importorskip("yaml")
    cfg_path = _REPO_ROOT / "config_popsim_mid_braunschweig.yml"
    aliases = calib._load_aliases(str(cfg_path))
    assert calib.resolve_population_producer(aliases) == "braunschweig.popsim.stage"
