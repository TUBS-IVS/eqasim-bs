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
