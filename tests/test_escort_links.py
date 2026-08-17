"""Household escort-link table (issue #201, Phase 2)."""
import pandas as pd
import pytest
from shapely.geometry import Point

from braunschweig.synthesis.locations.escort_links import build_escort_links


def _persons():
    return pd.DataFrame({
        "person_id":    [1, 2, 3, 4, 5],
        "household_id": [10, 10, 10, 20, 30],
        "HP_ALTER":     [40, 6, 9, 35, 50],
    })


def _education():
    return pd.DataFrame({
        "person_id": [2, 3],
        "location_id": ["edu_7", "edu_8"],
        "geometry": [Point(5, 5), Point(6, 6)],
    })


def _trips():
    return pd.DataFrame({
        "person_id":        [1, 1, 4, 5],
        "following_purpose": ["escort", "home", "escort", "shop"],
    })


def test_links_list_all_children_youngest_first():
    links, stats = build_escort_links(_persons(), _education(), _trips())
    # person 1 (household 10) links BOTH children, youngest first.
    assert len(links) == 2
    assert list(links.columns) == ["person_id", "child_rank", "location_id", "geometry"]
    assert list(links["person_id"]) == [1, 1]
    assert list(links["child_rank"]) == [0, 1]
    assert list(links["location_id"]) == ["edu_7", "edu_8"]  # age 6 before age 9
    # person 4 escorts but household 20 has no education-assigned child -> unlinked
    assert stats["n_escorters"] == 2
    assert stats["n_linked"] == 1
    assert stats["n_child_links"] == 2
    assert stats["link_rate"] == pytest.approx(0.5)


def test_links_respect_max_child_age():
    persons = _persons()
    persons.loc[persons["person_id"] == 2, "HP_ALTER"] = 25  # adult student
    persons.loc[persons["person_id"] == 3, "HP_ALTER"] = 30
    links, stats = build_escort_links(persons, _education(), _trips())
    assert len(links) == 0 and stats["n_linked"] == 0


def test_links_never_link_escorter_to_self():
    persons = pd.DataFrame({
        "person_id": [2], "household_id": [10], "HP_ALTER": [10],
    })
    education = _education()
    trips = pd.DataFrame({"person_id": [2], "following_purpose": ["escort"]})
    links, stats = build_escort_links(persons, education, trips)
    assert len(links) == 0


def test_no_escort_trips_returns_empty_table():
    """Flag-OFF (or no escort activities): empty link table, link_rate NaN,
    no household join is attempted."""
    trips = pd.DataFrame({
        "person_id": [1, 5], "following_purpose": ["home", "shop"],
    })
    links, stats = build_escort_links(_persons(), _education(), trips)
    assert list(links.columns) == ["person_id", "child_rank", "location_id", "geometry"]
    assert len(links) == 0
    assert stats["n_escorters"] == 0 and stats["n_linked"] == 0 and stats["n_child_links"] == 0
    assert pd.isna(stats["link_rate"])


def test_missing_required_person_column_raises():
    """A persons frame without the household-link inputs must fail loudly
    rather than silently produce an empty table."""
    persons = _persons().drop(columns=["HP_ALTER"])
    with pytest.raises(ValueError, match="HP_ALTER"):
        build_escort_links(persons, _education(), _trips())


# --- Task 12: escort_linked fixed-purpose boundary in the problem splitter ---
from shapely.geometry import Point as _P
import synthesis.population.spatial.secondary.problems as problems_mod


def _trips_frame(rows):
    return pd.DataFrame(rows, columns=[
        "person_id", "trip_index", "preceding_purpose", "following_purpose",
        "mode", "travel_time",
    ])


def test_escort_linked_is_a_fixed_purpose_boundary():
    df = _trips_frame([
        (1, 0, "home", "escort_linked", "car", 600.0),
        (1, 1, "escort_linked", "shop", "car", 600.0),
        (1, 2, "shop", "home", "car", 600.0),
    ])
    df_locations = pd.DataFrame({
        "person_id": [1],
        "home": [_P(0, 0)], "work": [_P(1, 1)], "education": [_P(2, 2)],
        "escort_linked": [_P(5, 5)],
    })
    problems = list(problems_mod.find_assignment_problems(df, df_locations))
    # Chain splits at the escort anchor: [home->escort_linked] has no variable
    # activity (skipped), [escort_linked->shop->home] anchors origin at (5,5).
    assert len(problems) == 1
    p = problems[0]
    assert p["purposes"] == ["shop"]
    assert p["origin"][0][0] == 5.0 and p["origin"][0][1] == 5.0


def test_problems_without_escort_linked_column_unchanged():
    df = _trips_frame([
        (1, 0, "home", "shop", "car", 600.0),
        (1, 1, "shop", "home", "car", 600.0),
    ])
    df_locations = pd.DataFrame({
        "person_id": [1], "home": [_P(0, 0)], "work": [_P(1, 1)], "education": [_P(2, 2)],
    })
    problems = list(problems_mod.find_assignment_problems(df, df_locations))
    assert len(problems) == 1
    assert problems[0]["purposes"] == ["shop"]


# --- Task 13: chainsolver + facilities integration of the household link ------
def test_rewrite_marks_only_anchored_activities():
    """Anchored activities become escort_linked on BOTH trip sides; overflow
    escort activities (beyond the household's children) and unlinked persons
    keep the plain escort purpose (SrV draw path)."""
    trips = pd.DataFrame({
        "person_id":         [1, 1, 1, 2],
        "trip_index":        [0, 1, 2, 0],
        "preceding_purpose": ["home", "escort", "escort", "home"],
        "following_purpose": ["escort", "escort", "home", "escort"],
    })
    links = _links_frame([(1, 0, "edu_7", _P(5, 5))])  # ONE child
    anchors, _ = assign_escort_anchors(trips, links)   # activity 1 anchored, 2 overflow
    from braunschweig.synthesis.locations.secondary_chainsolvers import (
        rewrite_linked_escort_trips,
    )
    out = rewrite_linked_escort_trips(trips, anchors)
    person_1 = out[out["person_id"] == 1]
    assert list(person_1["following_purpose"]) == ["escort_linked", "escort", "home"]
    assert list(person_1["preceding_purpose"]) == ["home", "escort_linked", "escort"]
    # unlinked person 2 keeps the plain escort purpose (draw path)
    assert list(out.loc[out["person_id"] == 2, "following_purpose"]) == ["escort"]
    # the input frame is NOT mutated
    assert "escort_linked" not in set(trips["following_purpose"])


def test_multi_child_pieces_stay_consistent_end_to_end():
    """The rewrite, the anchor table, and the location rows all derive from
    ONE assign_escort_anchors result: every escort_linked boundary the rewrite
    creates must resolve in the anchor table, and the onward chain must anchor
    at the SECOND child's school."""
    trips = pd.DataFrame({
        "person_id":         [1, 1, 1, 1],
        "trip_index":        [0, 1, 2, 3],
        "preceding_purpose": ["home", "escort", "escort", "shop"],
        "following_purpose": ["escort", "escort", "shop", "home"],
        "mode":              ["car"] * 4,
        "travel_time":       [600.0] * 4,
    })
    links = _links_frame([(1, 0, "edu_7", _P(5, 5)), (1, 1, "edu_8", _P(6, 6))])
    anchors_df, _ = assign_escort_anchors(trips, links)
    from braunschweig.synthesis.locations.secondary_chainsolvers import (
        rewrite_linked_escort_trips,
    )
    rewritten = rewrite_linked_escort_trips(trips, anchors_df)
    anchors = {
        (row.person_id, row.activity_index): row.geometry
        for row in anchors_df.itertuples(index=False)
    }
    df_locations = pd.DataFrame({
        "person_id": [1],
        "home": [_P(0, 0)], "work": [_P(1, 1)], "education": [_P(2, 2)],
    })
    problems = list(problems_mod.find_assignment_problems(
        rewritten, df_locations, activity_anchors=anchors))
    # Only [escort_linked@2 -> shop -> home] has a variable activity; its
    # origin is the SECOND child's school.
    assert len(problems) == 1
    assert problems[0]["purposes"] == ["shop"]
    assert problems[0]["origin"][0][0] == 6.0 and problems[0]["origin"][0][1] == 6.0


def test_validate_secondary_coverage_accepts_extra_valid_ids():
    from braunschweig.matsim.scenario.facilities import validate_secondary_coverage
    realised = pd.DataFrame({"location_id": ["sec_1", "edu_7"]})
    secondary = pd.DataFrame({"location_id": ["sec_1"]})
    with pytest.raises(RuntimeError):
        validate_secondary_coverage(realised, secondary)
    validate_secondary_coverage(realised, secondary, extra_valid_ids={"edu_7"})


# --- Multi-child anchoring: consecutive-run rule (issue #201 follow-up) ------
from braunschweig.synthesis.locations.escort_links import assign_escort_anchors


def _links_frame(rows):
    return pd.DataFrame(rows, columns=["person_id", "child_rank", "location_id", "geometry"])


def _chain(person_id, purposes):
    """Trips frame for one person's CONSISTENT activity chain."""
    rows = [
        (person_id, trip_index, purposes[trip_index], purposes[trip_index + 1])
        for trip_index in range(len(purposes) - 1)
    ]
    return pd.DataFrame(rows, columns=[
        "person_id", "trip_index", "preceding_purpose", "following_purpose",
    ])


def test_bring_fetch_reuses_the_same_child():
    """Single-child regression guard: NON-consecutive escort activities
    (bring ... fetch) anchor at the same school, exactly as before the fix."""
    trips = _chain(1, ["home", "escort", "work", "escort", "home"])
    links = _links_frame([(1, 0, "edu_7", _P(5, 5))])
    anchors, stats = assign_escort_anchors(trips, links)
    assert sorted(anchors["activity_index"]) == [1, 3]
    assert set(anchors["location_id"]) == {"edu_7"}
    assert stats == {
        "n_escort_activities": 2, "n_anchored": 2,
        "n_overflow_to_draw": 0, "n_runs": 2,
    }


def test_consecutive_run_assigns_distinct_children():
    """Multi-drop chain home->school->school->work: each drop a DIFFERENT
    child, youngest first."""
    trips = _chain(1, ["home", "escort", "escort", "work"])
    links = _links_frame([(1, 0, "edu_7", _P(5, 5)), (1, 1, "edu_8", _P(6, 6))])
    anchors, stats = assign_escort_anchors(trips, links)
    by_activity = anchors.set_index("activity_index")["location_id"]
    assert by_activity[1] == "edu_7" and by_activity[2] == "edu_8"
    assert stats["n_overflow_to_draw"] == 0 and stats["n_runs"] == 1


def test_single_child_multi_drop_overflows_to_draw():
    """THE artifact fix: with one child, the second consecutive drop is NOT
    anchored (falls back to the SrV draw) instead of collapsing onto the
    same point. Never cycles back to child 0."""
    trips = _chain(1, ["home", "escort", "escort", "work"])
    links = _links_frame([(1, 0, "edu_7", _P(5, 5))])
    anchors, stats = assign_escort_anchors(trips, links)
    assert list(anchors["activity_index"]) == [1]
    assert stats == {
        "n_escort_activities": 2, "n_anchored": 1,
        "n_overflow_to_draw": 1, "n_runs": 1,
    }


def test_separate_runs_restart_at_the_youngest_child():
    """Bring/fetch pairing: the second run reuses the same children in the
    same order (documented assumption: chains visit children youngest-first)."""
    trips = _chain(1, ["home", "escort", "escort", "work", "escort", "escort", "home"])
    links = _links_frame([(1, 0, "edu_7", _P(5, 5)), (1, 1, "edu_8", _P(6, 6))])
    anchors, stats = assign_escort_anchors(trips, links)
    by_activity = anchors.set_index("activity_index")["location_id"]
    assert by_activity[1] == "edu_7" and by_activity[2] == "edu_8"
    assert by_activity[4] == "edu_7" and by_activity[5] == "edu_8"
    assert stats["n_runs"] == 2 and stats["n_overflow_to_draw"] == 0


def test_origin_only_escort_activity_enters_the_assignment():
    """Regression (5% server run 2026-08-10, ported from the superseded
    anchored_escort_location_rows): an escort activity that appears ONLY as a
    trip ORIGIN (donor-chain inconsistency, 0.027% of links) must still be
    enumerated. Chain mirrors person 769474: trip 5 arrives ``home`` but trip
    6 departs from ``escort`` -> activity 6 is escort as an ORIGIN only.
    Escort activities: 1, 4, 5, 6 -> runs [1] and [4, 5, 6]; ONE child =>
    activities 1 and 4 anchored, 5 and 6 overflow."""
    trips = pd.DataFrame({
        "person_id":         [1, 1, 1, 1, 1, 1, 1],
        "trip_index":        [0, 1, 2, 3, 4, 5, 6],
        "preceding_purpose": ["home", "escort", "home", "shop", "escort", "escort", "escort"],
        "following_purpose": ["escort", "home", "shop", "escort", "escort", "home", "home"],
    })
    links = _links_frame([(1, 0, "edu_7", _P(5, 5))])
    anchors, stats = assign_escort_anchors(trips, links)
    assert sorted(anchors["activity_index"]) == [1, 4]
    assert stats["n_escort_activities"] == 4
    assert stats["n_overflow_to_draw"] == 2


def test_each_anchored_activity_appears_exactly_once():
    """An escort activity in a consistent chain is both the destination of the
    previous trip and the origin of the next one; it must be emitted once."""
    trips = _chain(1, ["home", "escort", "escort", "home"])
    links = _links_frame([(1, 0, "edu_7", _P(5, 5)), (1, 1, "edu_8", _P(6, 6))])
    anchors, _ = assign_escort_anchors(trips, links)
    assert not anchors.duplicated(subset=["person_id", "activity_index"]).any()
    assert sorted(anchors["activity_index"]) == [1, 2]


def test_unlinked_persons_are_ignored():
    """Escort trips of persons WITHOUT a link row contribute neither anchors
    nor stats (their activities stay on the draw path; the link rate already
    reports them)."""
    trips = pd.concat([
        _chain(1, ["home", "escort", "home"]),
        _chain(2, ["home", "escort", "home"]),
    ], ignore_index=True)
    links = _links_frame([(1, 0, "edu_7", _P(5, 5))])
    anchors, stats = assign_escort_anchors(trips, links)
    assert set(anchors["person_id"]) == {1}
    assert stats["n_escort_activities"] == 1 and stats["n_anchored"] == 1


def test_empty_links_produce_empty_anchor_table():
    trips = _chain(1, ["home", "escort", "home"])
    links = _links_frame([])
    anchors, stats = assign_escort_anchors(trips, links)
    assert list(anchors.columns) == ["person_id", "activity_index", "location_id", "geometry"]
    assert len(anchors) == 0
    assert stats == {
        "n_escort_activities": 0, "n_anchored": 0,
        "n_overflow_to_draw": 0, "n_runs": 0,
    }


# --- Per-activity anchor table in the problem splitter (issue #201 follow-up) --


def test_anchor_table_resolves_origin_boundary_per_activity():
    """Two consecutive escort_linked activities at DIFFERENT schools: the
    onward chain's origin must be activity 2's anchor (9, 9), which the old
    one-column-per-person lookup cannot represent."""
    df = _trips_frame([
        (1, 0, "home", "escort_linked", "car", 600.0),
        (1, 1, "escort_linked", "escort_linked", "car", 600.0),
        (1, 2, "escort_linked", "shop", "car", 600.0),
        (1, 3, "shop", "home", "car", 600.0),
    ])
    df_locations = pd.DataFrame({
        "person_id": [1],
        "home": [_P(0, 0)], "work": [_P(1, 1)], "education": [_P(2, 2)],
    })
    anchors = {(1, 1): _P(5, 5), (1, 2): _P(9, 9)}
    problems = list(problems_mod.find_assignment_problems(
        df, df_locations, activity_anchors=anchors))
    assert len(problems) == 1
    p = problems[0]
    assert p["purposes"] == ["shop"]
    assert p["origin"][0][0] == 9.0 and p["origin"][0][1] == 9.0


def test_anchor_table_resolves_destination_boundary():
    """Destination activity index = trip_index + number of trips in the
    problem: chain [home -> shop -> escort_linked] ends at activity 2."""
    df = _trips_frame([
        (1, 0, "home", "shop", "car", 600.0),
        (1, 1, "shop", "escort_linked", "car", 600.0),
    ])
    df_locations = pd.DataFrame({
        "person_id": [1],
        "home": [_P(0, 0)], "work": [_P(1, 1)], "education": [_P(2, 2)],
    })
    anchors = {(1, 2): _P(7, 7)}
    problems = list(problems_mod.find_assignment_problems(
        df, df_locations, activity_anchors=anchors))
    assert len(problems) == 1
    p = problems[0]
    assert p["purposes"] == ["shop"]
    assert p["destination"][0][0] == 7.0 and p["destination"][0][1] == 7.0


def test_missing_anchor_entry_fails_fast():
    """An escort_linked boundary WITHOUT an anchor entry is a bug in the
    caller's assignment (the rewrite and the table must come from the same
    source); it must raise, not fall back to a column."""
    df = _trips_frame([
        (1, 0, "home", "escort_linked", "car", 600.0),
        (1, 1, "escort_linked", "shop", "car", 600.0),
        (1, 2, "shop", "home", "car", 600.0),
    ])
    df_locations = pd.DataFrame({
        "person_id": [1],
        "home": [_P(0, 0)], "work": [_P(1, 1)], "education": [_P(2, 2)],
    })
    with pytest.raises(KeyError, match="activity_anchors"):
        list(problems_mod.find_assignment_problems(
            df, df_locations, activity_anchors={}))


# --- Perf fix: masks scoped to escort-purpose rows only (fix-wave 2026-08-12) --


def test_rewrite_handles_non_monotonic_row_index():
    """Regression for the escort-purpose-row-scoped rewrite: the candidate
    masks are built over a SUBSET of rows selected via ``out.loc[<boolean
    numpy array>, ...]`` (positional) and then scattered back in True-position
    order. ``df_trips`` arrives sorted but keeps its ORIGINAL (possibly
    non-monotonic) pandas row index from upstream, so mixing an index-aligned
    Series mask with a positional numpy array would silently misplace the
    rewrite. Same 4-row fixture as test_rewrite_marks_only_anchored_activities,
    with a shuffled, non-default index."""
    trips = pd.DataFrame({
        "person_id":         [1, 1, 1, 2],
        "trip_index":        [0, 1, 2, 0],
        "preceding_purpose": ["home", "escort", "escort", "home"],
        "following_purpose": ["escort", "escort", "home", "escort"],
    })
    trips.index = [17, 3, 99, 42]
    links = _links_frame([(1, 0, "edu_7", _P(5, 5))])  # ONE child
    anchors, _ = assign_escort_anchors(trips, links)   # activity 1 anchored, 2 overflow
    from braunschweig.synthesis.locations.secondary_chainsolvers import (
        rewrite_linked_escort_trips,
    )
    out = rewrite_linked_escort_trips(trips, anchors)
    person_1 = out[out["person_id"] == 1]
    assert list(person_1["following_purpose"]) == ["escort_linked", "escort", "home"]
    assert list(person_1["preceding_purpose"]) == ["home", "escort_linked", "escort"]
    # unlinked person 2 keeps the plain escort purpose (draw path)
    assert list(out.loc[out["person_id"] == 2, "following_purpose"]) == ["escort"]
    # the input frame is NOT mutated
    assert "escort_linked" not in set(trips["following_purpose"])


def test_seam_build_links_then_assign_anchors_uses_real_child_rank():
    """Producer-consumer seam: feed REAL build_escort_links output straight
    into assign_escort_anchors (no synthetic _links_frame), pinning that the
    ``child_rank`` column the real producer computes drives distinct schools
    for a consecutive multi-drop chain. Household 10: escorter person 1
    (age 40), children person 2 (age 6 -> edu_7) and person 3 (age 9 ->
    edu_8); build_escort_links also needs the ``following_purpose == "escort"``
    rows to identify person 1 as an escorter in the first place."""
    trips = _chain(1, ["home", "escort", "escort", "work"])
    links, _link_stats = build_escort_links(_persons(), _education(), trips)
    anchors, stats = assign_escort_anchors(trips, links)
    by_activity = anchors.set_index("activity_index")["location_id"]
    assert by_activity[1] == "edu_7" and by_activity[2] == "edu_8"  # youngest first
    assert stats["n_overflow_to_draw"] == 0


def test_rewrite_with_empty_anchors_leaves_trips_unchanged():
    """Flag-ON-with-zero-links path: assign_escort_anchors on an empty links
    table returns an empty anchors frame, and rewrite_linked_escort_trips must
    leave both purpose columns completely untouched. Pins the empty-
    MultiIndex construction/``isin`` behaviour against pandas upgrades."""
    trips = _chain(1, ["home", "escort", "home"])
    links = _links_frame([])
    anchors, _stats = assign_escort_anchors(trips, links)
    assert len(anchors) == 0
    from braunschweig.synthesis.locations.secondary_chainsolvers import (
        rewrite_linked_escort_trips,
    )
    out = rewrite_linked_escort_trips(trips, anchors)
    assert list(out["preceding_purpose"]) == list(trips["preceding_purpose"])
    assert list(out["following_purpose"]) == list(trips["following_purpose"])
    assert "escort_linked" not in set(out["preceding_purpose"]) | set(out["following_purpose"])
