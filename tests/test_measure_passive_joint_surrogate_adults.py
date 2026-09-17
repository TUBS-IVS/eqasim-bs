"""Surrogate-adult headroom measurement for the passive joint location (issue #409, option 1).

Unit tests of the two pure selection functions behind
``scripts/measure_passive_joint_surrogate_adults.py``. They decide which unlinked passive
escort leg could be re-pointed at a DIFFERENT adult of the same synthetic household, so a
wrong join here would silently inflate or deflate the headroom the decision rests on.
"""
import numpy as np
import pandas as pd
import pytest

from scripts.measure_passive_joint_surrogate_adults import (
    CANDIDATE_COLUMNS, UNLINKED_COLUMNS, build_surrogate_candidates,
    summarise_headroom, summarise_inverse_anchor_headroom, summarise_issue_409_cut,
    unlinked_paired_legs,
)

NAN = np.nan


def _persons():
    """Four households.

    10: two adults (1, 2) and an escorted child (3).
    20: one adult (4) and a child (5) whose own activity is education.
    30: a child (6) whose only other household member (7) is a 16-year-old minor.
    40: one adult (8) and a child (9) used for the linked/unlinked set difference.
    """
    return pd.DataFrame({
        "person_id":    [1,  2,  3, 4,  5, 6, 7,  8,  9],
        "household_id": [10, 10, 10, 20, 20, 30, 30, 40, 40],
        "HP_ALTER":     [40, 38, 8, 45, 10, 6, 16, 50, 9],
    })


def _trips():
    """Departure times in seconds; activity ``i + 1`` is the destination of trip ``i``.

    Person 1 leaves to escort at 07:30, to shop at 08:05 and home at 10:00; person 2 to
    leisure at 09:00. Child 3's own passive leg departs at 08:00, so person 1's shop trip
    sits 5 minutes away and person 2's leisure trip 60 minutes away.
    """
    return pd.DataFrame({
        "person_id":                  [1,        1,      1,      2,         4,      5,           6,      7,      8,      9],
        "trip_index":                 [0,        1,      2,      0,         0,      0,           0,      0,      0,      0],
        "following_purpose":          ["escort", "shop", "home", "leisure", "shop", "education", "shop", "shop", "shop", "shop"],
        "departure_time":             [27000.0,  29100.0, 36000.0, 32400.0, 28800.0, 28800.0,    28800.0, 28920.0, 28800.0, 28860.0],
        "W_ID":                       [1,        2,      3,      4,         6,      7,           8,      9,      10,     11],
        "passive_pair_status":        [NAN,      NAN,    NAN,    NAN,       NAN,    "paired",    "paired", NAN,  NAN,    "paired"],
        "passive_pair_adult_p_id":    [NAN,      NAN,    NAN,    NAN,       NAN,    1.0,         1.0,    NAN,    NAN,    8.0],
        "passive_pair_adult_w_id":    [NAN,      NAN,    NAN,    NAN,       NAN,    99.0,        98.0,   NAN,    NAN,    10.0],
        "passive_pair_adult_w_zweck": [NAN,      NAN,    NAN,    NAN,       NAN,    11.0,        4.0,    NAN,    NAN,    4.0],
        "passive_pair_gap_minutes":   [NAN,      NAN,    NAN,    NAN,       NAN,    2.0,         3.0,    NAN,    NAN,    1.0],
    })


def _child_three_trips():
    """Child 3's own paired passive leg: shop at 08:00, donor adult P_ID 1 shopping."""
    return pd.DataFrame({
        "person_id":                  [3],
        "trip_index":                 [0],
        "following_purpose":          ["shop"],
        "departure_time":             [28800.0],
        "W_ID":                       [5],
        "passive_pair_status":        ["paired"],
        "passive_pair_adult_p_id":    [1.0],
        "passive_pair_adult_w_id":    [97.0],
        "passive_pair_adult_w_zweck": [4.0],
        "passive_pair_gap_minutes":   [3.0],
    })


def _all_trips():
    return pd.concat([_trips(), _child_three_trips()], ignore_index=True)


def _no_links():
    return pd.DataFrame({"child_person_id": pd.Series([], dtype="int64"),
                         "child_activity_index": pd.Series([], dtype="int64")})


# --------------------------------------------------------------------------- unlinked set

def test_unlinked_returns_every_paired_leg_when_nothing_is_linked():
    unlinked = unlinked_paired_legs(_all_trips(), _persons(), _no_links())
    assert list(unlinked.columns) == UNLINKED_COLUMNS
    assert sorted(unlinked["child_person_id"].tolist()) == [3, 5, 6, 9]


def test_unlinked_excludes_the_legs_already_linked():
    links = pd.DataFrame({"child_person_id": [9], "child_activity_index": [1]})
    unlinked = unlinked_paired_legs(_all_trips(), _persons(), links)
    assert sorted(unlinked["child_person_id"].tolist()) == [3, 5, 6]


def test_unlinked_carries_the_childs_own_activity_and_the_donor_adults_purpose():
    unlinked = unlinked_paired_legs(_all_trips(), _persons(), _no_links())
    row = unlinked.set_index("child_person_id").loc[3]
    assert row["child_activity_index"] == 1
    assert row["child_purpose"] == "shop"
    assert row["child_departure_time"] == pytest.approx(28800.0)
    assert row["household_id"] == 10
    # The donor adult's own W_ZWECK is read through the plain MiD purpose table: code 4 is
    # shopping, code 11 is school. This is the ADULT's purpose, so no passive-leg override
    # applies to it.
    assert row["donor_adult_purpose"] == "shop"
    assert unlinked.set_index("child_person_id").loc[5, "donor_adult_purpose"] == "education"


def test_unlinked_raises_when_a_paired_leg_has_no_person_row():
    persons = _persons()
    persons = persons[persons["person_id"] != 3]
    with pytest.raises(ValueError, match="absent from the persons frame"):
        unlinked_paired_legs(_all_trips(), persons, _no_links())


# ----------------------------------------------------------------------- candidate search

def _candidates():
    unlinked = unlinked_paired_legs(_all_trips(), _persons(), _no_links())
    return build_surrogate_candidates(unlinked, _persons(), _all_trips())


def test_candidate_rows_have_the_documented_shape():
    candidates, _stats = _candidates()
    assert list(candidates.columns) == CANDIDATE_COLUMNS


def test_every_secondary_activity_of_every_household_adult_is_a_candidate():
    candidates, _stats = _candidates()
    child_three = candidates[candidates["child_person_id"] == 3]
    # Person 1's shop trip and person 2's leisure trip; person 1's escort and home trips
    # are NOT secondary and must not appear.
    assert sorted(child_three["adult_person_id"].tolist()) == [1, 2]
    assert sorted(child_three["adult_purpose"].tolist()) == ["leisure", "shop"]


def test_the_gap_is_the_absolute_departure_difference_in_minutes():
    candidates, _stats = _candidates()
    by_adult = candidates[candidates["child_person_id"] == 3].set_index("adult_person_id")
    assert by_adult.loc[1, "gap_minutes"] == pytest.approx(5.0)
    assert by_adult.loc[2, "gap_minutes"] == pytest.approx(60.0)


def test_the_child_is_never_its_own_candidate():
    candidates, _stats = _candidates()
    assert not (candidates["adult_person_id"] == candidates["child_person_id"]).any()


def test_a_child_whose_own_activity_is_not_secondary_is_ineligible():
    candidates, stats = _candidates()
    # Child 5 goes to its own education activity; anchoring it on an adult is meaningless,
    # so it yields no candidate row and is counted as ineligible.
    assert candidates[candidates["child_person_id"] == 5].empty
    assert stats["n_ineligible_child_purpose"] == 1


def test_a_minor_is_not_an_eligible_surrogate_adult():
    candidates, stats = _candidates()
    # Household 30 holds only the 16-year-old person 7, below the 18-year threshold.
    assert candidates[candidates["child_person_id"] == 6].empty
    assert stats["n_eligible_without_candidate"] == 1


def test_adult_min_age_is_configurable():
    unlinked = unlinked_paired_legs(_all_trips(), _persons(), _no_links())
    candidates, _stats = build_surrogate_candidates(
        unlinked, _persons(), _all_trips(), adult_min_age=16)
    assert candidates[candidates["child_person_id"] == 6]["adult_person_id"].tolist() == [7]


def test_the_escort_flag_marks_adults_who_escort_somebody_that_day():
    candidates, _stats = _candidates()
    by_adult = candidates[candidates["child_person_id"] == 3].set_index("adult_person_id")
    assert bool(by_adult.loc[1, "adult_escorts"]) is True
    assert bool(by_adult.loc[2, "adult_escorts"]) is False


def test_the_donor_purpose_match_compares_against_the_original_paired_adult():
    candidates, _stats = _candidates()
    by_adult = candidates[candidates["child_person_id"] == 3].set_index("adult_person_id")
    # The donor adult was shopping (W_ZWECK 4): person 1 shops, person 2 does leisure.
    assert bool(by_adult.loc[1, "purpose_matches_donor"]) is True
    assert bool(by_adult.loc[2, "purpose_matches_donor"]) is False


# ------------------------------------------------------------------- headroom summaries

def test_headroom_counts_legs_not_candidate_rows():
    candidates, _stats = _candidates()
    summary = summarise_headroom(candidates, unlinked_paired_legs(
        _all_trips(), _persons(), _no_links()), gap_minutes=(15.0,))
    wide = summary.set_index("restriction").loc["any_secondary_adult"]
    # Child 3 has TWO surrogates but only one is within 15 minutes; child 9 has one at
    # 1 minute. Two legs, not three candidate rows.
    assert wide["n_legs_reached"] == 2
    assert wide["n_unlinked"] == 4


def test_headroom_restrictions_narrow_monotonically():
    candidates, _stats = _candidates()
    summary = summarise_headroom(candidates, unlinked_paired_legs(
        _all_trips(), _persons(), _no_links()), gap_minutes=(30.0,))
    reached = summary.set_index("restriction")["n_legs_reached"]
    assert reached["any_secondary_adult"] >= reached["same_purpose_as_child"]
    # same_purpose_as_child and adult_also_escorts are SIBLING restrictions of
    # any_secondary_adult, not nested in each other, so this ordering is observed on this
    # fixture, not structural -- a differently shaped fixture could reverse it.
    assert reached["same_purpose_as_child"] >= reached["adult_also_escorts"]
    assert reached["adult_also_escorts"] >= reached["and_purpose_matches_donor"]


def test_headroom_same_purpose_as_child_restriction_reaches_both_legs_at_15_min():
    candidates, _stats = _candidates()
    summary = summarise_headroom(candidates, unlinked_paired_legs(
        _all_trips(), _persons(), _no_links()), gap_minutes=(15.0,))
    same_purpose = summary.set_index("restriction").loc["same_purpose_as_child"]
    # Child 3's only candidate within 15 min is person 1's shop trip (5 min away, same
    # "shop" purpose as the child); person 2's leisure candidate (60 min away, a different
    # purpose) is already excluded by the gap at BOTH 15 and 30 min in this fixture, so it
    # cannot distinguish the two restrictions here. Child 9's only candidate is person 8's
    # shop trip (1 min away, also "shop"). Both legs survive the same-purpose restriction,
    # so it reaches the same 2 legs as "any_secondary_adult" at this gap -- verified by
    # running, not assumed.
    assert same_purpose["n_legs_reached"] == 2
    assert same_purpose["n_unlinked"] == 4


def test_the_issue_cut_is_wider_because_it_ignores_the_childs_own_purpose():
    unlinked = unlinked_paired_legs(_all_trips(), _persons(), _no_links())
    candidates, _stats = build_surrogate_candidates(unlinked, _persons(), _all_trips())
    decision = summarise_headroom(candidates, unlinked, gap_minutes=(30.0,))
    issue = summarise_issue_409_cut(unlinked, _persons(), _all_trips(), gap_minutes=(30.0,))
    decision_reach = int(decision.set_index("restriction")
                         .loc["any_secondary_adult", "n_legs_reached"])
    issue_reach = int(issue.set_index("issue_cut")
                      .loc["any_secondary_adult__child_purpose_ignored", "n_legs_reached"])
    # Child 5's education leg has adult 4 shopping at the same minute in household 20: the
    # issue's cut counts it, the decision-relevant cut does not, because anchoring a child
    # at its own school on an adult cannot change a realised location.
    assert issue_reach == decision_reach + 1


def test_the_issue_cut_pairs_on_the_escort_trip_which_cannot_carry_an_anchor():
    unlinked = unlinked_paired_legs(_all_trips(), _persons(), _no_links())
    issue = summarise_issue_409_cut(unlinked, _persons(), _all_trips(), gap_minutes=(60.0,))
    row = issue.set_index("issue_cut").loc["escorting_adult__escort_trip_as_pair"]
    # Person 1 escorts at 07:30, 30 minutes before child 3's own 08:00 departure.
    assert row["n_legs_reached"] == 1
    assert row["median_gap_minutes"] == pytest.approx(30.0)


# ---------------------------------------------------------------- inverse anchor (#201)

def test_inverse_anchor_headroom_measures_the_201_direction_frequency():
    unlinked = unlinked_paired_legs(_all_trips(), _persons(), _no_links())
    summary = summarise_inverse_anchor_headroom(unlinked, _persons(), _all_trips(),
                                                gap_minutes=(15.0, 30.0))
    by_threshold = summary.set_index("gap_minutes_max")
    # Child 3 is eligible (its own activity is "shop") and its household adult (person 1)
    # escorts at 07:30, 30 minutes before the child's own 08:00 departure: 0 reached at 15
    # min, 1 at 30 min, with a 30-minute median gap for the sole pair.
    assert by_threshold.loc[15.0, "n_legs_reached"] == 0
    assert by_threshold.loc[30.0, "n_legs_reached"] == 1
    assert by_threshold.loc[30.0, "median_gap_minutes"] == pytest.approx(30.0)
    # n_eligible (3: children 3, 6, 9 -- shop) and n_unlinked (4: also child 5, education)
    # are carried on every row so both denominators sit next to the count.
    assert (summary["n_eligible"] == 3).all()
    assert (summary["n_unlinked"] == 4).all()


def test_inverse_anchor_headroom_excludes_an_ineligible_childs_own_activity():
    unlinked = unlinked_paired_legs(_all_trips(), _persons(), _no_links())
    summary = summarise_inverse_anchor_headroom(unlinked, _persons(), _all_trips(),
                                                gap_minutes=(60.0,))
    # Child 5's own activity is education, so it is excluded from the eligible set before
    # the escort join ever runs -- household 20's only adult (person 4) does not escort
    # anyway, so this also cannot inflate the count even if the exclusion were missing, but
    # the eligibility filter is the thing under test here, not that incidental fact.
    assert int(summary.loc[0, "n_legs_reached"]) == 1
    assert int(summary.loc[0, "n_eligible"]) == 3


def test_the_statistics_account_for_every_unlinked_leg_exactly_once():
    _candidates_frame, stats = _candidates()
    assert stats["n_unlinked"] == 4
    assert (stats["n_ineligible_child_purpose"]
            + stats["n_eligible_with_candidate"]
            + stats["n_eligible_without_candidate"]) == stats["n_unlinked"]
    # Children 3 and 9 shop, child 5 goes to education, child 6 has no adult at home.
    assert stats["n_eligible_child_purpose"] == 3
    assert stats["n_eligible_with_candidate"] == 2
    assert stats["n_candidate_rows"] == 3


from scripts.measure_passive_joint_surrogate_adults import sibling_age_histogram  # noqa: E402


def _wege_two_households():
    """MiD-shaped legs. Household 1: child (7 y) taken along at 08:00, the only adult leaves
    at 12:00 (240 min away), a 15-year-old sibling shops at 08:05. Household 2: child (6 y)
    at 08:00 and an adult shopping at 08:02."""
    return pd.DataFrame({
        "H_ID":     [1,  1,  1,  2,  2],
        "P_ID":     [1,  2,  3,  1,  2],
        "W_ID":     [1,  2,  3,  4,  5],
        "W_ZWECK":  [1,  13, 4,  4,  13],
        "W_SZS":    [12, 8,  8,  8,  8],
        "W_SZM":    [0,  0,  5,  2,  0],
        "HP_ALTER": [40, 7,  15, 38, 6],
    })


def test_sibling_age_histogram_reports_the_marginal_gain_per_floor():
    table = sibling_age_histogram(_wege_two_households(), floors=(16, 14), max_gap_minutes=15.0)
    by_floor = table.set_index("floor_years")
    assert by_floor.loc[18, "n_passive_minor_legs"] == 2
    assert by_floor.loc[18, "n_paired_at_floor"] == 1                 # household 2 only
    assert by_floor.loc[16, "n_newly_paired_vs_reference"] == 0
    assert by_floor.loc[14, "n_newly_paired_vs_reference"] == 1       # the 15-year-old sibling
    assert by_floor.loc[14, "share_of_unpaired_at_reference"] == pytest.approx(1.0)
    assert by_floor.loc[14, "partner_age_histogram"] == "15:1"


def test_sibling_age_histogram_rejects_a_floor_at_or_above_the_reference():
    """M9: a floor could never describe a leg newly paired relative to the reference if it
    sits at or above that reference floor -- named, not silently accepted."""
    with pytest.raises(ValueError, match="reference_floor"):
        sibling_age_histogram(_wege_two_households(), floors=(16, 18), reference_floor=18)
    with pytest.raises(ValueError, match="20"):
        sibling_age_histogram(_wege_two_households(), floors=(16, 20), reference_floor=18)
