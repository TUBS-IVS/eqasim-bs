"""Baseline guard for the existing IPF population workflow (``simple_ipf_open``).

Context
-------
The population-generation refactor (``docs/codebase`` cross-repo addendum;
memory ``project-popsim-three-workflows``) introduces a config switch
``population.method`` with three values:

    simple_ipf_open  -> the EXISTING in-house IPF chain (this repo, today)
    popsim_open      -> PopulationSim, open seed (ENTD)
    popsim_mid       -> PopulationSim, restricted MiD seed

Before that switch is added, this test freezes the *wiring* that defines
``simple_ipf_open`` so the switch provably preserves it. Specifically it pins:

1. the alias contract that routes the population producer to the in-house IPF
   formation stage (``data.census.filtered -> braunschweig.ipf.attributed``),
   and the enrichment alias, across every committed run config; and
2. the synpp stage contract (``configure``/``execute``) of the three modules
   that make up the IPF formation chain
   (``braunschweig.ipf.prepare`` / ``.model`` / ``.attributed``).

Byte-for-byte freeze of the produced CSVs is covered separately by the opt-in
``tests/test_smoke_1pct.py`` in strict mode (``EQASIM_BS_STRICT_SMOKE=1``),
which hashes the population output against ``tests/baselines/smoke_1pct_baseline.txt``.
This module is the fast, always-on companion: it needs no input data and no
pipeline run, so it stays green on a clean checkout and runs in CI.

When the ``population.method`` switch lands, ``simple_ipf_open`` must continue to
resolve to exactly the wiring asserted here; update this test only together with
a deliberate, documented behaviour change.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

# The run configs that drive the synthesis pipeline. Each must route the
# population producer to the in-house IPF chain (= the simple_ipf_open baseline).
RUN_CONFIGS = [
    "configs/fixtures/config_local_braunschweig.yml",
    "configs/fixtures/config_local_braunschweig_10pct.yml",
    "configs/fixtures/config_local_braunschweig_25pct.yml",
    "configs/fixtures/config_dryrun_braunschweig.yml",
]

# The alias contract that DEFINES simple_ipf_open: the upstream eqasim stage
# name -> the Braunschweig fork module that produces it today.
SIMPLE_IPF_OPEN_ALIASES = {
    "data.census.filtered": "braunschweig.ipf.attributed",
    "synthesis.population.enriched": "braunschweig.synthesis.population.enriched",
}

# The three modules that form the IPF population chain. Every synpp stage must
# expose configure(context) and execute(context).
IPF_CHAIN_MODULES = [
    "braunschweig.ipf.prepare",
    "braunschweig.ipf.model",
    "braunschweig.ipf.attributed",
]


def _load_config(name: str) -> dict:
    path = REPO_ROOT / name
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _existing_run_configs() -> list[str]:
    return [name for name in RUN_CONFIGS if (REPO_ROOT / name).is_file()]


# ---------------------------------------------------------------------------
# 1) Alias contract: the producer wiring that defines simple_ipf_open
# ---------------------------------------------------------------------------

def test_run_configs_present():
    """At least the canonical 1% and dryrun configs must exist; the refactor
    must not silently drop the configs that drive simple_ipf_open."""
    found = _existing_run_configs()
    assert "configs/fixtures/config_local_braunschweig.yml" in found
    assert "configs/fixtures/config_dryrun_braunschweig.yml" in found


@pytest.mark.parametrize("config_name", _existing_run_configs())
def test_population_producer_alias_is_in_house_ipf(config_name):
    """Every run config routes the population producer to the in-house IPF
    chain. This alias IS the simple_ipf_open baseline; the population.method
    switch must preserve it for simple_ipf_open."""
    aliases = _load_config(config_name).get("aliases", {})
    for upstream, expected in SIMPLE_IPF_OPEN_ALIASES.items():
        assert aliases.get(upstream) == expected, (
            f"{config_name}: alias '{upstream}' = {aliases.get(upstream)!r}, "
            f"expected {expected!r} (simple_ipf_open producer wiring)."
        )


def test_population_method_switch_not_yet_present():
    """Documents the pre-refactor baseline: no population.method key exists yet.

    This guards against an accidental half-wired switch landing without the
    accompanying producer implementations + tests. Delete/expand this assertion
    in the same change that deliberately introduces population.method.
    """
    for config_name in _existing_run_configs():
        config = _load_config(config_name).get("config", {})
        assert "population.method" not in config and "population_method" not in config, (
            f"{config_name}: a population.method key appeared; introduce the "
            "switch together with its producers and update this baseline test."
        )


# ---------------------------------------------------------------------------
# 2) synpp stage contract of the IPF formation chain
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("module_name", IPF_CHAIN_MODULES)
def test_ipf_chain_modules_expose_stage_contract(module_name):
    """Each IPF-chain stage must remain a valid synpp stage (configure/execute).
    The simple_ipf_open producer is exactly this chain."""
    module = importlib.import_module(module_name)
    for hook in ("configure", "execute"):
        assert hasattr(module, hook), f"{module_name} is missing {hook}(context)"
        assert callable(getattr(module, hook)), f"{module_name}.{hook} is not callable"
