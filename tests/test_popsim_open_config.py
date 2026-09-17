"""Tests for configs/fixtures/config_popsim_open_braunschweig.yml (Phase 3).

Verifies that the popsim_open config file:
- Parses without YAML errors.
- Sets the correct population.method and source values.
- Has hts: entd.
- Does NOT alias synthesis.population.spatial.secondary.distance_distributions
  (the default ENTD-based CDFs are correct for popsim_open).
- Aliasing contracts for the four mandatory popsim aliases are present.
- Every popsim_open fixture (not just this file) sets every MiD-only key
  EntdSource.build_trips rejects on a non-default value to its safe value
  (issue #373 fix round 1).
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from braunschweig.popsim.stage.config_keys import ENTD_REJECTED_KEYS

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "configs" / "fixtures" / "config_popsim_open_braunschweig.yml"


def _load_config() -> dict:
    with CONFIG_PATH.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


# ---------------------------------------------------------------------------
# File existence
# ---------------------------------------------------------------------------

def test_config_file_exists():
    """configs/fixtures/config_popsim_open_braunschweig.yml must exist."""
    assert CONFIG_PATH.is_file(), (
        f"configs/fixtures/config_popsim_open_braunschweig.yml not found at {CONFIG_PATH}"
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


# ---------------------------------------------------------------------------
# Config-parity guard: every popsim_open fixture must set every MiD-only key
# EntdSource.build_trips rejects on a non-default value to its safe value
# (issue #373 fix round 1: two popsim_open fixtures silently missed the
# newly-added w_zweck_10_as_leisure override -- it defaults to True and
# EntdSource.build_trips raises on True, so both configurations aborted inside
# braunschweig.popsim.trips_stage AFTER the full PopulationSim balancing).
#
# ENTD_REJECTED_KEYS (braunschweig.popsim.stage.config_keys) is the SAME dict
# EntdSource.build_trips' rejection checks read, so a newly REJECTED keyword
# (e.g. issue #373 task 4's two passive-escort keywords) is enforced here
# automatically without editing this test.
# ---------------------------------------------------------------------------

_FIXTURES_DIR = REPO_ROOT / "configs" / "fixtures"


def _popsim_open_fixture_paths() -> list[Path]:
    """Every configs/fixtures/*.yml whose braunschweig.population.method is popsim_open."""
    paths = []
    for path in sorted(_FIXTURES_DIR.glob("*.yml")):
        with path.open(encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh)
        if not isinstance(cfg, dict):
            continue
        if cfg.get("config", {}).get("braunschweig.population.method") == "popsim_open":
            paths.append(path)
    return paths


_POPSIM_OPEN_FIXTURES = _popsim_open_fixture_paths()


def test_popsim_open_fixture_discovery_finds_the_known_fixtures():
    """Sanity check on the glob-and-filter discovery above: if it found ZERO
    fixtures, the parametrized parity guard below would be vacuously green."""
    names = {path.name for path in _POPSIM_OPEN_FIXTURES}
    assert {
        "config_popsim_open_braunschweig.yml", "config_smoke_popsim_open_mini.yml",
    } <= names, (
        f"expected the two known popsim_open fixtures among the discovered ones, "
        f"found {sorted(names)}"
    )


@pytest.mark.parametrize(
    "fixture_path", _POPSIM_OPEN_FIXTURES, ids=lambda path: path.name,
)
def test_popsim_open_fixture_sets_every_entd_rejected_key_to_its_safe_value(fixture_path):
    """Every MiD-only key EntdSource.build_trips rejects on a non-default value must
    be set to its safe value in every popsim_open (ENTD-source) fixture."""
    with fixture_path.open(encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh)["config"]
    for key, safe_value in ENTD_REJECTED_KEYS.items():
        assert cfg.get(key) == safe_value, (
            f"{fixture_path.name}: config key {key!r} must be set to {safe_value!r} "
            f"(got {cfg.get(key)!r}) -- this is a popsim_open (ENTD source) fixture and "
            "EntdSource.build_trips rejects any other value."
        )
