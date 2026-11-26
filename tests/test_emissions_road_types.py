"""HBEFA road type per link (#688): faithful port of MATSim's ``OsmHbefaMapping`` plus counting.

The expected values are worked out by hand from the Java source (constants and branches of
``OsmHbefaMapping.getHEBFAtype``), not by re-running the port's logic.
"""
from __future__ import annotations

import csv
import math

import pytest

from braunschweig.emissions import road_type_proof
from braunschweig.emissions.road_types import RoadTypeTagger, UnmappedRoadClass, osm_hbefa_road_type
from braunschweig.emissions.view import LinkRecord

KMH = 1 / 3.6


@pytest.mark.parametrize(
    "osm_class, speed_kmh, expected",
    [
        # Motorways are always "Nat." (precedence of || over && in the original condition).
        ("motorway", 200, "URB/MW-Nat./130"),
        ("motorway", 60, "URB/MW-Nat./80"),
        ("motorway_link", 80, "URB/MW-Nat./80"),
        # Primary: national from 90 km/h; 89 rounds to 90.
        ("primary", 100, "URB/Trunk-Nat./100"),
        ("primary", 89, "URB/Trunk-Nat./90"),
        ("primary", 50, "URB/Trunk-City/50"),
        ("trunk", 120, "URB/Trunk-City/80"),
        ("trunk_link", 50, "URB/Trunk-City/50"),
        ("secondary", 30, "URB/Distr/50"),
        # 25 km/h rounds half up to 30, then the Local minimum of 50 applies.
        ("tertiary", 25, "URB/Local/50"),
        ("tertiary", 100, "URB/Local/60"),
        # 15 km/h rounds half up to 20, raised to the Access minimum.
        ("residential", 15, "URB/Access/30"),
        ("residential", 50, "URB/Access/50"),
        ("living_street", 10, "URB/Access/30"),
        # Unclassified is re-classed by speed alone.
        ("unclassified", 25, "URB/Access/30"),
        ("unclassified", 60, "URB/Local/60"),
        ("unclassified", 70, "URB/Distr/70"),
        ("unclassified", 80, "URB/Trunk-City/80"),
        ("unclassified", 100, "URB/MW-Nat./100"),
    ],
)
def test_road_type_matches_the_contrib_mapping(osm_class, speed_kmh, expected):
    assert osm_hbefa_road_type(osm_class, speed_kmh * KMH)[0] == expected


def test_unknown_class_and_invalid_speed_raise():
    with pytest.raises(UnmappedRoadClass, match="pedestrian"):
        osm_hbefa_road_type("pedestrian", 10 * KMH)
    with pytest.raises(ValueError, match="positive"):
        osm_hbefa_road_type("residential", 0.0)


def _link(link_id, osm_class, speed_kmh, modes, length=1000.0, **attributes):
    if osm_class is not None:
        attributes["osm:way:highway"] = osm_class
    return LinkRecord(link_id, length, speed_kmh * KMH, modes, attributes)


def test_tagger_counts_primary_fallback_and_speed_shifts_and_enforces_the_bound():
    links = [
        _link("1", "residential", 15, "car,truck"),                     # raised to 30
        _link("2", "trunk", 120, "car"),                                # lowered to 80
        _link("3", "secondary", 70, "car", length=2000.0),              # inside the range
        _link("4", "primary", 50, "car", allowed_speed=str(100 * KMH)),  # allowed speed wins
        _link("5", "track", 30, "truck", length=1000.0),                # drivable, unmapped
        _link("6", None, math.inf, "pt"),                               # rail: not drivable
        _link("7", "service", 20, "pt"),                                # mapped, not drivable
    ]
    tagger = RoadTypeTagger(frozenset({"car", "truck"}), "URB/Access/30", max_fallback_length_share=0.1)

    assert [tagger(link) for link in links] == [
        "URB/Access/30", "URB/Trunk-City/80", "URB/Distr/70", "URB/Trunk-Nat./100",
        "URB/Access/30", "URB/Access/30", "URB/Access/30",
    ]
    report = tagger.report()
    assert report["drivable_links"] == 5
    assert report["not_drivable_links"] == 2
    assert report["fallback_links"] == 1
    assert report["fallback_osm_classes"] == {"track": 1}
    assert report["fallback_length_share"] == pytest.approx(1000.0 / 6000.0)
    assert report["speed_raised_to_class_minimum_links"] == 1
    assert report["speed_lowered_to_class_maximum_links"] == 1
    assert report["speed_from_allowed_speed_links"] == 1
    assert report["links_by_road_type"]["URB/Access/30"] == 2  # link 1 and the fallback link 5
    assert sum(report["length_meters_by_road_type"].values()) == pytest.approx(6000.0)

    # 16.7 % of the drivable length is on the fallback: above the 10 % bound.
    with pytest.raises(ValueError, match="track"):
        tagger.check()

    lenient = RoadTypeTagger(frozenset({"car", "truck"}), "URB/Access/30", max_fallback_length_share=0.2)
    for link in links:
        lenient(link)
    lenient.check()


def test_the_port_reproduces_every_case_of_the_parity_proof():
    """Every OSM class and freespeed on the usable links of the proven 100 % network, with the road
    type the Java ORIGINAL returned (run manifest emissions-road-type-parity-2026-11-26). An edit
    of the port or of its class table that leaves the proven behaviour fails here."""
    proofs = road_type_proof.load_proofs()  # the committed record; the stage tests use their own
    assert [proof["run_manifest"] for proof in proofs] == ["emissions-road-type-parity-2026-11-26"]
    with road_type_proof.CASES_FILE.open(newline="", encoding="utf-8") as handle:
        cases = list(csv.DictReader(handle))
    assert len(cases) == 114 and sum(int(case["links"]) for case in cases) == 214_807
    different = [
        (case["osm_class"], case["freespeed_meters_per_second"], case["java_road_type"], ported)
        for case in cases
        if (ported := osm_hbefa_road_type(case["osm_class"], float(case["freespeed_meters_per_second"]))[0])
        != case["java_road_type"]
    ]
    assert different == []
