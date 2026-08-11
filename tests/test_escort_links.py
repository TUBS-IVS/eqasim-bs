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


def test_links_pick_youngest_child_in_household():
    links, stats = build_escort_links(_persons(), _education(), _trips())
    assert len(links) == 1
    row = links.iloc[0]
    assert row["person_id"] == 1
    assert row["location_id"] == "edu_7"  # child 2 (age 6) beats child 3 (age 9)
    # person 4 escorts but household 20 has no education-assigned child -> unlinked
    assert stats["n_escorters"] == 2
    assert stats["n_linked"] == 1
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
    assert list(links.columns) == ["person_id", "location_id", "geometry"]
    assert len(links) == 0
    assert stats["n_escorters"] == 0 and stats["n_linked"] == 0
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
def test_prepare_rewrite_marks_only_linked_persons():
    trips = pd.DataFrame({
        "person_id": [1, 1, 2],
        "preceding_purpose": ["home", "escort", "home"],
        "following_purpose": ["escort", "home", "escort"],
    })
    links = pd.DataFrame({"person_id": [1], "location_id": ["edu_7"],
                          "geometry": [_P(5, 5)]})
    from braunschweig.synthesis.locations.secondary_chainsolvers import (
        rewrite_linked_escort_trips,
    )
    out = rewrite_linked_escort_trips(trips, links)
    assert list(out.loc[out["person_id"] == 1, "following_purpose"]) == ["escort_linked", "home"]
    assert list(out.loc[out["person_id"] == 1, "preceding_purpose"]) == ["home", "escort_linked"]
    # unlinked person 2 keeps the plain escort purpose (draw path)
    assert list(out.loc[out["person_id"] == 2, "following_purpose"]) == ["escort"]
    # the input frame is NOT mutated
    assert "escort_linked" not in set(trips["following_purpose"])


def test_anchored_location_rows_for_linked_escorts():
    trips = pd.DataFrame({
        "person_id": [1, 1, 1],
        "trip_index": [0, 1, 2],
        "preceding_purpose": ["home", "escort", "shop"],
        "following_purpose": ["escort", "shop", "home"],
    })
    links = pd.DataFrame({"person_id": [1], "location_id": ["edu_7"],
                          "geometry": [_P(5, 5)]})
    from braunschweig.synthesis.locations.secondary_chainsolvers import (
        anchored_escort_location_rows,
    )
    rows = anchored_escort_location_rows(trips, links)
    assert list(rows.columns) == ["person_id", "activity_index", "location_id", "geometry"]
    assert len(rows) == 1
    assert rows.iloc[0]["activity_index"] == 1  # trip_index 0 -> destination activity 1
    assert rows.iloc[0]["location_id"] == "edu_7"


def test_anchored_rows_cover_origin_only_escort_activity():
    """Regression (5% server run 2026-08-10): an escort activity that appears
    ONLY as a trip ORIGIN must still be anchored.

    The donor trip chains contain rare inconsistencies (0.027% of links, mostly
    non-escort) where ``trip[i].following_purpose != trip[i+1].preceding_purpose``.
    When the origin side is escort, that escort activity has no trip whose
    ``following_purpose`` is escort, so a destination-only filter misses it. On the
    linked path the problem splitter does not place it either (``escort_linked``
    is a FIXED purpose), so it ends up without geometry and
    ``synthesis/population/spatial/locations.py`` asserts.

    Chain below mirrors person 769474: trip 5 arrives ``home`` but trip 6 departs
    from ``escort`` -> activity 6 is escort as an ORIGIN only.
    """
    trips = pd.DataFrame({
        "person_id":         [1, 1, 1, 1, 1, 1, 1],
        "trip_index":        [0, 1, 2, 3, 4, 5, 6],
        "preceding_purpose": ["home", "escort", "home", "shop", "escort", "escort", "escort"],
        "following_purpose": ["escort", "home", "shop", "escort", "escort", "home", "home"],
    })
    links = pd.DataFrame({"person_id": [1], "location_id": ["edu_7"],
                          "geometry": [_P(5, 5)]})
    from braunschweig.synthesis.locations.secondary_chainsolvers import (
        anchored_escort_location_rows,
    )
    rows = anchored_escort_location_rows(trips, links)

    # Escort activities: 1, 4, 5 (trip destinations) and 6 (origin of trip 6).
    assert sorted(rows["activity_index"]) == [1, 4, 5, 6]
    assert set(rows["location_id"]) == {"edu_7"}


def test_anchored_rows_have_no_duplicate_activities():
    """An escort activity in a CONSISTENT chain is both the destination of the
    previous trip and the origin of the next one; it must be emitted exactly
    once, otherwise the location merge produces more rows than activities."""
    trips = pd.DataFrame({
        "person_id":         [1, 1, 1],
        "trip_index":        [0, 1, 2],
        "preceding_purpose": ["home", "escort", "escort"],
        "following_purpose": ["escort", "escort", "home"],
    })
    links = pd.DataFrame({"person_id": [1], "location_id": ["edu_7"],
                          "geometry": [_P(5, 5)]})
    from braunschweig.synthesis.locations.secondary_chainsolvers import (
        anchored_escort_location_rows,
    )
    rows = anchored_escort_location_rows(trips, links)

    # Activities 1 and 2 are escort; each exactly once.
    assert sorted(rows["activity_index"]) == [1, 2]
    assert not rows.duplicated(subset=["person_id", "activity_index"]).any()


def test_validate_secondary_coverage_accepts_extra_valid_ids():
    from braunschweig.matsim.scenario.facilities import validate_secondary_coverage
    realised = pd.DataFrame({"location_id": ["sec_1", "edu_7"]})
    secondary = pd.DataFrame({"location_id": ["sec_1"]})
    with pytest.raises(RuntimeError):
        validate_secondary_coverage(realised, secondary)
    validate_secondary_coverage(realised, secondary, extra_valid_ids={"edu_7"})
