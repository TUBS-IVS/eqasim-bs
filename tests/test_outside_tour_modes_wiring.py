"""The two post-location consumers read the simulated-trips alias; the chain solver keeps trips.final (#453).

Configs keep the stage flags under ``config`` and the stage aliases under the top-level ``aliases`` key.
"""
import pathlib

import yaml

from braunschweig.matsim.scenario import population as population_writer
from braunschweig.synthesis.commute_day import output_day
from braunschweig.synthesis.outside_tour_modes import config_keys as keys

REPO = pathlib.Path(__file__).resolve().parents[1]
STAGE_NAME = "braunschweig.synthesis.outside_tour_modes.stage"


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_population_writer_and_output_day_read_the_simulated_alias():
    assert population_writer.DAY_TRIPS_STAGE == keys.SIMULATED_TRIPS_ALIAS
    assert output_day.DAY_TRIPS_STAGE == keys.SIMULATED_TRIPS_ALIAS


def test_chain_solver_still_reads_trips_final():
    source = (REPO / "braunschweig" / "synthesis" / "locations" / "secondary_chainsolvers"
              / "__init__.py").read_text(encoding="utf-8")
    assert f'"{keys.FINAL_TRIPS_STAGE}"' in source
    assert keys.SIMULATED_TRIPS_ALIAS not in source


def test_production_config_aliases_the_stage_and_enables_the_flag():
    document = _load(REPO / "configs" / "base_bs.yml")
    assert document["aliases"][keys.SIMULATED_TRIPS_ALIAS] == STAGE_NAME
    assert document["config"][keys.KEY_ENABLED] is True
    keys.validate_settings(document["config"][keys.KEY_MIN_SHARE], document["config"][keys.KEY_WARN_SHARE])


def test_every_fixture_aliases_the_simulated_trips_to_its_final_target_and_disables_the_flag():
    checked = 0
    for path in sorted((REPO / "configs" / "fixtures").glob("*.yml")):
        document = _load(path)
        aliases = document.get("aliases", {})
        if keys.FINAL_TRIPS_STAGE not in aliases:
            continue
        checked += 1
        assert aliases[keys.SIMULATED_TRIPS_ALIAS] == aliases[keys.FINAL_TRIPS_STAGE], path.name
        assert document["config"][keys.KEY_ENABLED] is False, path.name
    assert checked > 0
