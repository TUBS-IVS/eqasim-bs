"""Tests for config_popsim_open_braunschweig.yml (Phase 3).

Verifies that the popsim_open config file:
- Parses without YAML errors.
- Sets the correct population.method and source values.
- Has hts: entd.
- Does NOT alias synthesis.population.spatial.secondary.distance_distributions
  (the default ENTD-based CDFs are correct for popsim_open).
- Aliasing contracts for the four mandatory popsim aliases are present.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "config_popsim_open_braunschweig.yml"


def _load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------

def test_config_file_exists():
    """config_popsim_open_braunschweig.yml must exist in the repo root."""
    assert CONFIG_PATH.is_file(), (
        f"config_popsim_open_braunschweig.yml not found at {CONFIG_PATH}"
    )


# ---------------------------------------------------------------------------
# YAML parse + top-level structure
# ---------------------------------------------------------------------------

def test_config_parses_as_valid_yaml():
    """The config must parse cleanly as YAML (no syntax errors)."""
    cfg = _load_config()
    assert isinstance(cfg, dict), "Top-level YAML must be a mapping"


def test_config_has_required_top_level_keys():
    """Config must have working_directory, run, config, and aliases sections."""
    cfg = _load_config()
    for key in ("working_directory", "run", "config", "aliases"):
        assert key in cfg, f"Top-level key {key!r} missing from popsim_open config"


# ---------------------------------------------------------------------------
# config block: population.method and source
# ---------------------------------------------------------------------------

def test_config_method_is_popsim_open():
    """braunschweig.population.method must be 'popsim_open'."""
    cfg = _load_config()
    method = cfg["config"].get("braunschweig.population.method")
    assert method == "popsim_open", (
        f"Expected method='popsim_open', got {method!r}"
    )


def test_config_source_is_entd():
    """braunschweig.population.popsim.source must be 'entd'."""
    cfg = _load_config()
    source = cfg["config"].get("braunschweig.population.popsim.source")
    assert source == "entd", (
        f"Expected source='entd', got {source!r}"
    )


def test_config_hts_is_entd():
    """hts must be 'entd' so data.hts.selected resolves to data.hts.entd.reweighted."""
    cfg = _load_config()
    hts = cfg["config"].get("hts")
    assert hts == "entd", (
        f"Expected hts='entd', got {hts!r}"
    )


# ---------------------------------------------------------------------------
# aliases block: distance_distributions alias MUST be absent
# ---------------------------------------------------------------------------

def test_config_does_not_alias_secondary_distance_distributions():
    """The secondary distance_distributions alias must be ABSENT for popsim_open.

    For popsim_mid this alias points to braunschweig.popsim.distance_distributions
    (MiD-derived CDFs).  For popsim_open the DEFAULT eqasim stage (which uses ENTD
    trips) is correct; overriding it with MiD CDFs would be a bug.
    """
    cfg = _load_config()
    aliases = cfg.get("aliases", {})
    dist_alias = "synthesis.population.spatial.secondary.distance_distributions"
    assert dist_alias not in aliases, (
        f"popsim_open config must NOT alias '{dist_alias}' "
        "(the ENTD default is correct for popsim_open; MiD CDFs would be wrong)"
    )


# ---------------------------------------------------------------------------
# aliases block: mandatory popsim aliases must be present
# ---------------------------------------------------------------------------

_REQUIRED_POPSIM_ALIASES = {
    "data.census.filtered": "braunschweig.popsim.stage",
    "synthesis.population.trips": "braunschweig.popsim.trips_stage",
    "synthesis.population.enriched": "braunschweig.popsim.enriched_adapter",
    "synthesis.population.spatial.commute_distance": "braunschweig.popsim.commute_distance",
}


@pytest.mark.parametrize("upstream,expected", _REQUIRED_POPSIM_ALIASES.items())
def test_required_popsim_aliases_present(upstream, expected):
    """Each mandatory popsim alias must be present and map to the expected target."""
    cfg = _load_config()
    aliases = cfg.get("aliases", {})
    actual = aliases.get(upstream)
    assert actual == expected, (
        f"popsim_open config: alias '{upstream}' = {actual!r}, expected {expected!r}"
    )


# ---------------------------------------------------------------------------
# aliases block: data.census.filtered targets braunschweig.popsim.stage
# ---------------------------------------------------------------------------

def test_data_census_filtered_points_to_popsim_stage():
    """data.census.filtered must alias to braunschweig.popsim.stage (not the IPF chain)."""
    cfg = _load_config()
    alias = cfg["aliases"].get("data.census.filtered")
    assert alias == "braunschweig.popsim.stage", (
        f"data.census.filtered must alias braunschweig.popsim.stage for popsim_open, "
        f"got {alias!r}"
    )
