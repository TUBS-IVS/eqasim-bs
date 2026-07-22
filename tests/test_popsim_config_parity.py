"""Feature-parity tests for the two full popsim run configs.

The project rule is that flag-gated model features default ON in run configs so
they are not forgotten. The reference feature set is
config_local_braunschweig_25pct_allfeat.yml (validated all-features 25% run).
These tests pin the parity flags in configs/fixtures/config_popsim_mid_braunschweig.yml and
configs/fixtures/config_popsim_open_braunschweig.yml:

- education gravity (real NDS schools / Kita / Hochschule),
- household vehicle fleet (vehicles_method=household + fleet flags),
- urban parking + carless car-leg remoding,
- cross-cordon einpendler injection (cordon_enabled + the terminal MATSim
  writer wrapper aliases, without which the flag does nothing).

Legacy-IPF-only enrichment flags (status_from_hhtype, cars_income_aware, ...)
are intentionally NOT required here: the popsim path replaces the legacy
enriched stage (synthesis.population.enriched -> braunschweig.popsim.enriched_adapter),
so those keys would be dead in these configs.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

MID_CONFIG = REPO_ROOT / "configs" / "fixtures" / "config_popsim_mid_braunschweig.yml"
OPEN_CONFIG = REPO_ROOT / "configs" / "fixtures" / "config_popsim_open_braunschweig.yml"

# The cordon einpendler injection is terminal: it wraps the four MATSim scenario
# writers. Without these aliases cordon_enabled=true silently does nothing.
CORDON_WRITER_ALIASES = {
    "matsim.scenario.population": "braunschweig.matsim.scenario.population",
    "matsim.scenario.households": "braunschweig.matsim.scenario.households",
    "matsim.scenario.vehicles": "braunschweig.matsim.scenario.vehicles",
    "matsim.scenario.facilities": "braunschweig.matsim.scenario.facilities",
}


def _cfg(path):
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)["config"]


def test_popsim_mid_parity_flags_on():
    c = _cfg(MID_CONFIG)
    assert c["education_gravity_enabled"] is True
    assert c["vehicles_method"] == "household"
    assert c["fleet_model_enabled"] is True
    assert c["fleet_hsn_tsn_attributes"] is True
    assert c["enable_urban_parking"] is True
    assert c["remode_carless_car_legs"] is True
    assert c["cordon_enabled"] is True


def test_popsim_open_parity_flags_on():
    c = _cfg(OPEN_CONFIG)
    assert c["education_gravity_enabled"] is True
    assert c["vehicles_method"] == "household"
    assert c["fleet_model_enabled"] is True
    assert c["fleet_hsn_tsn_attributes"] is True
    assert c["enable_urban_parking"] is True
    assert c["remode_carless_car_legs"] is True
    assert c["cordon_enabled"] is True


def test_popsim_configs_carry_cordon_writer_aliases():
    for path in (MID_CONFIG, OPEN_CONFIG):
        with open(path, encoding="utf-8") as f:
            aliases = yaml.safe_load(f)["aliases"]
        for upstream, expected in CORDON_WRITER_ALIASES.items():
            assert aliases.get(upstream) == expected, (
                f"{path.name}: alias {upstream!r} must map to {expected!r} "
                f"(got {aliases.get(upstream)!r}); without the terminal writer "
                "wrappers cordon_enabled does nothing"
            )


def test_popsim_configs_keep_popsim_specific_wiring():
    """Parity edits must not disturb the popsim producer wiring."""
    for path in (MID_CONFIG, OPEN_CONFIG):
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        assert raw["aliases"]["data.census.filtered"] == "braunschweig.popsim.stage"
        assert raw["aliases"]["synthesis.population.enriched"] == (
            "braunschweig.popsim.enriched_adapter"
        )
    # popsim_open must still NOT alias the secondary distance distributions
    # (the default ENTD CDFs are correct for the ENTD-seeded population).
    with open(OPEN_CONFIG, encoding="utf-8") as f:
        open_aliases = yaml.safe_load(f)["aliases"]
    assert "synthesis.population.spatial.secondary.distance_distributions" not in open_aliases
