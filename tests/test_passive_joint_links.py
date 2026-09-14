"""Passive escort joint-location links (issue #385, ADR-0119): the escorted child's joint
activity is linked to the accompanying adult's activity inside the same synthetic household."""
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from braunschweig.synthesis.locations.passive_joint_links import (
    ANCHOR_COLUMNS, DEFAULT_UNRESOLVED_ANCHOR_WARNING_SHARE, LINK_COLUMNS,
    PASSIVE_LINKED_PURPOSE, SECONDARY_JOINT_PURPOSES, build_passive_joint_links,
    resolve_joint_anchors,
)
from braunschweig.synthesis.locations.secondary_chainsolvers.escort import (
    rewrite_anchored_activities,
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


@pytest.mark.parametrize("column", ["passive_pair_adult_p_id", "passive_pair_adult_w_id"])
def test_paired_leg_without_an_adult_id_raises_naming_the_column(column):
    """A row marked "paired" but carrying NaN in a pairing id is a phase-1 wiring
    defect, not a legitimate exclusion: it must raise a named error instead of the
    generic pandas cast error."""
    trips = _trips()
    trips.loc[(trips["person_id"] == 3) & (trips["trip_index"] == 0), column] = np.nan
    with pytest.raises(ValueError, match=column) as excinfo:
        build_passive_joint_links(_persons(), trips)
    message = str(excinfo.value)
    assert "1 paired passive leg" in message
    # The first offending row is named so the defect can be traced in the trips frame.
    assert "person_id 3" in message and "trip_index 0" in message


def _persons_sharing_one_plan_source():
    # Defensive case: persons 1 and 6 of household 10 carry the SAME plan source
    # (100, 1) -- e.g. a member-completion filler mirroring the donor adult -- so the
    # child's paired leg matches TWO adults.
    return pd.DataFrame({
        "person_id":    [1,   6,   2],
        "household_id": [10,  10,  10],
        "source_H_ID":  [100, 100, 100],
        "source_P_ID":  [1,   1,   2],
    })


def _trips_sharing_one_plan_source():
    nan = np.nan
    return pd.DataFrame({
        "person_id":               [1,      6,      2],
        "trip_index":              [0,      0,      0],
        "following_purpose":       ["shop", "shop", "shop"],
        "W_ID":                    [1,      1,      1],
        "passive_pair_status":     [nan,    nan,    "paired"],
        "passive_pair_adult_p_id": [nan,    nan,    1.0],
        "passive_pair_adult_w_id": [nan,    nan,    1.0],
    })


def test_child_activity_matching_two_adults_keeps_the_lower_adult_person_id():
    links, stats = build_passive_joint_links(_persons_sharing_one_plan_source(),
                                             _trips_sharing_one_plan_source())
    assert links[["child_person_id", "child_activity_index", "adult_person_id"]
                 ].values.tolist() == [[2, 1, 1]]
    assert stats["n_duplicate_dropped"] == 1
    assert stats["n_linked"] == 1
    # A duplicate plan source INFLATES the row count (one paired leg, two adult matches),
    # so the reason counts sum to the paired total PLUS the inflation, which is exactly
    # what n_duplicate_dropped counts here (see the Returns section of the docstring).
    reasons = (stats["n_linked"] + stats["n_adult_not_in_household"] + stats["n_adult_leg_missing"]
               + stats["n_purpose_not_secondary"] + stats["n_adult_is_linked_child"]
               + stats["n_duplicate_dropped"])
    assert reasons == stats["n_passive_paired"] + stats["n_duplicate_dropped"]


def test_no_linkable_paired_leg_is_logged_as_a_warning(caplog):
    """Fallback transparency: paired legs present but nothing linked means the pairing
    columns or the plan-source ids are broken, not that the data happens to say so."""
    persons = _persons()
    persons.loc[persons["person_id"] == 1, "source_P_ID"] = 7
    with caplog.at_level("INFO"):
        links, stats = build_passive_joint_links(persons, _trips())
    assert len(links) == 0 and stats["n_passive_paired"] > 0
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "no paired passive leg could be linked" in warnings[0].lower()


def test_a_linked_run_logs_the_rate_at_info(caplog):
    with caplog.at_level("INFO"):
        build_passive_joint_links(_persons(), _trips())
    assert [r.levelname for r in caplog.records] == ["INFO"]
    assert "2/3 paired passive legs linked" in caplog.records[0].message


def _links():
    return pd.DataFrame({
        "child_person_id":      [2, 3, 6],
        "child_activity_index": [1, 1, 2],
        "adult_person_id":      [1, 1, 7],
        "adult_activity_index": [1, 1, 3],
        "adult_purpose":        ["shop", "shop", "leisure"],
    })


def _pass1_locations():
    # Adult 1's shop was placed by the solver; adult 7 activity 3 was placed by the RDA
    # fallback (a synthesised sec_ id) -- both count as placed. Activity 5 of person 9 is
    # unrelated noise.
    return pd.DataFrame({
        "person_id":      [1,         7,           9],
        "activity_index": [1,         3,           5],
        "location_id":    ["shop_42", "sec_12345", "leis_1"],
        "geometry":       [Point(10, 10), Point(20, 20), Point(0, 0)],
    })


def test_resolve_joint_anchors_copies_the_adults_location_to_each_child():
    anchors, stats = resolve_joint_anchors(_links(), _pass1_locations())
    assert list(anchors.columns) == ANCHOR_COLUMNS
    assert anchors[["person_id", "activity_index", "location_id"]].values.tolist() == [
        [2, 1, "shop_42"], [3, 1, "shop_42"], [6, 2, "sec_12345"],
    ]
    assert anchors["geometry"].iloc[0] == Point(10, 10)
    assert anchors["geometry"].iloc[2] == Point(20, 20)
    assert stats == {"n_links": 3, "n_resolved": 3, "n_unresolved": 0}


def test_resolve_joint_anchors_counts_an_adult_activity_without_a_location():
    locations = _pass1_locations()
    locations = locations[locations["person_id"] != 7]
    anchors, stats = resolve_joint_anchors(_links(), locations)
    assert anchors["person_id"].tolist() == [2, 3]
    assert stats == {"n_links": 3, "n_resolved": 2, "n_unresolved": 1}


def test_resolve_joint_anchors_with_no_links_is_empty():
    anchors, stats = resolve_joint_anchors(_links().iloc[0:0], _pass1_locations())
    assert list(anchors.columns) == ANCHOR_COLUMNS and len(anchors) == 0
    assert stats == {"n_links": 0, "n_resolved": 0, "n_unresolved": 0}


def _child_trips():
    # child 2: home -> shop (activity 1) -> home; child 3: home -> shop -> leisure -> home
    return pd.DataFrame({
        "person_id":         [2,      2,      3,      3,         3],
        "trip_index":        [0,      1,      0,      1,         2],
        "preceding_purpose": ["home", "shop", "home", "shop",    "leisure"],
        "following_purpose": ["shop", "home", "shop", "leisure", "home"],
    }, index=[10, 11, 12, 13, 14])  # non-monotonic index on purpose


def test_rewrite_anchored_activities_marks_both_sides_regardless_of_purpose():
    anchors = pd.DataFrame({"person_id": [2, 3], "activity_index": [1, 2],
                            "location_id": ["a", "b"], "geometry": [Point(0, 0), Point(1, 1)]})
    out = rewrite_anchored_activities(_child_trips(), anchors, PASSIVE_LINKED_PURPOSE)
    # child 2 activity 1 = destination of trip 0, origin of trip 1
    assert out.loc[10, "following_purpose"] == PASSIVE_LINKED_PURPOSE
    assert out.loc[11, "preceding_purpose"] == PASSIVE_LINKED_PURPOSE
    # child 3 activity 2 (leisure) = destination of trip 1, origin of trip 2
    assert out.loc[13, "following_purpose"] == PASSIVE_LINKED_PURPOSE
    assert out.loc[14, "preceding_purpose"] == PASSIVE_LINKED_PURPOSE
    # untouched: child 3's shop activity (index 1) and every home
    assert out.loc[12, "following_purpose"] == "shop" and out.loc[13, "preceding_purpose"] == "shop"
    assert (out["preceding_purpose"] == "home").sum() == 2
    assert (out["following_purpose"] == "home").sum() == 2


def test_rewrite_anchored_activities_returns_a_copy_and_handles_no_anchors():
    trips = _child_trips()
    out = rewrite_anchored_activities(trips, pd.DataFrame(columns=ANCHOR_COLUMNS), PASSIVE_LINKED_PURPOSE)
    pd.testing.assert_frame_equal(out, trips)
    assert out is not trips


def test_rewrite_anchored_activities_ignores_an_anchor_for_a_person_without_trips():
    """An anchor whose person has no row in the pass frame (e.g. a child whose trips were
    routed to the other pass) matches nothing and leaves the frame unchanged."""
    trips = _child_trips()
    anchors = pd.DataFrame({"person_id": [99], "activity_index": [1],
                            "location_id": ["a"], "geometry": [Point(0, 0)]})
    out = rewrite_anchored_activities(trips, anchors, PASSIVE_LINKED_PURPOSE)
    pd.testing.assert_frame_equal(out, trips)
    assert out is not trips


def test_rewrite_anchored_activities_on_a_duplicate_index_touches_only_the_anchored_person():
    """A DUPLICATE row index must not leak the rewrite to an unrelated person.

    ``_anchored_side_masks`` returns POSITIONAL boolean masks. Indexing the frame's index
    with such a mask (``out.index[mask]``) turns the selected POSITIONS into LABELS, and
    ``.loc[labels]`` then hits EVERY row carrying those labels -- on a frame whose index
    repeats (two persons sharing the labels 0 and 1) that rewrites the other person's
    purposes as well. The sibling ``rewrite_linked_escort_trips`` passes the boolean mask
    to ``.loc`` directly, which stays positional. Not reachable in the pipeline today (the
    chainsolver's trips frame arrives with a unique index), so this guards robustness, not
    a live behaviour.
    """
    trips = pd.DataFrame({
        "person_id":         [2,      2,      3,         3],
        "trip_index":        [0,      1,      0,         1],
        "preceding_purpose": ["home", "shop", "home",    "leisure"],
        "following_purpose": ["shop", "home", "leisure", "home"],
    }, index=[0, 1, 0, 1])  # duplicate labels: person 2 and person 3 share 0 and 1
    anchors = pd.DataFrame({"person_id": [2], "activity_index": [1],
                            "location_id": ["a"], "geometry": [Point(0, 0)]})
    out = rewrite_anchored_activities(trips, anchors, PASSIVE_LINKED_PURPOSE)
    # Positional assertions: the labels are ambiguous, the positions are not.
    assert out["following_purpose"].tolist() == [
        PASSIVE_LINKED_PURPOSE, "home", "leisure", "home"]
    assert out["preceding_purpose"].tolist() == [
        "home", PASSIVE_LINKED_PURPOSE, "home", "leisure"]


def test_resolve_joint_anchors_logs_dropped_duplicate_placement_rows(caplog):
    # Feed locations with a duplicate (person_id, activity_index) key:
    # person 1 activity 1 appears twice with different locations
    locations = pd.DataFrame({
        "person_id":      [1,         1,         7],
        "activity_index": [1,         1,         3],
        "location_id":    ["shop_42", "shop_99", "sec_12345"],
        "geometry":       [Point(10, 10), Point(15, 15), Point(20, 20)],
    })
    with caplog.at_level("WARNING"):
        anchors, stats = resolve_joint_anchors(_links(), locations)
    # First row (shop_42) wins; second (shop_99) dropped
    assert anchors[["person_id", "activity_index", "location_id"]].values.tolist() == [
        [2, 1, "shop_42"], [3, 1, "shop_42"], [6, 2, "sec_12345"],
    ]
    # Verify warning was logged
    assert any("duplicate" in record.message.lower() for record in caplog.records
               if record.levelname == "WARNING")
    warning_msg = [r.message for r in caplog.records if "duplicate" in r.message.lower()]
    assert len(warning_msg) > 0
    # The line names the dropped count and the total placed rows separately.
    assert "1 of 3 placed pass-1 rows" in warning_msg[0]
    assert stats["n_links"] == 3


def test_resolve_joint_anchors_names_links_not_activities_in_the_unresolved_line(caplog):
    locations = _pass1_locations()
    locations = locations[locations["person_id"] != 7]
    with caplog.at_level("INFO"):
        resolve_joint_anchors(_links(), locations)
    messages = [r.message for r in caplog.records]
    assert len(messages) == 1
    # One unresolved LINK, not one unresolved adult activity.
    assert "1 links pointing at an adult activity without a placed location" in messages[0]
    assert caplog.records[0].levelname == "INFO"  # 1/3 unresolved is below the threshold


def test_resolve_joint_anchors_warns_exactly_at_the_unresolved_share_threshold(caplog):
    """Threshold convention: the escalation is ``>=``, like every sibling rate instrument
    of this stage (reporting._fallback_accounting_summary,
    reporting._excursion_boundary_clip_summary, the SrV marginal-fallback line), so a rate
    landing EXACTLY on the threshold warns instead of staying silent."""
    # Two links (adult 1 and adult 7); adult 7 unplaced -> 1 of 2 unresolved = exactly 0.5.
    links = _links().iloc[[0, 2]]
    locations = _pass1_locations()
    locations = locations[locations["person_id"] != 7]
    with caplog.at_level("INFO"):
        _anchors, stats = resolve_joint_anchors(links, locations)
    assert stats["n_unresolved"] / stats["n_links"] == DEFAULT_UNRESOLVED_ANCHOR_WARNING_SHARE
    assert [r.levelname for r in caplog.records] == ["WARNING"]


def test_resolve_joint_anchors_warns_above_the_unresolved_share_threshold(caplog):
    """Fallback transparency: at or above the threshold share of unresolved links the
    pass-1 output is probably incomplete, so the line escalates to WARNING."""
    assert DEFAULT_UNRESOLVED_ANCHOR_WARNING_SHARE == 0.5
    # Only adult 1 was placed -> link (6, 2) -> adult 7 stays unresolved: 1 of 3.
    # Drop adults 1 AND 7 -> 3 of 3 unresolved, above the threshold.
    locations = _pass1_locations()
    locations = locations[~locations["person_id"].isin([1, 7])]
    with caplog.at_level("INFO"):
        _anchors, stats = resolve_joint_anchors(_links(), locations)
    assert stats["n_unresolved"] == 3
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert len(warnings) == 1
    assert "pass-1 output is probably incomplete" in warnings[0]
