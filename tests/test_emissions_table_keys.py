"""Key pre-check against the HBEFA tables (#688): the tool's lookup order is replayed per type.

Defects these tests catch: a fleet type silently answered by the average table (the eqasim
"fleet-blind" failure), a lookup no table answers (the tool would stop hours into the events), and
a table whose rows repeat a key (the tool silently keeps the later row).
"""
from __future__ import annotations

import pytest

from braunschweig.emissions import table_keys
from braunschweig.emissions.vehicle_types import HbefaKeys, TypeResolution
from tests.emissions_tables import write_tables

BEHAVIOR = "tryDetailedThenTechnologyAverageThenAverageTable"
#: Drivable link length per road type; shares 0.75 and 0.25.
LENGTHS = {"URB/Local/50": 3000.0, "URB/Access/30": 1000.0}


def _type(type_id, category, technology="average", size_class="average", concept="average", excluded=False):
    return TypeResolution(type_id, HbefaKeys(category, technology, size_class, concept), excluded, "")


TYPES = [
    _type("car_exact", "PASSENGER_CAR", "petrol (4S)", "not specified", "PC P Euro-6"),
    # HBEFA 3 spelling: only the tool's rewrite (size 'not specified', 'PC P Euro-6') finds it.
    _type("car_hbefa3", "PASSENGER_CAR", "petrol (4S)", "medium", "PC-P-Euro-6"),
    # No diesel sub-segment in the tables: technology average on one road type, average table else.
    _type("car_diesel", "PASSENGER_CAR", "diesel", "not specified", "PC D Euro-6"),
    _type("default_car", "PASSENGER_CAR"),
    _type("heavy_truck", "HEAVY_GOODS_VEHICLE"),
    _type("Bus", "NON_HBEFA_VEHICLE", excluded=True),
]


def _report(tmp_path, behavior=BEHAVIOR, method="StopAndGoFraction", **table_options):
    index = table_keys.read_table_keys(write_tables(tmp_path, **table_options))
    table_keys.check_table_keys(index)
    return table_keys.precheck_lookups(TYPES, LENGTHS, index, behavior, method)


def test_every_type_gets_the_level_the_tool_would_use(tmp_path):
    report = _report(tmp_path, hgv_cold=True)
    by_id = {entry["type_id"]: entry for entry in report["types"]}

    assert by_id["car_exact"]["warm_length_share_by_level"]["detailed"] == 1.0
    assert by_id["car_exact"]["cold_level"] == "detailed" and not by_id["car_exact"]["fallback"]
    assert by_id["car_hbefa3"]["warm_length_share_by_level"]["detailed_hbefa3_rewrite"] == 1.0
    assert not by_id["car_hbefa3"]["fallback"]
    diesel = by_id["car_diesel"]["warm_length_share_by_level"]
    assert diesel["technology_average"] == 0.75 and diesel["average_table"] == 0.25
    assert by_id["car_diesel"]["cold_level"] == "average_table" and by_id["car_diesel"]["fallback"]
    # Written as average on purpose: the average table is their primary method, not a fallback.
    for type_id in ("default_car", "heavy_truck"):
        assert by_id[type_id]["warm_length_share_by_level"]["average_table"] == 1.0
        assert not by_id[type_id]["expects_detailed"] and not by_id[type_id]["fallback"]
    assert by_id["Bus"]["excluded"] and "cold_level" not in by_id["Bus"]

    assert (report["emitting_types"], report["excluded_types"], report["types_expecting_detailed"]) == (5, 1, 3)
    assert report["primary_types"] == 2 and report["fallback_types"] == ["car_diesel"]
    assert report["fallback_type_share"] == pytest.approx(1 / 3)
    assert report["aborting_types"] == []
    with pytest.raises(ValueError, match=r"car_diesel.*do not match the table's keys"):
        table_keys.check_precheck(report, max_fallback_type_share=0.0)
    table_keys.check_precheck(report, max_fallback_type_share=0.5)


def test_a_lookup_no_table_answers_is_reported_and_raises(tmp_path):
    # Without HGV rows in the cold average table the tool stops at the first truck cold start.
    report = _report(tmp_path, hgv_cold=False)

    truck = next(entry for entry in report["types"] if entry["type_id"] == "heavy_truck")
    assert truck["cold_level"] == "abort" and truck["cold_patterns_aborting"] == 26
    assert report["aborting_types"] == ["heavy_truck"]
    with pytest.raises(ValueError, match=r"heavy_truck.*26 of 26 cold lookups fail"):
        table_keys.check_precheck(report, max_fallback_type_share=1.0)


@pytest.mark.parametrize("behavior, aborting", [
    # No average table is consulted: the two average-keyed types and the diesel type find nothing.
    ("onlyTryDetailedElseAbort", ["car_diesel", "default_car", "heavy_truck"]),
    ("tryDetailedThenTechnologyAverageElseAbort", ["car_diesel", "default_car", "heavy_truck"]),
    ("directlyTryAverageTable", []),
])
def test_the_lookup_behaviour_decides_how_far_the_order_runs(tmp_path, behavior, aborting):
    report = _report(tmp_path, behavior=behavior, hgv_cold=True)

    assert report["aborting_types"] == aborting
    if behavior == "directlyTryAverageTable":
        # The fleet composition is ignored by choice: nothing counts as a fallback.
        assert report["types_expecting_detailed"] == 0 and report["fallback_types"] == []


def test_a_component_missing_in_the_detailed_table_falls_to_the_next_level(tmp_path):
    # The petrol sub-segment has no NOx row: the tool finds CO there and NOx in the average table.
    report = _report(tmp_path, hgv_cold=True, petrol_components=("CO",))

    exact = next(entry for entry in report["types"] if entry["type_id"] == "car_exact")
    assert exact["warm_length_share_by_level"]["average_table"] == 1.0 and exact["fallback"]


def test_average_speed_needs_only_the_free_flow_factor(tmp_path):
    report = _report(tmp_path, method="AverageSpeed", hgv_cold=True)

    assert report["traffic_situations_checked"] == ["FREEFLOW"]
    with pytest.raises(ValueError, match="computation method"):
        _report(tmp_path / "other", method="Average")


def test_tables_the_tool_would_misread_are_refused(tmp_path):
    second_gradient = [("pass. car", "CO", "URB/Local/50/Freeflow", "+2%")]
    index = table_keys.read_table_keys(write_tables(tmp_path, extra_warm_average=second_gradient))
    summary = table_keys.summarize_table_keys(index)

    assert summary["warm_average"]["duplicate_rows"] == 1
    assert summary["warm_average"]["categories"]["HEAVY_GOODS_VEHICLE"]["road_types"] == [
        "URB/Access/30", "URB/Local/50"]
    assert summary["warm_detailed"]["categories"]["PASSENGER_CAR"]["vehicle_keys"] == 2
    assert summary["cold_average"]["components"] == ["CO", "NOx"]
    with pytest.raises(ValueError, match=r"warm_average.*silently keeps the later row.*Gradient"):
        table_keys.check_table_keys(index)

    unknown = [("pass. car", "Unobtainium", "URB/Local/50/Freeflow", "0%")]
    with pytest.raises(ValueError, match="component 'Unobtainium'"):
        table_keys.read_table_keys(write_tables(tmp_path / "unknown", extra_warm_average=unknown))
    jammed = [("pass. car", "CO", "URB/Local/50/Jam", "0%")]
    with pytest.raises(ValueError, match="traffic situation 'URB/Local/50/Jam'"):
        table_keys.read_table_keys(write_tables(tmp_path / "jam", extra_warm_average=jammed))
