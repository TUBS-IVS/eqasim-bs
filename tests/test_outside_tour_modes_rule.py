import numpy as np
import pandas as pd
import pytest

from braunschweig.calibration import srv_mode_by_distance_band as M
from braunschweig.synthesis.outside_tour_modes import rule as R
from braunschweig.synthesis.outside_tour_modes import tours as T
from braunschweig.synthesis.outside_tour_modes.srv_table import ModeShareTable


def _table():
    rows = []
    cells = {
        ("work", 0.0, 5.0): {"walk": 0.3, "bicycle": 0.3, "car": 0.3, "car_passenger": 0.05, "pt": 0.05},
        ("work", 5.0, float("inf")): {"walk": 0.0, "bicycle": 0.005, "car": 0.8, "car_passenger": 0.1, "pt": 0.095},
        ("education", 0.0, 5.0): {"walk": 0.4, "bicycle": 0.3, "car": 0.0, "car_passenger": 0.2, "pt": 0.1},
        ("education", 5.0, float("inf")): {"walk": 0.0, "bicycle": 0.0, "car": 0.2, "car_passenger": 0.3, "pt": 0.5},
        ("other", 0.0, 5.0): {"walk": 0.3, "bicycle": 0.1, "car": 0.5, "car_passenger": 0.05, "pt": 0.05},
        ("other", 5.0, float("inf")): {"walk": 0.0, "bicycle": 0.1, "car": 0.7, "car_passenger": 0.1, "pt": 0.1},
        ("all", 0.0, 45.0): {"walk": 0.3, "bicycle": 0.2, "car": 0.4, "car_passenger": 0.05, "pt": 0.05},
        ("all", 45.0, float("inf")): {"walk": 0.0, "bicycle": 0.0, "car": 0.7, "car_passenger": 0.05, "pt": 0.25},
    }
    for (group, lower, upper), shares in cells.items():
        for mode in M.MODES:
            rows.append((group, M.band_label(lower, upper), lower, upper, mode, shares[mode], 100, int(shares[mode] * 100)))
    return ModeShareTable.from_frame(pd.DataFrame(rows, columns=M.TABLE_COLUMNS))


def _trips(rows):
    columns = ["person_id", "trip_index", "preceding_purpose", "following_purpose", "mode"]
    return pd.DataFrame(rows, columns=columns)


def test_purpose_groups_follow_the_precedence_portal_home_destination():
    trips = _trips([
        (1, 0, "home", "work", "walk"), (1, 1, "work", "shop", "walk"), (1, 2, "shop", "home", "walk"),
        (2, 0, "home", "outside", "car"), (2, 1, "outside", "home", "car"),
        (3, 0, "home", "education", "bicycle"), (3, 1, "education", "home", "bicycle"),
    ])
    assert R.purpose_groups(trips).tolist() == ["work", "other", "other", "all", "all", "education", "education"]


def test_leg_cells_use_the_distance_band_and_the_top_band_for_portal_legs():
    trips = _trips([(1, 0, "home", "work", "walk"), (2, 0, "home", "outside", "walk")])
    groups = R.purpose_groups(trips)
    cells = R.leg_cells(groups, np.array([12.0, 3.0]), _table())
    assert cells[0][:2] == ("work", "5_plus") and cells[1][:2] == ("all", "45_plus")
    masked = R.leg_cells(groups, np.array([12.0, 3.0]), _table(), mask=np.array([True, False]))
    assert masked[0][:2] == ("work", "5_plus") and masked[1] is None


def test_implausible_legs_compare_the_donor_mode_share_with_min_share():
    trips = _trips([(1, 0, "home", "work", "walk"), (1, 1, "work", "home", "car"), (2, 0, "home", "work", "bicycle")])
    cells = R.leg_cells(R.purpose_groups(trips), np.array([12.0, 12.0, 2.0]), _table())
    assert R.implausible_legs(trips, cells, 0.01).tolist() == [True, False, False]
    cells[0] = None                                    # a leg that was not looked up is never implausible
    assert R.implausible_legs(trips, cells, 0.01).tolist() == [False, False, False]


def test_allowed_modes_add_the_always_available_modes():
    availability = pd.DataFrame({"can_car": [True, False], "can_bicycle": [False, True], "can_car_passenger": [True, False],
                                 "has_car": [True, False]}, index=pd.Index([1, 2], name="person_id"))
    allowed = R.allowed_modes(availability)
    assert allowed[1] == frozenset({"pt", "walk", "car", "car_passenger"})
    assert allowed[2] == frozenset({"pt", "walk", "bicycle"})


def _draw_fixture():
    trips = _trips([
        (1, 0, "home", "work", "walk"), (1, 1, "work", "shop", "walk"), (1, 2, "shop", "home", "walk"),   # outside tour
        (1, 3, "home", "leisure", "walk"), (1, 4, "leisure", "home", "walk"),                             # inside tour
        (2, 0, "home", "education", "bicycle"), (2, 1, "education", "home", "bicycle"),                   # outside, child
    ])
    tour_ids = T.home_based_tour_ids(trips)
    outside = np.array([True, True, True, False, False, True, True])
    leg_km = np.array([20.0, 1.0, 19.0, 1.0, 1.0, 15.0, 15.0])
    cells = R.leg_cells(R.purpose_groups(trips), leg_km, _table())
    implausible = R.implausible_legs(trips, cells, 0.01)
    allowed = {1: frozenset({"pt", "walk", "car", "car_passenger", "bicycle"}), 2: frozenset({"pt", "walk"})}
    return trips, tour_ids, outside, implausible, leg_km, cells, allowed


def test_tour_substitutes_draw_one_mode_per_outside_tour_with_an_implausible_leg():
    trips, tour_ids, outside, implausible, leg_km, cells, allowed = _draw_fixture()
    subs = R.tour_substitutes(trips, tour_ids, outside, implausible, leg_km, cells, allowed, 0.01,
                              np.random.RandomState(1))
    assert subs[["person_id", "tour"]].to_numpy().tolist() == [[1, 0], [2, 0]]
    # person 1: the longest implausible leg is the 20 km work leg -> cell work/5_plus -> candidates car,
    # car_passenger, pt (bicycle 0.005 and walk 0.0 are implausible); every candidate is allowed
    assert subs.loc[0, "cell"] == "work/5_plus" and subs.loc[0, "substitute"] in {"car", "car_passenger", "pt"}
    assert subs.loc[0, "n_candidates"] == 3 and subs.loc[0, "n_removed_by_availability"] == 0
    assert subs.loc[0, "n_legs"] == 3 and subs.loc[0, "n_legs_implausible"] == 2   # the 20 km and the 19 km walk
    assert subs.loc[0, "donor_modes"] == "walk" and subs.loc[0, "removed_modes"] == ""
    # person 2: education/5_plus candidates car, car_passenger, pt; the child may only use pt and walk
    assert subs.loc[1, "substitute"] == "pt" and subs.loc[1, "n_removed_by_availability"] == 2
    assert subs.loc[1, "removed_modes"] == "car,car_passenger"


def test_tour_substitutes_are_deterministic_for_a_seed_and_differ_between_seeds():
    args = _draw_fixture()
    draws = [R.tour_substitutes(*args, 0.01, np.random.RandomState(seed))["substitute"].tolist() for seed in (3, 3)]
    assert draws[0] == draws[1]
    many = {tuple(R.tour_substitutes(*args, 0.01, np.random.RandomState(seed))["substitute"]) for seed in range(40)}
    assert len(many) > 1   # the draw of person 1 varies over car / car_passenger / pt


def test_tour_substitutes_fall_back_to_the_always_available_modes_only_through_the_table():
    # No candidate survives the availability restriction: raise (the table assertion at load time makes this
    # unreachable in production; a silent pt fallback would hide a broken table).
    trips, tour_ids, outside, implausible, leg_km, cells, _ = _draw_fixture()
    allowed = {1: frozenset({"walk"}), 2: frozenset({"walk"})}
    with pytest.raises(ValueError, match="person_id 1"):
        R.tour_substitutes(trips, tour_ids, outside, implausible, leg_km, cells, allowed, 0.01, np.random.RandomState(0))


def test_apply_tour_modes_replaces_every_leg_of_the_tour_and_counts_the_sweep():
    trips, tour_ids, outside, implausible, leg_km, cells, allowed = _draw_fixture()
    subs = R.tour_substitutes(trips, tour_ids, outside, implausible, leg_km, cells, allowed, 0.01,
                              np.random.RandomState(1))
    subs["substitute"] = ["car", "pt"]
    out, n_remoded, n_swept = R.apply_tour_modes(trips, tour_ids, subs, implausible)
    assert out["mode"].tolist() == ["car", "car", "car", "walk", "walk", "pt", "pt"]
    assert n_remoded == 5 and n_swept == 1     # the 1 km shop leg of person 1 was plausible and is swept along
    assert trips["mode"].tolist()[0] == "walk"  # the input frame is not modified
