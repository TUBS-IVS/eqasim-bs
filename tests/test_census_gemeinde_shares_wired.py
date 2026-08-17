"""Guard: every run config on the IPF population path must take its Gemeinde
population shares from the OPEN Zensus 2022 table, not from the scraped
urbistat table.

Background
----------
``braunschweig.data.census.population`` builds the Gemeinde x sex x age margin
as ``DESTATIS_Kreis_total * Gemeinde_share``. The Kreis total is always the
official DESTATIS 12411-0018 figure; only the *share* -- the spatial key inside
a Kreis -- has two implementations:

* ``urbistat_age_gemeinden.csv`` -- scraped, non-redistributable, coarse age
  bands (DESTATIS class 10 = ages 10-14 is smeared into the 12-17 band), and
  matched to VG250 by fuzzy Gemeinde NAME;
* Zensus 2022 ``1000A-3082`` -- open (dl-de/by-2-0), authoritative 12-digit ARS
  so no name matching at all, band edges aligned with the DESTATIS classes.

The Zensus implementation and its unit tests landed in 2026-06
(``tests/test_population_zensus_shares.py``) but the switch
``braunschweig.census.use_zensus_gemeinde_shares`` defaults to ``False`` and was
never set in any run config -- the "built but never activated" failure mode. This
test closes that gap: it fails if a config that actually executes the stage falls
back to the scraped source.

Scope
-----
Only configs whose population producer is the IPF chain execute
``braunschweig.data.census.population`` -- it appears in the ``simple_ipf_open``
DAG snapshot and in neither ``production`` nor ``popsim_open``. Under the popsim
methods ``data.census.filtered`` is aliased to ``braunschweig.popsim.stage``, so
the flag is inert there and is deliberately not required (setting it would add a
dead key, the very thing issue #251 was about).
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURES = REPO_ROOT / "configs" / "fixtures"

SHARES_KEY = "braunschweig.census.use_zensus_gemeinde_shares"
METHOD_KEY = "braunschweig.population.method"
FILTERED_ALIAS_KEY = "data.census.filtered"

# The alias value that marks the IPF population chain as the active producer.
IPF_PRODUCER = "braunschweig.ipf.attributed"
# Producer of both popsim workflows; the population stage is off their DAG.
POPSIM_PRODUCER = "braunschweig.popsim.stage"


def _run_configs() -> list[Path]:
    """Every committed run config.

    Deliberately scans ``configs/fixtures/`` only. Root-level
    ``config_local_*.yml`` files are gitignored local experiment configs
    (.gitignore), so globbing the repository root made this guard's coverage
    depend on the machine it ran on -- passing vacuously on a clean checkout.
    """
    return sorted(FIXTURES.glob("*.yml"))


def _load(path: Path) -> dict:
    with path.open(encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _uses_ipf_population(document: dict) -> bool:
    """True when this config's population producer is the IPF chain.

    Decided by the ``data.census.filtered`` alias, which is what actually wires
    the producer into the DAG, rather than by ``population.method`` alone (the
    method key is optional and defaults to the IPF path).
    """
    aliases = document.get("aliases") or {}
    return aliases.get(FILTERED_ALIAS_KEY) == IPF_PRODUCER


IPF_CONFIGS = [p for p in _run_configs() if _uses_ipf_population(_load(p))]
POPSIM_CONFIGS = [p for p in _run_configs() if not _uses_ipf_population(_load(p))]


def test_at_least_one_ipf_config_is_discovered() -> None:
    """Guard the guard: an empty parametrisation would pass vacuously."""
    assert IPF_CONFIGS, (
        "No config with the IPF population producer was discovered -- the "
        "discovery rule in _uses_ipf_population is stale."
    )


@pytest.mark.parametrize("config_path", IPF_CONFIGS, ids=lambda p: p.name)
def test_ipf_config_uses_open_zensus_gemeinde_shares(config_path: Path) -> None:
    """A config that runs the stage must not fall back to the scraped source."""
    config = _load(config_path).get("config") or {}
    assert config.get(SHARES_KEY) is True, (
        f"{config_path.name} runs braunschweig.data.census.population but does "
        f"not set {SHARES_KEY}: true. Without it the Gemeinde shares come from "
        f"the scraped, non-redistributable urbistat table instead of the open "
        f"Zensus 2022 table 1000A-3082."
    )


@pytest.mark.parametrize("config_path", POPSIM_CONFIGS, ids=lambda p: p.name)
def test_popsim_config_does_not_set_the_inert_flag(config_path: Path) -> None:
    """Under the popsim methods the stage is off the DAG, so the flag stays out.

    Asserting the absence keeps the scope of the guard above explicit: if a
    popsim config ever switches back to the IPF producer, the discovery rule
    moves it into the parametrisation above instead of silently exempting it.
    """
    document = _load(config_path)
    aliases = document.get("aliases") or {}
    assert aliases.get(FILTERED_ALIAS_KEY) == POPSIM_PRODUCER, (
        f"{config_path.name} is neither on the IPF producer nor on the popsim "
        f"producer; classify it explicitly before this guard can reason about it."
    )
    config = document.get("config") or {}
    assert config.get(METHOD_KEY) in ("popsim_mid", "popsim_open"), (
        f"{config_path.name} uses the popsim producer but declares "
        f"{METHOD_KEY}={config.get(METHOD_KEY)!r}."
    )
    assert SHARES_KEY not in config, (
        f"{config_path.name} sets {SHARES_KEY}, but braunschweig.data.census."
        f"population is not on the popsim DAG -- that would be a dead key."
    )
