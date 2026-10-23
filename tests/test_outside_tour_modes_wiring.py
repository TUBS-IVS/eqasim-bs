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
ACTIVITIES_STAGE_NAME = "braunschweig.synthesis.outside_tour_modes.activities_stage"


def _load(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def test_population_writer_and_output_day_read_the_simulated_aliases():
    # The activities follow the simulated trips, whose times the pt check of ADR-0159 may move.
    assert population_writer.DAY_TRIPS_STAGE == keys.SIMULATED_TRIPS_ALIAS
    assert output_day.DAY_TRIPS_STAGE == keys.SIMULATED_TRIPS_ALIAS
    assert population_writer.DAY_ACTIVITIES_STAGE == keys.SIMULATED_ACTIVITIES_ALIAS
    assert output_day.DAY_ACTIVITIES_STAGE == keys.SIMULATED_ACTIVITIES_ALIAS


def test_the_simulated_activities_follow_the_shifted_trip_times():
    import pandas as pd

    from braunschweig.synthesis.outside_tour_modes import activities_stage

    trips = pd.DataFrame({"person_id": [1, 1], "trip_index": [0, 1], "preceding_purpose": ["home", "leisure"],
                          "following_purpose": ["leisure", "home"], "departure_time": [17 * 3600.0, 21 * 3600.0],
                          "arrival_time": [17.5 * 3600.0, 21.5 * 3600.0], "is_first_trip": [True, False],
                          "is_last_trip": [False, True], "mode": ["pt", "pt"]})
    persons = pd.DataFrame({"person_id": [1, 2]})

    class _Context:
        def stage(self, name):
            return {keys.SIMULATED_TRIPS_ALIAS: trips, keys.PERSONS_STAGE: persons}[name]

    activities = activities_stage.execute(_Context())
    leisure = activities[(activities["person_id"] == 1) & (activities["purpose"] == "leisure")].iloc[0]
    assert (leisure["start_time"], leisure["end_time"]) == (17.5 * 3600.0, 21 * 3600.0)
    assert set(activities["person_id"]) == {1, 2}            # a person without trips keeps a home activity
    assert list(trips.columns) == ["person_id", "trip_index", "preceding_purpose", "following_purpose",
                                   "departure_time", "arrival_time", "is_first_trip", "is_last_trip", "mode"]


def test_chain_solver_still_reads_trips_final():
    source = (REPO / "braunschweig" / "synthesis" / "locations" / "secondary_chainsolvers"
              / "__init__.py").read_text(encoding="utf-8")
    assert f'"{keys.FINAL_TRIPS_STAGE}"' in source
    assert keys.SIMULATED_TRIPS_ALIAS not in source


def test_without_a_moved_tour_the_simulated_activities_equal_the_final_activities():
    # ADR-0159 decision 5: with the outside-tour layer off (or no tour moved) the population writer must see the
    # same activities as before the alias switch.
    import pandas as pd

    from braunschweig.synthesis.commute_day import activities_day_stage
    from braunschweig.synthesis.outside_tour_modes import activities_stage

    trips = pd.DataFrame({"person_id": [1, 1, 3], "trip_index": [0, 1, 0],
                          "preceding_purpose": ["home", "work", "home"], "following_purpose": ["work", "home", "shop"],
                          "departure_time": [8 * 3600.0, 17 * 3600.0, 9 * 3600.0],
                          "arrival_time": [8.5 * 3600.0, 17.5 * 3600.0, 9.2 * 3600.0],
                          "is_first_trip": [True, False, True], "is_last_trip": [False, True, True],
                          "mode": ["car", "car", "walk"]})
    persons = pd.DataFrame({"person_id": [1, 2, 3]})

    class _Context:
        def stage(self, name):
            return {keys.SIMULATED_TRIPS_ALIAS: trips, keys.FINAL_TRIPS_STAGE: trips, keys.PERSONS_STAGE: persons}[name]

    pd.testing.assert_frame_equal(activities_stage.execute(_Context()), activities_day_stage.execute(_Context()))


def test_production_config_aliases_the_stage_and_enables_the_flag():
    document = _load(REPO / "configs" / "base_bs.yml")
    assert document["aliases"][keys.SIMULATED_TRIPS_ALIAS] == STAGE_NAME
    assert document["aliases"][keys.SIMULATED_ACTIVITIES_ALIAS] == ACTIVITIES_STAGE_NAME
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
        assert aliases[keys.SIMULATED_ACTIVITIES_ALIAS] == aliases[keys.FINAL_ACTIVITIES_STAGE], path.name
        assert document["config"][keys.KEY_ENABLED] is False, path.name
    assert checked > 0
