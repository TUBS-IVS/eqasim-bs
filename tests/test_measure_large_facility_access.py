"""The independent re-implementation of the large-facility access rule in scripts/measure_large_facility_access.py.

It is the cross-check of the Java rule (eqasim-java-bs LargeFacilityAccessLinkSelector, ADR-0165) and the
source of the rule's default parameters, so a defect here would mis-state both. The synthetic layout mirrors
the Java test: a 600 veh/h side street 10 m from the site, a 1000 veh/h secondary road 200 m away and a
motorway, a trunk and a ramp that are closer but not eligible.
"""
import importlib.util
import pathlib

SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "measure_large_facility_access.py"
spec = importlib.util.spec_from_file_location("measure_large_facility_access", SCRIPT)
measure = importlib.util.module_from_spec(spec)
spec.loader.exec_module(measure)


def _link(nodes, links, link_id, start, end, capacity, highway):
    nodes[link_id + "_from"], nodes[link_id + "_to"] = start, end
    links[link_id] = {"from": link_id + "_from", "to": link_id + "_to", "modes": {"car"}, "capacity": capacity,
                      "highway": highway}


def test_sweep_moves_large_sites_to_the_closest_eligible_link_and_counts_the_rest():
    nodes, links = {}, {}
    _link(nodes, links, "side_street", (-100.0, 10.0), (100.0, 10.0), 600.0, "tertiary")
    _link(nodes, links, "secondary", (-100.0, 200.0), (100.0, 200.0), 1000.0, "secondary")
    _link(nodes, links, "motorway", (-100.0, -50.0), (100.0, -50.0), 6000.0, "motorway")
    _link(nodes, links, "trunk", (-100.0, -60.0), (100.0, -60.0), 4000.0, "trunk")
    _link(nodes, links, "ramp", (-100.0, -40.0), (100.0, -40.0), 1500.0, "motorway_link")
    _link(nodes, links, "far_side_street", (-100.0, 2000.0), (100.0, 2000.0), 600.0, "residential")
    facilities = {
        "work_big": (0.0, 0.0, "side_street"),          # large, small link, secondary 200 m away -> moved
        "work_far": (0.0, 2000.0, "far_side_street"),   # large, nothing eligible within 500 m -> fallback
        "work_main": (0.0, 190.0, "secondary"),         # large, already on 1000 veh/h -> adequate
        "work_small": (10.0, 0.0, "side_street"),       # 900 at 100 % -> below the threshold
        "outside_1": (-10.0, 0.0, "side_street"),       # portal -> excluded, never counted
    }
    counts = {"work_big": 10, "work_far": 10, "work_main": 10, "work_small": 9, "outside_1": 50}

    report = measure.measure(nodes, links, 3600.0, facilities, counts, 0.01, [1000.0], [1000.0], [500.0],
                             ["outside_"], group_by="facility")

    assert report["facilities_with_activities"] == 4
    assert report["size_classes"] == [{"minimum_activities": 1000.0, "facilities": 3,
                                       "current_link_capacity_veh_per_h": {600.0: 2, 1000.0: 1}}]
    (row,) = report["sweep"]
    assert (row["large_facilities"], row["already_adequate"], row["moved"], row["fallback"]) == (3, 1, 1, 1)
    assert row["fallback_share_of_needing"] == 0.5
    assert row["chosen_highway"] == {"secondary": 1}
    assert row["median_moved_distance_m"] == 200.0
    assert row["largest_fallbacks"] == [(1000, "work_far")]


def test_sites_pool_the_facilities_of_one_coordinate():
    nodes, links = {}, {}
    _link(nodes, links, "side_street", (-100.0, 10.0), (100.0, 10.0), 600.0, "tertiary")
    _link(nodes, links, "secondary", (-100.0, 200.0), (100.0, 200.0), 1000.0, "secondary")
    # A workplace and an incommuter workplace at one point: 6 + 5 activities at 1 % = 1100, neither alone is large.
    facilities = {"work_a": (0.0, 0.0, "side_street"), "ic_work_1": (0.0, 0.0, "side_street"),
                  "home_1": (5.0, 5.0, "side_street")}
    counts = {"work_a": 6, "ic_work_1": 5, "home_1": 20}

    by_site = measure.measure(nodes, links, 3600.0, facilities, counts, 0.01, [1000.0], [1000.0], [500.0],
                              ["outside_", "EXT", "home_"], group_by="site")
    by_facility = measure.measure(nodes, links, 3600.0, facilities, counts, 0.01, [1000.0], [1000.0], [500.0],
                                  ["outside_", "EXT", "home_"], group_by="facility")

    assert by_site["sweep"][0]["large_facilities"] == 1
    assert by_site["sweep"][0]["moved"] == 1
    assert by_facility["sweep"][0]["large_facilities"] == 0
