"""The before/after measurement of ADR-0164 runs the production assignment and refuses foreign inputs."""
import geopandas as gpd
import pandas as pd
import pytest
from shapely.geometry import Point

from scripts import measure_primary_location_assignment as measure


def _inputs():
    # One zone, homes at the origin; candidates at 1, 10 and 168 km; the 2 km commuter comes last in the input
    # order and is left the 168 km location by the upstream greedy.
    persons = pd.DataFrame({"person_id": [1, 2, 3], "commune_id": "A", "home_location": [Point(0.0, 0.0)] * 3,
                            "commute_distance": [12000.0, 380.0, 2000.0]})
    candidates = gpd.GeoDataFrame({"origin_id": "A", "destination_id": ["A", "B", "EXT"],
                                   "location_id": ["near", "mid", "far"]},
                                  geometry=[Point(1000.0, 0.0), Point(10000.0, 0.0), Point(168000.0, 0.0)])
    cached = pd.DataFrame({"person_id": [1, 2, 3], "location_id": ["mid", "near", "far"]})
    return persons, candidates, cached


def test_compare_reports_the_short_donor_sent_far_before_and_none_after():
    result = measure.compare(*_inputs())
    before, after = result["before_upstream_greedy"], result["after_far_class_rank"]
    assert before["n_donor_le_5km_assigned_gt_45km"] == 1 and after["n_donor_le_5km_assigned_gt_45km"] == 0
    assert before["n_assigned_gt_45km"] == after["n_assigned_gt_45km"] == 1


def test_compare_refuses_inputs_that_do_not_reproduce_the_cached_assignment():
    persons, candidates, cached = _inputs()
    cached["location_id"] = ["near", "mid", "far"]
    with pytest.raises(ValueError, match="differs from the cached assignment"):
        measure.compare(persons, candidates, cached)
