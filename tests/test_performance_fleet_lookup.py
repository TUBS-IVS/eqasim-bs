"""Performance-equivalence tests for household fleet home lookups.

The indexed lookup must produce exactly the same car frame as the original
per-household filtered-frame lookup.  The fixture also covers its meaningful
edge cases, including the historical first-row behaviour after duplicate
RegioStaR matches.
"""

from __future__ import annotations

from collections.abc import Callable

import pandas as pd
import pytest

from braunschweig.synthesis.vehicles.cars import household as household_module


def _lookup_fixture() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return deterministic households covering lookup and owner edge cases."""
    persons = pd.DataFrame.from_records([
        # Zero cars: no output row and no home lookup.
        {"household_id": 1, "person_id": 11, "age": 44, "has_license": True,
         "number_of_cars": 0, "economic_status": "low"},
        # Multiple licensed owners with an age tie: person_id breaks the tie.
        {"household_id": 2, "person_id": 22, "age": 50, "has_license": True,
         "number_of_cars": 2, "economic_status": "high"},
        {"household_id": 2, "person_id": 21, "age": 50, "has_license": True,
         "number_of_cars": 2, "economic_status": "high"},
        # Cars but no licensed adult: oldest adult fallback.
        {"household_id": 3, "person_id": 31, "age": 65, "has_license": False,
         "number_of_cars": 1, "economic_status": "medium"},
        {"household_id": 3, "person_id": 32, "age": 60, "has_license": False,
         "number_of_cars": 1, "economic_status": "medium"},
        # Cars but no adult: oldest person fallback.
        {"household_id": 4, "person_id": 42, "age": 16, "has_license": False,
         "number_of_cars": 1, "economic_status": "very_low"},
        {"household_id": 4, "person_id": 41, "age": 17, "has_license": False,
         "number_of_cars": 1, "economic_status": "very_low"},
        # Existing API accepts distinct mixed-type household identifiers.
        {"household_id": "five", "person_id": 51, "age": 30, "has_license": True,
         "number_of_cars": 1, "economic_status": "very_high"},
    ])
    homes = pd.DataFrame.from_records([
        {"household_id": 1, "commune_id": "031010000000"},
        {"household_id": 2, "commune_id": "031020000000"},
        {"household_id": 3, "commune_id": "031530000000"},
        {"household_id": 4, "commune_id": "031540000000"},
        {"household_id": "five", "commune_id": "031010000000"},
    ])
    regiostar = pd.DataFrame.from_records([
        {"commune_id": "03101000", "name": "First municipality", "regiostar7": 71},
        # Duplicate match: the old ``home.iloc[0]`` selects this row.
        {"commune_id": "03102000", "name": "First duplicate", "regiostar7": 72},
        {"commune_id": "03102000", "name": "Second duplicate", "regiostar7": 77},
        {"commune_id": "03153000", "name": "Third municipality", "regiostar7": 73},
        {"commune_id": "03154000", "name": "Fourth municipality", "regiostar7": 74},
    ])
    return persons, homes, regiostar


def test_indexed_home_lookup_is_exactly_equivalent_for_lookup_edge_cases():
    """The ON and OFF paths preserve rows, order, dtypes, and first matches."""
    persons, homes, regiostar = _lookup_fixture()

    old = household_module.build_household_car_frame(
        persons, homes, regiostar, indexed_home_lookup=False)
    new = household_module.build_household_car_frame(
        persons, homes, regiostar, indexed_home_lookup=True)

    pd.testing.assert_frame_equal(new, old, check_exact=True)
    household_two = new.loc[new["household_id"] == 2].reset_index(drop=True)
    assert household_two["owner_id"].tolist() == [21, 22]
    assert household_two["gemeinde"].tolist() == ["FIRST DUPLICATE"] * 2
    assert 1 not in set(new["household_id"])


def test_indexed_home_lookup_preserves_missing_home_error():
    """A car-owning household without a home still raises the baseline error."""
    persons, homes, regiostar = _lookup_fixture()
    homes = homes.loc[homes["household_id"] != 3]

    with pytest.raises(ValueError, match="no home commune for household 3"):
        household_module.build_household_car_frame(
            persons, homes, regiostar, indexed_home_lookup=False)
    with pytest.raises(ValueError, match="no home commune for household 3"):
        household_module.build_household_car_frame(
            persons, homes, regiostar, indexed_home_lookup=True)


def test_indexed_home_lookup_eliminates_per_household_home_filters(monkeypatch):
    """The indexed path performs no repeated ``household_id == id`` filters."""
    persons, homes, regiostar = _lookup_fixture()
    calls = 0
    original_eq: Callable = pd.Series.__eq__

    def _count_home_filters(self, other):
        nonlocal calls
        if self.name == "household_id":
            calls += 1
        return original_eq(self, other)

    monkeypatch.setattr(pd.Series, "__eq__", _count_home_filters)
    household_module.build_household_car_frame(
        persons, homes, regiostar, indexed_home_lookup=True)

    assert calls == 0
    household_module.build_household_car_frame(
        persons, homes, regiostar, indexed_home_lookup=False)
    assert calls == 4  # one filter for each car-owning household in this fixture


def test_configure_registers_default_on_fleet_home_lookup():
    """The performance flag is declared for synpp's resolved stage config."""
    registered: dict[str, object] = {}

    class _Context:
        def stage(self, *_args, **_kwargs):
            pass

        def config(self, key, default=None):
            registered[key] = default

    household_module.configure(_Context())

    assert registered["braunschweig.performance.fleet_home_lookup"] is True


def test_execute_forwards_fleet_home_lookup_to_car_frame(monkeypatch):
    """The resolved performance flag selects the car-frame lookup path."""
    persons, homes, regiostar = _lookup_fixture()
    captured: list[bool] = []

    def _build_car_frame(*_args, indexed_home_lookup, **_kwargs):
        captured.append(indexed_home_lookup)
        return pd.DataFrame.from_records([{
            "household_id": 2,
            "owner_id": 21,
            "economic_status": "high",
            "kreis_ags5": "03102",
            "gemeinde": "FIRST DUPLICATE",
            "raumtyp": 72,
            "owner_age": 50.0,
        }])

    def _sample_fleet(df_cars, *_args, **_kwargs):
        df_spec = df_cars.copy()
        df_spec["powertrain"] = "petrol"
        df_spec["euro_class"] = "euro6"
        df_spec["type_id"] = "car_type"
        return df_spec, pd.DataFrame.from_records([{
            "type_id": "car_type", "nb_seats": 4, "length": 5.0,
            "width": 1.0, "pce": 1.0, "mode": "car",
            "hbefa_cat": "PASSENGER_CAR", "hbefa_tech": "average",
            "hbefa_size": "average", "hbefa_emission": "average",
        }])

    monkeypatch.setattr(household_module, "build_household_car_frame", _build_car_frame)
    monkeypatch.setattr(household_module.fleet, "sample_fleet", _sample_fleet)

    class _Context:
        def config(self, key):
            return {
                "data_path": "unused",
                "random_seed": 42,
                "hbefa_segment_size_map": None,
                "fleet_model_enabled": True,
                "fleet_model_brands": False,
                "fleet_hsn_tsn_attributes": False,
                "fleet_consistency_v2": True,
                "fleet_age_income_coupling": True,
                "fleet_ev_income_tilt": True,
                "fleet_euro6_substage": True,
                "fleet_wohnmobile_age_tilt": True,
                "fleet_electric_calibration": "kreis_mix_gemeinde_bev_tilt",
                "kba_fleet_paths": None,
                "braunschweig.performance.fleet_home_lookup": False,
            }[key]

        def stage(self, name):
            return {
                "synthesis.population.enriched": persons,
                "synthesis.population.spatial.home.zones": homes,
                "braunschweig.data.bbsr.regiostar": regiostar,
            }[name]

    household_module.execute(_Context())

    assert captured == [False]
