"""The allotment of a home Kreis's students to its destinations (issue #140, Phase 4, design B5 and A26).

Defects these tests catch: a cell that ends above or below its whole-numbered total (a site gains or loses a
student), a matching that is not the minimum (the plan rule degrades to an arbitrary allotment), a person without an
observed leg that is treated as primary or not counted, a leg of 0.0 read as missing, a fallback that ignores the
held-back places or the decay, the home-distance rule sending neighbours of a site across, a result that depends on
the input row order or ignores its generator, an observed leg that differs from the commute-distance stage's
selection, a building drawn for the wrong destination, not by capacity or depending on the row order, a minimality
check that misses a cheaper cycle, a diagnostic that counts the wrong persons, and a solver too slow for the real
Kreis size.
"""
from __future__ import annotations

import time

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from scipy.optimize import linprog
from scipy.sparse import coo_matrix
from shapely.geometry import Point

from braunschweig.popsim import commute_distance
from braunschweig.synthesis.locations.university_assignment import (
    _certify_minimum, allot_within_kreis, allotment_diagnostics, destination_centres, draw_buildings,
    observed_education_leg_km, random_allotment_mean_distance,
)

#: Today's production university decay (``education_university_slope``), per km.
_SLOPE = -0.1415


def _persons(xy_km, legs_km) -> pd.DataFrame:
    xy = np.asarray(xy_km, dtype=float) * 1000.0
    return pd.DataFrame({"person_id": [f"p{i:04d}" for i in range(len(xy))], "x": xy[:, 0], "y": xy[:, 1],
                         "leg_km": np.asarray(legs_km, dtype=float)})


def _centres(xy_km_by_destination: dict, scope: str = "local") -> pd.DataFrame:
    return pd.DataFrame({"destination_id": list(xy_km_by_destination),
                         "x": [xy[0] * 1000.0 for xy in xy_km_by_destination.values()],
                         "y": [xy[1] * 1000.0 for xy in xy_km_by_destination.values()],
                         "scope": scope})


def _distance_m(persons: pd.DataFrame, centres: pd.DataFrame, destinations) -> np.ndarray:
    centre = centres.set_index("destination_id").loc[list(destinations), ["x", "y"]].to_numpy()
    home = persons[["x", "y"]].to_numpy()
    return np.sqrt(((home[:, None, :] - centre[None, :, :]) ** 2).sum(axis=2))


def _lp_minimum(cost: np.ndarray, capacities: np.ndarray) -> float:
    """The minimum of the transport problem (unit supplies, whole capacities), solved independently by HiGHS."""
    n, m = cost.shape
    rows = np.concatenate([np.repeat(np.arange(n), m), n + np.tile(np.arange(m), n)])
    cols = np.concatenate([np.arange(n * m), np.arange(n * m)])
    a_eq = coo_matrix((np.ones(2 * n * m), (rows, cols)), shape=(n + m, n * m))
    result = linprog(cost.ravel(), A_eq=a_eq, b_eq=np.concatenate([np.ones(n), capacities]), bounds=(0, None),
                     method="highs")
    assert result.status == 0
    return float(result.fun)


def _random_kreis(generator: np.random.Generator):
    """Homes clustered in a Kreis of 30 km, a few far destinations, shared homes (exact ties), some legs missing."""
    n, m = int(generator.integers(2, 60)), int(generator.integers(1, 8))
    homes = generator.normal(0.0, 6.0, size=(n, 2))
    shared = generator.random(n) < 0.3
    homes[shared] = homes[0]
    legs = np.round(generator.gamma(1.5, 10.0, size=n), 0)
    legs[generator.random(n) < 0.2] = np.nan
    legs[generator.random(n) < 0.05] = 0.0
    centres = {f"d{j}": tuple(generator.normal(0.0, 40.0, size=2)) for j in range(m)}
    totals = generator.multinomial(n, generator.dirichlet(np.ones(m)))
    return _persons(homes, legs), pd.Series(totals, index=list(centres)), _centres(centres)


# ------------------------------------------------------------------------------------------------ allotment
@pytest.mark.parametrize("rule", ["plan_length", "home_distance"])
def test_every_cell_gets_exactly_its_total_and_the_fallback_takes_the_held_back_places(rule):
    generator = np.random.default_rng(7)
    totals = pd.Series([14, 9, 0, 11, 6], index=["Braunschweig TU", "Wolfenbüttel", "Empty", "Hannover", "Far"])
    centres = _centres({"Braunschweig TU": (0, 0), "Wolfenbüttel": (12, -8), "Empty": (1, 1), "Hannover": (-60, 5),
                        "Far": (150, 150)})
    legs = np.round(generator.gamma(2.0, 8.0, size=40), 1)
    legs[[3, 11, 17, 25, 31, 38]] = np.nan
    legs[5] = 0.0
    persons = _persons(generator.normal(0.0, 8.0, size=(40, 2)), legs)

    assigned = allot_within_kreis(persons, totals, centres, rule, _SLOPE, np.random.default_rng(1))

    assert list(assigned.columns) == ["person_id", "destination_id", "path"]
    assert assigned["person_id"].tolist() == persons["person_id"].tolist()
    counts = assigned["destination_id"].value_counts().reindex(totals.index, fill_value=0)
    pd.testing.assert_series_equal(counts, totals, check_names=False)
    path = assigned.set_index("person_id")["path"]
    if rule == "home_distance":
        assert (path == "home_distance").all()
        return
    missing_leg = persons.set_index("person_id")["leg_km"].isna()
    assert (path[missing_leg] == "fallback_decay").all() and (path[~missing_leg] == "plan_length").all()
    assert path["p0005"] == "plan_length", "a leg of 0.0 km is an observed leg"
    # Six fallback persons over the totals 14, 9, 0, 11, 6 of 40: quotas 2.1, 1.35, 0, 1.65, 0.9 -> floors 2, 1, 0,
    # 1, 0 and the two places left to the largest remainders (0.9 and 0.65).
    held = assigned.loc[assigned["path"] == "fallback_decay", "destination_id"].value_counts()
    assert held.reindex(totals.index, fill_value=0).tolist() == [2, 1, 0, 2, 1]


@pytest.mark.parametrize("rule", ["plan_length", "home_distance"])
def test_the_matching_is_the_minimum_of_the_transport_problem(rule):
    """Compared with an independent LP solve; the module rounds its costs to whole metres, hence the slack."""
    generator = np.random.default_rng(20261128)
    for case in range(40):
        persons, totals, centres = _random_kreis(generator)
        assigned = allot_within_kreis(persons, totals, centres, rule, _SLOPE, np.random.default_rng(case))
        matched = assigned["path"] != "fallback_decay"
        if not matched.any():
            continue
        target_m = np.zeros(len(persons)) if rule == "home_distance" else persons["leg_km"].to_numpy() * 1000.0
        cost = np.abs(_distance_m(persons, centres, totals.index) - target_m[:, None])[matched.to_numpy()]
        column = pd.Index(totals.index).get_indexer(assigned.loc[matched, "destination_id"])
        achieved = float(cost[np.arange(len(cost)), column].sum())
        capacities = assigned.loc[matched, "destination_id"].value_counts().reindex(totals.index, fill_value=0)
        minimum = _lp_minimum(cost, capacities.to_numpy())
        assert minimum - 1e-6 <= achieved <= minimum + len(cost) * 1.0, f"case {case}: {achieved} vs {minimum}"


def test_the_person_with_the_longer_leg_gets_the_farther_destination():
    persons = _persons([(0, 0), (0, 0), (0.2, 0)], [30.0, 2.0, 25.0])
    centres = _centres({"near": (3, 0), "far": (25, 0)})
    totals = pd.Series({"near": 1, "far": 2})
    for seed in range(10):
        assigned = allot_within_kreis(persons, totals, centres, "plan_length", _SLOPE, np.random.default_rng(seed))
        assert assigned.set_index("person_id")["destination_id"].to_dict() == {
            "p0000": "far", "p0001": "near", "p0002": "far"}


def test_a_person_without_a_leg_takes_the_decay_fallback_on_the_held_back_places():
    # Fallback persons next to site A and next to site B; one held-back place each (2 x 3/6). With a steep decay each
    # takes its own site whichever comes first in the seeded order; the leg of 0.0 km is primary.
    persons = _persons([(0, 0.1), (10, 0.1), (0, 0), (10, 0), (5, 0), (5, 1)], [np.nan, np.nan, 0.0, 0.0, 5.0, 5.0])
    centres = _centres({"A": (0, 0), "B": (10, 0)})
    for seed in range(10):
        assigned = allot_within_kreis(persons, pd.Series({"A": 3, "B": 3}), centres, "plan_length", -5.0,
                                      np.random.default_rng(seed)).set_index("person_id")
        assert assigned.loc[["p0000", "p0001"], "path"].tolist() == ["fallback_decay", "fallback_decay"]
        assert assigned.loc[["p0000", "p0001"], "destination_id"].tolist() == ["A", "B"]
        assert assigned.loc[["p0002", "p0003"], "path"].tolist() == ["plan_length", "plan_length"]
        assert assigned.loc[["p0002", "p0003"], "destination_id"].tolist() == ["A", "B"]


def test_home_distance_sends_nobody_across_on_a_line_of_homes():
    xs = np.arange(10) + 0.5
    persons = _persons(np.column_stack([xs, np.zeros(10)]), [40.0, np.nan] * 5)
    centres = _centres({"west": (0, 0), "east": (10, 0)})
    for seed in range(5):
        assigned = allot_within_kreis(persons, pd.Series({"west": 6, "east": 4}), centres, "home_distance", _SLOPE,
                                      np.random.default_rng(seed))
        assert assigned["destination_id"].tolist() == ["west"] * 6 + ["east"] * 4
        assert (assigned["path"] == "home_distance").all()


@pytest.mark.parametrize("rule", ["plan_length", "home_distance"])
def test_the_allotment_follows_its_seed_and_not_the_row_order(rule):
    persons, totals, centres = _random_kreis(np.random.default_rng(3))
    first = allot_within_kreis(persons, totals, centres, rule, _SLOPE, np.random.default_rng(11))
    shuffled = persons.sample(frac=1.0, random_state=5)
    second = allot_within_kreis(shuffled, totals.iloc[::-1], centres.iloc[::-1], rule, _SLOPE,
                                np.random.default_rng(11))
    assert second["person_id"].tolist() == shuffled["person_id"].tolist()
    pd.testing.assert_frame_equal(first.set_index("person_id").sort_index(),
                                  second.set_index("person_id").sort_index())


_PERSONS = _persons([(0, 0), (1, 0)], [3.0, np.nan])
_CENTRES = _centres({"A": (0, 0), "B": (5, 0)})
_REFUSALS = {
    "unknown_rule": (_PERSONS, pd.Series({"A": 1, "B": 1}), _CENTRES, "nearest", "rule"),
    "persons_and_totals_differ": (_PERSONS, pd.Series({"A": 2, "B": 1}), _CENTRES, "plan_length", "persons"),
    "cell_without_centre": (_PERSONS, pd.Series({"A": 1, "C": 1}), _CENTRES, "home_distance", "centre"),
    "fractional_total": (_PERSONS, pd.Series({"A": 1.5, "B": 0.5}), _CENTRES, "plan_length", "whole"),
    "negative_leg": (_persons([(0, 0), (1, 0)], [-1.0, 2.0]), pd.Series({"A": 1, "B": 1}), _CENTRES, "plan_length",
                     "leg"),
    "duplicate_person": (pd.concat([_PERSONS.iloc[:1]] * 2), pd.Series({"A": 1, "B": 1}), _CENTRES, "plan_length",
                         "more than once"),
}


@pytest.mark.parametrize("persons, totals, centres, rule, message", list(_REFUSALS.values()), ids=list(_REFUSALS))
def test_the_allotment_refuses_input_it_cannot_honour(persons, totals, centres, rule, message):
    with pytest.raises(ValueError, match=message):
        allot_within_kreis(persons, totals, centres, rule, _SLOPE, np.random.default_rng(0))


@pytest.mark.parametrize("cost, minimal, not_minimal", [
    # Two persons: the exchange saves 9 m.
    ([[0, 10], [0, 1]], [0, 1], [1, 0]),
    # Three persons: no exchange of two helps, only the cycle of all three (6 m -> 0 m).
    ([[2, 0, 9], [9, 2, 0], [0, 9, 2]], [1, 2, 0], [0, 1, 2]),
], ids=["exchange_of_two", "cycle_of_three"])
def test_the_minimality_check_refuses_an_allotment_with_a_cheaper_cycle(cost, minimal, not_minimal):
    """The solver's last line of defence; the error path cannot be reached through a correct solver."""
    cost = np.array(cost, dtype=np.int64)
    _certify_minimum(cost, np.array(minimal))
    with pytest.raises(RuntimeError, match="not minimal"):
        _certify_minimum(cost, np.array(not_minimal))


def test_a_real_size_kreis_is_allotted_within_seconds():
    """A Kreis larger than the largest real one (the module docstring states that size as an ASSUMPTION).

    ASSUMPTION (a dated single measurement on the development laptop, 2026-11-28; other machines differ): 1.2-1.3 s
    per rule here, 0.7-0.8 s for 4,500 x 19. The bound of 15 s per rule leaves a tenfold margin for a slower CI
    runner and still fails a general LP solver (the module docstring gives its measured times). It cannot be
    cheaper: the size itself is what the test checks.
    """
    generator = np.random.default_rng(5000)
    n, m = 5000, 35
    legs = generator.gamma(2.0, 8.0, size=n)
    legs[generator.random(n) < 0.15] = np.nan
    persons = _persons(generator.normal(0.0, 7.0, size=(n, 2)), legs)
    centres = _centres({f"d{j:02d}": tuple(generator.normal(0.0, 60.0, size=2)) for j in range(m)})
    totals = pd.Series(generator.multinomial(n, generator.dirichlet(np.ones(m))), index=centres["destination_id"])
    for rule in ("plan_length", "home_distance"):
        start = time.perf_counter()
        assigned = allot_within_kreis(persons, totals, centres, rule, _SLOPE, np.random.default_rng(1))
        elapsed = time.perf_counter() - start
        assert assigned["destination_id"].value_counts().reindex(totals.index, fill_value=0).tolist() == \
            totals.tolist()
        assert elapsed < 15.0, f"{rule}: {elapsed:.1f} s"


# ------------------------------------------------------------------------------------------------ observed leg
def test_the_observed_leg_is_the_commute_distance_selection_without_imputation():
    rows = [  # person, preceding, following, euclidean distance (m)
        (1, "home", "education", 3000.0), (2, "home", "work", 9000.0), (1, "education", "home", 3500.0),
        (2, "work", "education", 2000.0), (2, "education", "home", 8000.0),
        (3, "home", "shop", 500.0), (3, "shop", "education", 700.0), (3, "education", "home", np.nan),
        (4, "home", "education", np.nan), (4, "education", "home", 4000.0),
        (5, "home", "work", 12000.0), (5, "work", "home", 12000.0),
        (6, "education", "home", 1500.0), (6, "home", "education", 2500.0),
        (7, "home", "education", 0.0), (7, "education", "leisure", 300.0),
        (8, "shop", "education", 900.0), (8, "education", "shop", 900.0),
    ]
    trips = pd.DataFrame(rows, columns=["person_id", "preceding_purpose", "following_purpose", "euclidean_distance"])

    observed = observed_education_leg_km(trips)
    reference = commute_distance.run(trips, random_seed=0)["education"].set_index("person_id")["commute_distance"]

    assert sorted(observed.index) == sorted(reference.index) == [1, 2, 3, 4, 6, 7]
    pd.testing.assert_series_equal(observed.dropna().sort_index(), (reference.loc[observed.dropna().index] / 1000.0)
                                   .sort_index(), check_names=False)
    assert observed[[1, 2, 6, 7]].tolist() == [3.0, 8.0, 1.5, 0.0]
    assert np.isnan(observed[3]) and np.isnan(observed[4]), "a missing length stays missing (no imputation)"


# ------------------------------------------------------------------------------------------------ centres, buildings
def _facilities(crs: str = "EPSG:25832") -> gpd.GeoDataFrame:
    return gpd.GeoDataFrame({
        "location_id": ["uni_loc_a_0", "uni_loc_a_1", "uni_sur_b", "uni_loc_c_0"],
        "capacity": [1.0, 3.0, 7.0, 2.0],
        "destination_id": ["A", "A", "B", "C"],
        "scope": ["local", "local", "surrounding", "local"],
        "site_ags8": ["03101000", "03101000", None, "03158037"],
    }, geometry=[Point(0, 0), Point(4000, 0), Point(100000, 5000), Point(20000, 1000)], crs=crs)


def test_centres_are_capacity_weighted_and_buildings_are_drawn_by_capacity():
    facilities = _facilities()
    centres = destination_centres(facilities)
    assert centres.set_index("destination_id")[["x", "y", "scope"]].to_dict("index") == {
        "A": {"x": 3000.0, "y": 0.0, "scope": "local"}, "B": {"x": 100000.0, "y": 5000.0, "scope": "surrounding"},
        "C": {"x": 20000.0, "y": 1000.0, "scope": "local"}}
    with pytest.raises(ValueError, match="EPSG:25832"):
        destination_centres(_facilities("EPSG:4326"))

    destinations = pd.Series(["A"] * 4000 + ["B"] * 10 + ["C"] * 5, index=np.arange(4015) + 100)
    drawn = draw_buildings(destinations, facilities, np.random.default_rng(2))
    assert drawn.index.equals(destinations.index)
    assert set(drawn[destinations == "A"]) == {"uni_loc_a_0", "uni_loc_a_1"}
    assert (drawn[destinations == "B"] == "uni_sur_b").all() and (drawn[destinations == "C"] == "uni_loc_c_0").all()
    # Capacity 3 of 4: the share of building 1 is 0.75 (standard error 0.007 at 4,000 draws).
    assert abs((drawn[destinations == "A"] == "uni_loc_a_1").mean() - 0.75) < 0.03
    # The same persons in another row order get the same buildings, returned in their own row order.
    shuffled = destinations.sample(frac=1.0, random_state=9)
    drawn_shuffled = draw_buildings(shuffled, facilities, np.random.default_rng(2))
    assert drawn_shuffled.index.equals(shuffled.index)
    pd.testing.assert_series_equal(drawn_shuffled.loc[drawn.index], drawn)
    with pytest.raises(ValueError, match="no facility"):
        draw_buildings(pd.Series(["A", "Z"]), facilities, np.random.default_rng(2))


# ------------------------------------------------------------------------------------------------ diagnostics
def test_the_diagnostics_count_the_neighbours_of_a_site_sent_elsewhere():
    # S1 and S2 share a centre (two sites of one commune), S3 is a surrounding institution, S4 a local site 10 km off,
    # S5 a local site 30 km off that receives nobody (no place for the Kreis).
    centres = pd.concat([_centres({"S1": (0, 0), "S2": (0, 0), "S4": (10, 0), "S5": (30, 0)}),
                         _centres({"S3": (50, 0)}, scope="surrounding")], ignore_index=True)
    persons = _persons([(1, 0), (0.5, 0), (1.5, 0), (5, 0), (9, 0), (30.5, 0)],
                       [1.0, np.nan, 40.0, 3.0, np.nan, np.nan])
    assigned = pd.DataFrame({"person_id": persons["person_id"],
                             "destination_id": ["S1", "S2", "S3", "S4", "S1", "S3"],
                             "path": ["plan_length", "fallback_decay", "plan_length", "plan_length",
                                      "fallback_decay", "fallback_decay"],
                             "home_ars5": ["K1", "K1", "K1", "K1", "K2", "K2"]})

    diagnostics = allotment_diagnostics(assigned.iloc[::-1], persons, centres)

    assert diagnostics["n_persons"] == 6
    assert diagnostics["n_by_path"] == {"fallback_decay": 3, "plan_length": 3}
    assert diagnostics["n_with_observed_leg"] == 3
    assert diagnostics["n_near_local_site"] == 5  # persons 1, 2, 3 (S1/S2), 5 (S4) and 6 (S5), within 2 km
    assert diagnostics["n_near_local_site_sent_elsewhere"] == 3  # 3 (to S3), 5 (to S1, not S4), 6 (to S3, not S5)
    assert diagnostics["n_near_local_site_with_place"] == 4  # as above without person 6: S5 has no place
    assert diagnostics["n_near_local_site_with_place_sent_elsewhere"] == 2  # persons 3 and 5
    assert diagnostics["mean_abs_leg_gap_km"] == pytest.approx((0.0 + 8.5 + 2.0) / 3)
    assert diagnostics["mean_distance_km"] == pytest.approx((1.0 + 0.5 + 48.5 + 5.0 + 9.0 + 19.5) / 6)

    # Persons 5 and 6 form a second Kreis whose places are S1 and S3 only: S4 has no place for person 5 any more.
    per_kreis = allotment_diagnostics(assigned, persons, centres, kreis_column="home_ars5")
    assert (per_kreis["n_near_local_site"], per_kreis["n_near_local_site_sent_elsewhere"]) == (5, 3)
    assert per_kreis["n_near_local_site_with_place"] == 3  # persons 1, 2, 3
    assert per_kreis["n_near_local_site_with_place_sent_elsewhere"] == 1  # person 3

    # One home, places at 3 km (one) and 6 km (three): every random allotment has the mean (3 + 18) / 4.
    one_home = _persons([(0, 0)] * 4, [np.nan] * 4)
    baseline = random_allotment_mean_distance(one_home, pd.Series({"S1": 0, "near": 1, "far": 3}),
                                              _centres({"S1": (0, 0), "near": (3, 0), "far": (0, 6)}),
                                              np.random.default_rng(0))
    assert baseline == pytest.approx(5.25)
