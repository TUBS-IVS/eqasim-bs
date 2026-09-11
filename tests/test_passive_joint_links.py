"""Passive escort joint-location links (issue #385, ADR-0118): the escorted child's joint
activity is linked to the accompanying adult's activity inside the same synthetic household."""
import numpy as np
import pandas as pd
import pytest

from braunschweig.synthesis.locations.passive_joint_links import (
    LINK_COLUMNS, SECONDARY_JOINT_PURPOSES, build_passive_joint_links,
)


def _persons():
    # Household 10: adult 1 (donor P_ID 1), child 2 (donor P_ID 2), child 3 (donor P_ID 3);
    # household 20: adult 4 whose plan source is a DIFFERENT donor household (a completion
    # filler mirrors donor 900/1); household 30: child 5 alone (its donor adult was not copied).
    return pd.DataFrame({
        "person_id":    [1,   2,   3,   4,    5],
        "household_id": [10,  10,  10,  20,   30],
        "source_H_ID":  [100, 100, 100, 900,  300],
        "source_P_ID":  [1,   2,   3,   1,    2],
    })


def _trips():
    # person 1 (adult): home -> shop (W_ID 1) -> home (W_ID 2)
    # person 2 (child): passive leg paired with adult 1, W_ID 1 -> shop; then home
    # person 3 (child): passive leg paired with adult 1, W_ID 1 -> shop (second child, same activity)
    # person 4 (adult, hh 20): shop leg, but nobody pairs with it
    # person 5 (child, hh 30): paired with donor adult P_ID 1 who is NOT in household 30
    nan = np.nan
    return pd.DataFrame({
        "person_id":               [1,      1,      2,        2,      3,        4,      5],
        "trip_index":              [0,      1,      0,        1,      0,        0,      0],
        "following_purpose":       ["shop", "home", "shop",   "home", "shop",   "shop", "shop"],
        "W_ID":                    [1,      2,      1,        2,      1,        1,      1],
        "passive_pair_status":     [nan,    nan,    "paired", nan,    "paired", nan,    "paired"],
        "passive_pair_adult_p_id": [nan,    nan,    1.0,      nan,    1.0,      nan,    1.0],
        "passive_pair_adult_w_id": [nan,    nan,    1.0,      nan,    1.0,      nan,    1.0],
    })


def test_link_columns_and_secondary_vocabulary():
    assert LINK_COLUMNS == ["child_person_id", "child_activity_index",
                            "adult_person_id", "adult_activity_index", "adult_purpose"]
    assert SECONDARY_JOINT_PURPOSES == frozenset({"shop", "leisure", "other"})


def test_two_children_link_to_the_same_adult_activity():
    links, stats = build_passive_joint_links(_persons(), _trips())
    assert list(links.columns) == LINK_COLUMNS
    assert links[["child_person_id", "child_activity_index",
                  "adult_person_id", "adult_activity_index"]].values.tolist() == [
        [2, 1, 1, 1],
        [3, 1, 1, 1],
    ]
    assert set(links["adult_purpose"]) == {"shop"}
    assert stats["n_passive_paired"] == 3
    assert stats["n_linked"] == 2
    assert stats["n_adult_not_in_household"] == 1  # child 5
    assert stats["link_rate"] == pytest.approx(2 / 3)


def test_stats_reasons_sum_to_the_paired_total():
    _links, stats = build_passive_joint_links(_persons(), _trips())
    reasons = (stats["n_linked"] + stats["n_adult_not_in_household"] + stats["n_adult_leg_missing"]
               + stats["n_purpose_not_secondary"] + stats["n_adult_is_linked_child"]
               + stats["n_duplicate_dropped"])
    assert reasons == stats["n_passive_paired"]


def test_same_household_but_different_plan_source_is_not_linked():
    persons = _persons()
    # The adult in household 10 now executes a remapped diary (another donor person).
    persons.loc[persons["person_id"] == 1, "source_P_ID"] = 7
    links, stats = build_passive_joint_links(persons, _trips())
    assert len(links) == 0
    assert stats["n_adult_not_in_household"] == 3


def test_adult_present_but_paired_leg_missing_is_counted():
    trips = _trips()
    # The adult's shop leg was dropped (e.g. a spliced day); only the home leg remains.
    trips = trips[~((trips["person_id"] == 1) & (trips["W_ID"] == 1))]
    links, stats = build_passive_joint_links(_persons(), trips)
    assert len(links) == 0
    assert stats["n_adult_in_household"] == 2
    assert stats["n_adult_leg_missing"] == 2


@pytest.mark.parametrize("adult_purpose", ["home", "work", "education", "escort"])
def test_adult_activity_with_a_non_secondary_purpose_is_excluded(adult_purpose):
    trips = _trips()
    trips.loc[(trips["person_id"] == 1) & (trips["W_ID"] == 1), "following_purpose"] = adult_purpose
    links, stats = build_passive_joint_links(_persons(), trips)
    assert len(links) == 0
    assert stats["n_purpose_not_secondary"] == 2


def test_child_activity_with_a_non_secondary_purpose_is_excluded():
    trips = _trips()
    trips.loc[(trips["person_id"] == 2) & (trips["trip_index"] == 0), "following_purpose"] = "education"
    links, stats = build_passive_joint_links(_persons(), trips)
    assert links["child_person_id"].tolist() == [3]
    assert stats["n_purpose_not_secondary"] == 1


def test_unpaired_passive_rows_are_ignored():
    trips = _trips()
    trips.loc[trips["person_id"] == 3, "passive_pair_status"] = "unpaired_gap"
    links, stats = build_passive_joint_links(_persons(), trips)
    assert links["child_person_id"].tolist() == [2]
    assert stats["n_passive_paired"] == 2


def test_adult_that_is_itself_a_linked_child_is_dropped():
    persons = _persons()
    trips = _trips()
    # Make adult 1 a paired passive traveller of person 2's leg W_ID 2 (a cycle 1 <-> 2).
    trips.loc[(trips["person_id"] == 1) & (trips["W_ID"] == 1), "passive_pair_status"] = "paired"
    trips.loc[(trips["person_id"] == 1) & (trips["W_ID"] == 1), "passive_pair_adult_p_id"] = 2.0
    trips.loc[(trips["person_id"] == 1) & (trips["W_ID"] == 1), "passive_pair_adult_w_id"] = 1.0
    links, stats = build_passive_joint_links(persons, trips)
    assert stats["n_adult_is_linked_child"] >= 1
    # No link may point from a child to an adult that is itself a linked child.
    children = set(links["child_person_id"])
    assert not set(links["adult_person_id"]) & children


def test_no_paired_rows_returns_empty_table_with_nan_rate():
    trips = _trips()
    trips["passive_pair_status"] = np.nan
    links, stats = build_passive_joint_links(_persons(), trips)
    assert list(links.columns) == LINK_COLUMNS and len(links) == 0
    assert stats["n_passive_paired"] == 0 and np.isnan(stats["link_rate"])


def test_missing_required_column_raises_naming_it():
    with pytest.raises(ValueError, match="source_P_ID"):
        build_passive_joint_links(_persons().drop(columns=["source_P_ID"]), _trips())
    with pytest.raises(ValueError, match="passive_pair_adult_w_id"):
        build_passive_joint_links(_persons(), _trips().drop(columns=["passive_pair_adult_w_id"]))


def test_child_missing_from_persons_frame_raises():
    persons = _persons()
    persons = persons[persons["person_id"] != 2]
    with pytest.raises(ValueError, match="person_id"):
        build_passive_joint_links(persons, _trips())
