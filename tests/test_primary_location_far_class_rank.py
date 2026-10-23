"""Assignment of the sampled work/education locations to the persons of one origin zone (eqasim-bs#465, ADR-0164).

Regression: person 343377 (donor walked 0.38 km to work) got a work location 168 km away because the greedy
assignment hands the far locations left over at the end of the person order to whoever comes last.
"""
import geopandas as gpd
import pandas as pd
from shapely.geometry import Point

import synthesis.population.spatial.primary.locations as locations


class _Progress:
    def update(self, n=1):
        pass


class _WorkerContext:
    """The parallel data bag of ``process`` as ``process_municipality`` reads it."""

    def __init__(self, df_persons, df_candidates, far_class_rank, min_distance_m=30000.0):
        self._data = {"df_persons": df_persons, "df_candidates": df_candidates, "origin_zone_col": "commune_id",
                      "dest_zone_col": "commune_id", "far_class_rank": far_class_rank,
                      "far_class_min_distance_m": min_distance_m}
        self.progress = _Progress()

    def data(self, name):
        return self._data[name]


def _zone():
    # All homes at the origin; candidates at 1 km, 10 km and 168 km. Input order: a 12 km commuter, a 0.38 km
    # walker, a 2 km commuter. The legacy greedy gives the 12 km commuter the 10 km location, the walker the 1 km
    # one and leaves the 168 km location to the 2 km commuter.
    persons = pd.DataFrame({"person_id": [1, 2, 3], "commune_id": "A",
                            "home_location": [Point(0.0, 0.0)] * 3,
                            "commute_distance": [12000.0, 380.0, 2000.0]})
    candidates = gpd.GeoDataFrame({"origin_id": "A", "destination_id": ["A", "B", "EXT"],
                                   "location_id": ["near", "mid", "far"]},
                                  geometry=[Point(1000.0, 0.0), Point(10000.0, 0.0), Point(168000.0, 0.0)])
    return persons, candidates


def _assigned(far_class_rank):
    persons, candidates = _zone()
    out = locations.process_municipality(_WorkerContext(persons, candidates, far_class_rank), "A")
    return dict(zip(out["person_id"], out["location_id"]))


def test_far_locations_go_to_the_longest_commuters_and_short_commuters_keep_their_near_locations():
    assert _assigned(far_class_rank=True) == {1: "far", 2: "near", 3: "mid"}


def test_legacy_greedy_order_is_kept_when_the_far_class_rank_is_off():
    assert _assigned(far_class_rank=False) == {1: "mid", 2: "near", 3: "far"}


def test_assignment_quality_counts_the_short_donor_assigned_far_and_the_missing_donor_distance():
    persons, candidates = _zone()
    persons.loc[2, "commute_distance"] = float("nan")
    out = locations.process_municipality(_WorkerContext(persons, candidates, far_class_rank=False), "A")
    quality = locations.log_assignment_quality("work", persons, out, False, 30000.0)
    # Legacy greedy: the NaN person (input last) is left the 168 km location; the walker keeps 1 km (dev 0.62 km),
    # the 12 km commuter gets 10 km (dev 2 km).
    assert quality["n_persons"] == 3 and quality["n_without_donor_distance"] == 1
    assert quality["n_short_donor_assigned_far"] == 0
    assert abs(quality["median_abs_deviation_km"] - 1.31) < 1e-9
