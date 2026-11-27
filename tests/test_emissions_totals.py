"""Totals and vehicle-km of the post-run emissions summary (#688), pure functions on hand-made input.

Defects these tests catch: a fallback share that is only ever asserted at zero, emissions of a
vehicle type nobody classified that end up in the totals, an after-24:00 share that disappears
when the tool's two files name a pollutant differently, and a vehicles file that is read as empty
so that all driving counts as "unknown". All numbers are synthetic.
"""
from __future__ import annotations

import gzip
import logging

import pytest

from braunschweig.emissions import totals, vehicle_km
from tests.emissions_archive import write_archive


def _entry(type_id, *, excluded=False, expects_detailed=True, fallback=False):
    return {"type_id": type_id, "excluded": excluded, "expects_detailed": expects_detailed, "fallback": fallback}


def _driven(**meters_by_type):
    return {
        "by_type": {
            type_id: {"meters": meters, "meters_after_day_end": 0.0, "link_entries": 1, "vehicles": 1,
                      "vehicles_with_link_entry": 1}
            for type_id, meters in meters_by_type.items()
        },
        "event_lines": len(meters_by_type),
        "unknown_vehicle_link_entries": 0,
        "unknown_vehicle_meters": 0.0,
    }


ENTRIES = [
    _entry("car_primary"),
    _entry("car_fallback", fallback=True),
    _entry("default_car", expects_detailed=False),
    _entry("Bus", excluded=True),
]


def test_the_fallback_share_weights_the_fleet_types_by_their_vehicle_km():
    summary = totals.summarize(
        {("car_primary", "CO"): 3000.0, ("car_fallback", "CO"): 2000.0, ("default_car", "CO"): 1000.0},
        {"CO": 6.0},
        _driven(car_primary=3000.0, car_fallback=1000.0, default_car=4000.0, Bus=0.0),
        ENTRIES, 0.5)

    # Fleet = the types with their own technology: 3 km on the detailed table, 1 km below it.
    assert summary["fleet_vehicle_km_sample"] == pytest.approx(4.0)
    assert summary["fallback_vehicle_km_share"] == pytest.approx(0.25)
    assert summary["groups"]["primary"]["kilograms"] == {"CO": pytest.approx(3.0)}
    assert summary["groups"]["fallback"]["kilograms"] == {"CO": pytest.approx(2.0)}
    assert summary["groups"]["fallback"]["vehicle_km_scaled"] == pytest.approx(2.0)
    # The average type chosen on purpose is no fallback: 4 of 8 emitting km.
    assert summary["average_table_by_choice_vehicle_km_share"] == pytest.approx(0.5)
    assert summary["pollutants"]["CO"]["gram_per_vehicle_km"] == pytest.approx(6000.0 / 16.0)


@pytest.mark.parametrize("type_id, message", [
    ("car_nobody_classified", "the pre-check did not see"),
    ("Bus", "excluded"),
])
def test_an_emission_of_an_unclassified_or_excluded_type_is_refused(type_id, message):
    with pytest.raises(ValueError, match=message):
        totals.summarize(
            {("car_primary", "CO"): 3000.0, (type_id, "CO"): 5000.0}, {"CO": 8.0},
            _driven(car_primary=3000.0), ENTRIES, 1.0)


def test_an_excluded_type_listed_with_zero_emission_is_accepted():
    summary = totals.summarize(
        {("car_primary", "CO"): 3000.0, ("Bus", "CO"): 0.0}, {"CO": 3.0},
        _driven(car_primary=3000.0, Bus=500.0), ENTRIES, 1.0)

    assert summary["pollutants"]["CO"]["total_kg"] == pytest.approx(3.0)
    assert summary["groups"]["excluded"]["vehicle_km_sample"] == pytest.approx(0.5)


def test_pollutants_the_two_tool_files_do_not_share_are_named(caplog):
    with caplog.at_level(logging.WARNING, logger="braunschweig.emissions.totals"):
        summary = totals.summarize(
            {("car_primary", "CO"): 3000.0, ("car_primary", "NOx"): 100.0, ("car_primary", "PM"): 10.0},
            # CO: 4 kg before 24:00 against a 3 kg total; NOx is absent; HC occurs only here.
            {"CO": 4.0, "PM": 0.01, "HC": 1.0},
            _driven(car_primary=3000.0), ENTRIES, 1.0)

    assert summary["pollutants_without_day_total"] == ["NOx"]
    assert summary["day_total_pollutants_without_type_rows"] == ["HC"]
    assert summary["pollutants_with_day_total_above_total"] == ["CO"]
    assert summary["pollutants"]["NOx"]["after_24h_share"] is None
    assert summary["pollutants"]["PM"]["after_24h_share"] == pytest.approx(0.0)
    # Each of the three is warned about, not only stored.
    assert len([record for record in caplog.records if record.levelno == logging.WARNING]) == 3


def test_a_vehicles_file_that_yields_no_vehicle_is_refused(tmp_path):
    archive = write_archive(tmp_path)
    # Attribute order the line pattern does not know: without the check all driving would be "unknown".
    for name in ("output_vehicles.xml.gz", "output_transitVehicles.xml.gz"):
        with gzip.open(archive / name, "rt", encoding="utf-8") as handle:
            text = handle.read()
        with gzip.open(archive / name, "wt", encoding="utf-8", newline="") as handle:
            handle.write(text.replace('<vehicle id="', '<vehicle  id="'))

    with pytest.raises(ValueError, match="no vehicle"):
        vehicle_km.vehicle_km_by_type(archive)
