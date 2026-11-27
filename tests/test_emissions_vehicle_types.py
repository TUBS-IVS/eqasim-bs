"""Vehicle type -> HBEFA key resolution for the post-run emissions view (#688).

One contract: every vehicle type of a run archive resolves to an explicit action, an unknown
type or technology aborts instead of silently falling back, and the confirmed owner
assumptions (public transport and ``default_car_passenger`` emit nothing, ``heavy_truck`` is the
HGV average) are applied.
"""
from __future__ import annotations

import pytest

from braunschweig.emissions.vehicle_types import (
    NON_HBEFA_VEHICLE,
    resolve_vehicle_type,
)


def _fleet_attributes(technology: str, concept: str = "PC petrol Euro-6d") -> dict[str, str]:
    return {
        "HbefaVehicleCategory": "PASSENGER_CAR",
        "HbefaTechnology": technology,
        "HbefaSizeClass": "medium",
        "HbefaEmissionsConcept": concept,
    }


@pytest.mark.parametrize(
    "type_id", ["default_car_passenger", "Bus", "Ferry", "Funicular", "Rail", "Subway", "Tram"]
)
def test_assumed_zero_emitters_are_excluded(type_id):
    resolution = resolve_vehicle_type(type_id, {})
    assert resolution.excluded is True
    assert resolution.keys.category == NON_HBEFA_VEHICLE


def test_heavy_truck_is_the_hgv_average_and_default_car_is_kept():
    truck = resolve_vehicle_type("heavy_truck", {})
    assert truck.excluded is False
    assert (truck.keys.category, truck.keys.technology) == ("HEAVY_GOODS_VEHICLE", "average")
    assert truck.keys.size_class == truck.keys.emissions_concept == "average"

    default_car = resolve_vehicle_type("default_car", _fleet_attributes("average", "average"))
    assert default_car.excluded is False
    assert default_car.keys.category == "PASSENGER_CAR"
    assert default_car.keys.technology == "average"


@pytest.mark.parametrize(
    "archive_name, contrib_id",
    [
        ("petrol (4S)", "petrol (4S)"),
        ("diesel", "diesel"),
        ("electricity", "electricity"),
        ("bifuel CNG/petrol", "bifuel CNG/petrol"),
        ("plug-in hybrid petrol/electricity", "Plug-in Hybrid petrol/electric"),
        ("petrol (hybrid)", "petrol (4S)"),
        ("Fuel Cell", "FuelCell"),
    ],
)
def test_fleet_technology_names_are_translated_to_the_contrib_ids(archive_name, contrib_id):
    resolution = resolve_vehicle_type(
        "car_x_medium_pc_test", _fleet_attributes(archive_name, "PC test Euro-6d")
    )
    assert resolution.excluded is False
    assert resolution.keys.technology == contrib_id
    # Size class and emissions concept are passed through unchanged.
    assert resolution.keys.size_class == "medium"
    assert resolution.keys.emissions_concept == "PC test Euro-6d"


def test_unknown_type_and_unknown_technology_abort():
    with pytest.raises(ValueError, match="brand_new_type"):
        resolve_vehicle_type("brand_new_type", {})
    with pytest.raises(ValueError, match="steam"):
        resolve_vehicle_type("car_x_medium_pc_test", _fleet_attributes("steam"))
    with pytest.raises(ValueError, match="car_incomplete"):
        resolve_vehicle_type("car_incomplete", {"HbefaTechnology": "diesel"})
