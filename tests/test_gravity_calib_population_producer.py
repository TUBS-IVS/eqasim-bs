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


def test_resolve_stage_returns_configured_producer():
    aliases = {"synthesis.population.enriched": "braunschweig.popsim.enriched_adapter"}
    assert (
        calib.resolve_stage(aliases, "synthesis.population.enriched", "fallback")
        == "braunschweig.popsim.enriched_adapter"
    )


def test_resolve_stage_falls_back_when_key_absent():
    assert calib.resolve_stage({}, "synthesis.population.enriched", "fallback") == "fallback"
    assert calib.resolve_stage(None, "x", "fallback") == "fallback"


def test_script_does_not_hardcode_popsim_incompatible_stage_loads():
    # Regression guard: the three workflow-dependent stages must be resolved via
    # aliases (variable args), never hard-coded to the IPF/eqasim stage names that
    # popsim_mid caches do not contain.
    src = _SCRIPT_PATH.read_text(encoding="utf-8")
    assert '_load_stage(wd, "braunschweig.data.census.population")' not in src
    assert '_load_stage(wd, "braunschweig.synthesis.population.enriched")' not in src
    assert '_load_stage(wd, "synthesis.population.spatial.home.locations")' not in src
    # The resolved-producer variables must be the load arguments instead.
    assert "_load_stage(wd, population_producer)" in src
    assert "_load_stage(wd, enriched_producer)" in src
    assert "_load_stage(wd, home_locations_producer)" in src


def test_popsim_mid_config_resolves_all_three_producers():
    # End-to-end: the committed popsim_mid config must resolve every
    # workflow-dependent stage to its popsim producer.
    pytest.importorskip("yaml")
    cfg_path = _REPO_ROOT / "config_popsim_mid_braunschweig.yml"
    aliases = calib._load_aliases(str(cfg_path))
    assert calib.resolve_population_producer(aliases) == "braunschweig.popsim.stage"
    assert (
        calib.resolve_stage(aliases, calib.ENRICHED_ALIAS_KEY, "x")
        == "braunschweig.popsim.enriched_adapter"
    )
    assert (
        calib.resolve_stage(aliases, calib.HOME_LOCATIONS_ALIAS_KEY, "x")
        == "braunschweig.synthesis.locations.home_cell"
    )
