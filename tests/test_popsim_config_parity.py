"""Feature-parity tests for the two full popsim run configs.

The project rule is that flag-gated model features default ON in run configs so
they are not forgotten. The reference feature set is
config_local_braunschweig_25pct_allfeat.yml (validated all-features 25% run).
These tests pin the parity flags in configs/fixtures/config_popsim_mid_braunschweig.yml and
configs/fixtures/config_popsim_open_braunschweig.yml:

- education gravity (real NDS schools / Kita / Hochschule),
- household vehicle fleet (vehicles_method=household + fleet flags),
- the zone-based parking costs instead of the legacy 8 km ring (ADR-0139), with
  the parking block of configs/base_bs.yml, + carless car-leg remoding,
- cross-cordon einpendler injection (cordon_enabled + the terminal MATSim
  writer wrapper aliases, without which the flag does nothing).

Legacy-IPF-only enrichment flags (status_from_hhtype, cars_income_aware, ...)
are intentionally NOT required here: the popsim path replaces the legacy
enriched stage (synthesis.population.enriched -> braunschweig.popsim.enriched_adapter),
so those keys would be dead in these configs.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]

BASE_CONFIG = REPO_ROOT / "configs" / "base_bs.yml"
MID_CONFIG = REPO_ROOT / "configs" / "fixtures" / "config_popsim_mid_braunschweig.yml"
OPEN_CONFIG = REPO_ROOT / "configs" / "fixtures" / "config_popsim_open_braunschweig.yml"

#: The zone-parking flag; pinned by the parity tests, so the block comparison below leaves it out.
PARKING_ZONES_FLAG = "parking_zones_enabled"

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
    # Zone-based parking costs replace the legacy 8 km ring, as in configs/base_bs.yml (ADR-0139);
    # the two flags are mutually exclusive.
    assert c[PARKING_ZONES_FLAG] is True
    assert c["enable_urban_parking"] is False
    assert c["remode_carless_car_legs"] is True
    assert c["cordon_enabled"] is True


def test_popsim_open_parity_flags_on():
    c = _cfg(OPEN_CONFIG)
    assert c["education_gravity_enabled"] is True
    assert c["vehicles_method"] == "household"
    assert c["fleet_model_enabled"] is True
    assert c["fleet_hsn_tsn_attributes"] is True
    assert c[PARKING_ZONES_FLAG] is True
    assert c["enable_urban_parking"] is False
    assert c["remode_carless_car_legs"] is True
    assert c["cordon_enabled"] is True


@pytest.mark.parametrize("path", [MID_CONFIG, OPEN_CONFIG], ids=["popsim_mid", "popsim_open"])
def test_popsim_configs_carry_the_parking_block_of_the_base(path):
    """The zone release and its parameters are the canonical ones of configs/base_bs.yml. A stale
    copy here would price these runs with another release, or name and date their tariff model
    with a wrong parking_tariff_snapshot_date, which nothing cross-checks against the tariff
    table."""
    base = _cfg(BASE_CONFIG)
    parameter_keys = sorted(key for key in base
                            if key.startswith("parking_") and key != PARKING_ZONES_FLAG)
    assert "parking_tariffs_path" in parameter_keys  # guard: the scan found the base block
    fixture = _cfg(path)
    assert {key: fixture.get(key) for key in parameter_keys} == {
        key: base[key] for key in parameter_keys}


def test_popsim_mid_day_absence_disabled():
    """General day absence (issue #370) must stay OFF here, final-review fix wave Important
    finding 1: matsim.scenario.population is aliased to the braunschweig wrapper (cordon_enabled
    True, see CORDON_WRITER_ALIASES below), which merges a dayAbsenceState attribute
    independently of synthesis.population.trips.final -- aliased below to the bare
    braunschweig.popsim.trips_stage, not day-absence-aware -- so leaving the flag at its true
    default would write an absence attribute onto persons whose trips are never removed."""
    c = _cfg(MID_CONFIG)
    assert c["day_absence_enabled"] is False


def test_popsim_open_day_absence_disabled():
    """Same flag, same OFF requirement (ruling R13): config_popsim_open_braunschweig.yml also
    aliases matsim.scenario.population to the braunschweig wrapper."""
    c = _cfg(OPEN_CONFIG)
    assert c["day_absence_enabled"] is False


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
