"""Unit tests for braunschweig.synthesis.commute_day.state (synthetic frames only).

Covers the pure core of the commute-day-state model (ADR-0104, issue #244): donor distance
class from the pre-assignment trips table, assigned distance class from home/work geometry,
the MiD-table keep probability, and the seeded state draw.
"""
import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from braunschweig.synthesis.commute_day import state


# ---------------------------------------------------------------------------
# donor_distance_class_from_trips
# ---------------------------------------------------------------------------

def _synthetic_trips():
    # p1: a non-work leg followed by a work trip at 12.4 km -> class "10_25".
    # p2: no trip with following_purpose == "work" at all -> donor class None.
    # p3: the work trip's FIRST candidate column (wegkm_imp) is NaN while the
    #     second (wegkm) is a valid 7 km; only the first EXISTING column may be
    #     used (columns are never mixed row by row), so p3 -> donor class None.
    return pd.DataFrame({
        "person_id":        ["p1", "p1", "p1", "p2", "p2", "p3", "p3"],
        "trip_index":       [0, 1, 2, 0, 1, 0, 1],
        "following_purpose": ["shop", "work", "home", "shop", "home", "work", "home"],
        "wegkm_imp":        [3.0, 12.4, 12.0, 5.0, 5.0, np.nan, 8.0],
        "wegkm":            [3.0, 12.0, 12.0, 5.0, 5.0, 7.0, 8.0],
    })


def test_donor_distance_class_from_trips_first_work_trip():
    result = state.donor_distance_class_from_trips(_synthetic_trips())
    result = result.set_index("person_id")
    assert result.loc["p1", "donor_distance_km"] == pytest.approx(12.4)
    assert result.loc["p1", "donor_distance_class"] == "10_25"
    assert pd.isna(result.loc["p2", "donor_distance_km"])
    assert result.loc["p2", "donor_distance_class"] is None
    # p3: wegkm_imp is the first existing candidate column and is NaN there;
    # wegkm (the second candidate) must NOT be consulted as a per-row fallback.
    assert pd.isna(result.loc["p3", "donor_distance_km"])
    assert result.loc["p3", "donor_distance_class"] is None


def test_donor_distance_class_from_trips_raises_without_any_candidate_column():
    trips = pd.DataFrame({
        "person_id": ["p1"], "trip_index": [0], "following_purpose": ["work"],
    })
    with pytest.raises(KeyError):
        state.donor_distance_class_from_trips(trips)


def test_donor_distance_class_from_trips_uses_only_wegkm_when_wegkm_imp_absent():
    trips = pd.DataFrame({
        "person_id": ["p1"], "trip_index": [0], "following_purpose": ["work"],
        "wegkm": [40.0],
    })
    result = state.donor_distance_class_from_trips(trips).set_index("person_id")
    assert result.loc["p1", "donor_distance_class"] == "25_50"


@pytest.mark.parametrize("donor_km, expected_class", [
    # A donor work-trip length was never subject to the MiD P_ARB_ENTF 200 km top-code
    # (topcode_km=None), so an exact 200.0 km trip classifies as "gt200", not "100_200".
    (200.0, "gt200"),
    # >= MID_TRIP_LENGTH_MAX_KM (1000): a MiD filter/missing code, never a real trip length.
    (1500.0, None),
    # Non-positive: not a real trip at all.
    (0.0, None),
])
def test_donor_distance_class_from_trips_bounds_and_topcode(donor_km, expected_class):
    trips = pd.DataFrame({
        "person_id": ["p1"], "trip_index": [0], "following_purpose": ["work"],
        "wegkm_imp": [donor_km],
    })
    result = state.donor_distance_class_from_trips(trips).set_index("person_id")
    assert result.loc["p1", "donor_distance_class"] == expected_class


# ---------------------------------------------------------------------------
# keep_probability
# ---------------------------------------------------------------------------

def _synthetic_workday_location_table():
    return pd.DataFrame({
        "distance_class": ["lt10", "10_25", "25_50", "50_100", "100_200"],
        "share_at_workplace": [0.59, 0.56, 0.55, 0.44, 0.30],
    })


def test_keep_probability_ratio():
    table = _synthetic_workday_location_table()
    assert state.keep_probability("100_200", "10_25", table) == pytest.approx(0.30 / 0.56)


def test_keep_probability_gt200_uses_the_100_200_row():
    # MiD has no gt200 row; an assigned (or donor) class of gt200 reads the 100_200 row.
    table = _synthetic_workday_location_table()
    assert state.keep_probability("gt200", "lt10", table) == pytest.approx(0.30 / 0.59)


def test_keep_probability_clips_above_one():
    # lt10 (0.59) / 25_50 (0.55) > 1 -- must clip to 1.0. This pair is never actually
    # re-drawn by draw_states (assigned rank lt10 < donor rank 25_50), see below.
    table = _synthetic_workday_location_table()
    assert state.keep_probability("lt10", "25_50", table) == 1.0


def test_keep_probability_donor_none_means_no_redraw():
    table = _synthetic_workday_location_table()
    assert state.keep_probability("gt200", None, table) == 1.0


# ---------------------------------------------------------------------------
# assigned_distance_class
# ---------------------------------------------------------------------------

def _synthetic_geometries():
    df_home = gpd.GeoDataFrame({
        "household_id": ["h1", "h2"],
        "geometry": [Point(0.0, 0.0), Point(0.0, 0.0)],
    }, crs="EPSG:25832")
    df_work = gpd.GeoDataFrame({
        "person_id": ["p1", "p2"],
        "location_id": ["l1", "l2"],
        "geometry": [Point(0.0, 15000.0), Point(0.0, 40000.0)],
    }, crs="EPSG:25832")
    persons = pd.DataFrame({"person_id": ["p1", "p2"], "household_id": ["h1", "h2"]})
    return df_work, df_home, persons


def test_assigned_distance_class_euclidean_times_detour():
    df_work, df_home, persons = _synthetic_geometries()
    result = state.assigned_distance_class(df_work, df_home, persons, detour=1.3).set_index("person_id")
    # p1: 15 km euclid * 1.3 = 19.5 km -> "10_25"; p2: 40 km * 1.3 = 52 km -> "50_100".
    assert result.loc["p1", "distance_km"] == pytest.approx(19.5)
    assert result.loc["p1", "assigned_distance_class"] == "10_25"
    assert result.loc["p2", "distance_km"] == pytest.approx(52.0)
    assert result.loc["p2", "assigned_distance_class"] == "50_100"


def test_assigned_distance_class_requires_matching_projected_crs():
    df_work, df_home, persons = _synthetic_geometries()
    df_work = df_work.to_crs("EPSG:4326")
    with pytest.raises(ValueError):
        state.assigned_distance_class(df_work, df_home, persons, detour=1.3)


def test_assigned_distance_class_raises_on_geographic_crs():
    # Both frames agree on the CRS (so the mismatch branch is not what fires), but it is a
    # geographic (degree-based) CRS -- metric distances require a projected CRS.
    df_work, df_home, persons = _synthetic_geometries()
    df_work = df_work.set_crs("EPSG:4326", allow_override=True)
    df_home = df_home.set_crs("EPSG:4326", allow_override=True)
    with pytest.raises(ValueError):
        state.assigned_distance_class(df_work, df_home, persons, detour=1.3)


def test_assigned_distance_class_raises_on_missing_crs():
    df_work, df_home, persons = _synthetic_geometries()
    df_home = df_home.set_crs(None, allow_override=True)
    with pytest.raises(ValueError):
        state.assigned_distance_class(df_work, df_home, persons, detour=1.3)


def test_assigned_distance_class_topcode_km_none_at_exactly_200km():
    # A model distance was never subject to the MiD P_ARB_ENTF 200 km top-code (topcode_km=None),
    # so an exact 200.0 km assigned distance classifies as "gt200", not "100_200".
    df_home = gpd.GeoDataFrame({"household_id": ["h1"], "geometry": [Point(0.0, 0.0)]},
                               crs="EPSG:25832")
    df_work = gpd.GeoDataFrame({"person_id": ["p1"], "location_id": ["l1"],
                               "geometry": [Point(0.0, 200000.0)]}, crs="EPSG:25832")
    persons = pd.DataFrame({"person_id": ["p1"], "household_id": ["h1"]})
    # detour=1.0 keeps the arithmetic exact (200000 m / 1000 * 1.0 == 200.0 km).
    result = state.assigned_distance_class(df_work, df_home, persons, detour=1.0).set_index("person_id")
    assert result.loc["p1", "distance_km"] == pytest.approx(200.0)
    assert result.loc["p1", "assigned_distance_class"] == "gt200"


# ---------------------------------------------------------------------------
# draw_states
# ---------------------------------------------------------------------------

def _synthetic_workers():
    # p1: eligible (assigned gt200 > donor lt10), far (250 km), NOT escort-protected.
    # p2: eligible, far (250 km), escort-protected.
    # p3: NOT eligible (assigned lt10 rank 0 is not > donor 25_50 rank 2) -- never re-drawn.
    # p4: donor class missing (no donor trip) -- never re-drawn.
    # p5: eligible, NOT far (15 km) -- exercises the home_redraw path without the far rule.
    return pd.DataFrame({
        "person_id": ["p1", "p2", "p3", "p4", "p5"],
        "distance_km": [250.0, 250.0, 5.0, 60.0, 15.0],
        "assigned_distance_class": ["gt200", "gt200", "lt10", "50_100", "gt200"],
        "donor_distance_class": ["lt10", "lt10", "25_50", None, "lt10"],
    })


def _table_forcing_zero_keep_probability():
    # share_at_workplace(100_200) == 0.0 forces p_keep == 0.0 for every assigned class that
    # maps to the 100_200 row (100_200 and gt200), so u < p_keep is never true regardless of
    # the RNG draw -- the "not kept" branch is then deterministic.
    return pd.DataFrame({
        "distance_class": ["lt10", "10_25", "25_50", "50_100", "100_200"],
        "share_at_workplace": [0.59, 0.56, 0.55, 0.44, 0.0],
    })


@pytest.mark.parametrize("absent_share_far", [1.0, 0.0])
def test_draw_states_eligibility_matches_assigned_gt_donor_rank(absent_share_far):
    workers = _synthetic_workers()
    table = _table_forcing_zero_keep_probability()
    rng = np.random.RandomState(1234)
    result, diagnostics = state.draw_states(
        workers, table, rng,
        far_threshold_km=200.0, absent_share_far=absent_share_far, escort_persons=set(),
    )
    result = result.set_index("person_id")
    # Eligibility follows assigned_rank > donor_rank exactly, independent of the RNG draw.
    assert bool(result.loc["p1", "redraw_eligible"]) is True
    assert bool(result.loc["p2", "redraw_eligible"]) is True
    assert bool(result.loc["p3", "redraw_eligible"]) is False
    assert bool(result.loc["p4", "redraw_eligible"]) is False
    assert bool(result.loc["p5", "redraw_eligible"]) is True

    assert result.loc["p3", "commute_day_state"] == "at_workplace"
    assert result.loc["p3", "reason"] == "not_eligible"
    assert result.loc["p4", "commute_day_state"] == "at_workplace"
    assert result.loc["p4", "reason"] == "donor_class_missing"


def test_draw_states_far_person_becomes_absent_when_absent_share_far_is_one():
    workers = _synthetic_workers()
    table = _table_forcing_zero_keep_probability()
    rng = np.random.RandomState(1234)
    result, diagnostics = state.draw_states(
        workers, table, rng,
        far_threshold_km=200.0, absent_share_far=1.0, escort_persons=set(),
    )
    result = result.set_index("person_id")
    # p1 is far, not kept (p_keep forced to 0.0) and not escort-protected -> absent.
    assert result.loc["p1", "commute_day_state"] == "absent"
    assert result.loc["p1", "reason"] == "absent_far"
    assert diagnostics["n_escort_protected"] == 0


def test_draw_states_escort_protected_far_person_never_absent():
    workers = _synthetic_workers()
    table = _table_forcing_zero_keep_probability()
    rng = np.random.RandomState(1234)
    result, diagnostics = state.draw_states(
        workers, table, rng,
        far_threshold_km=200.0, absent_share_far=1.0, escort_persons={"p2"},
    )
    result = result.set_index("person_id")
    assert result.loc["p2", "commute_day_state"] == "home"
    assert result.loc["p2", "reason"] == "home_escort_protected"
    assert diagnostics["n_escort_protected"] == 1
    assert diagnostics["n_absent"] == 1  # only p1


def test_draw_states_far_person_becomes_home_when_absent_share_far_is_zero():
    workers = _synthetic_workers()
    table = _table_forcing_zero_keep_probability()
    rng = np.random.RandomState(1234)
    result, diagnostics = state.draw_states(
        workers, table, rng,
        far_threshold_km=200.0, absent_share_far=0.0, escort_persons=set(),
    )
    result = result.set_index("person_id")
    assert result.loc["p1", "commute_day_state"] == "home"
    assert result.loc["p1", "reason"] == "home_redraw"
    assert diagnostics["n_absent"] == 0


def test_draw_states_p_keep_matches_keep_probability_function():
    # draw_states must produce the IDENTICAL p_keep that keep_probability computes for the same
    # (assigned, donor) pair -- both call the same shared ratio/clip formula, so they cannot
    # drift apart (see state._clipped_keep_ratio).
    workers = pd.DataFrame({
        "person_id": ["p1", "p2"],
        "distance_km": [60.0, 5.0],
        "assigned_distance_class": ["100_200", "lt10"],
        "donor_distance_class": ["10_25", "25_50"],
    })
    table = _synthetic_workday_location_table()
    rng = np.random.RandomState(3)
    result, diagnostics = state.draw_states(
        workers, table, rng,
        far_threshold_km=200.0, absent_share_far=1.0, escort_persons=set(),
    )
    result = result.set_index("person_id")
    # p1 is eligible (100_200 rank 4 > 10_25 rank 1).
    assert bool(result.loc["p1", "redraw_eligible"]) is True
    assert result.loc["p1", "p_keep"] == pytest.approx(state.keep_probability("100_200", "10_25", table))
    assert result.loc["p1", "p_keep"] == pytest.approx(0.30 / 0.56)
    # p2 is NOT eligible (lt10 rank 0 is not > 25_50 rank 2): p_keep stays exactly 1.0.
    assert bool(result.loc["p2", "redraw_eligible"]) is False
    assert result.loc["p2", "p_keep"] == 1.0


def test_draw_states_by_assigned_class_uses_unknown_key_for_missing_assigned_class():
    # An assigned class of None/NaN must be keyed as the literal string "unknown" in the
    # diagnostics, never as a null key (which would break JSON serialisation of the run's
    # diagnostics).
    workers = pd.DataFrame({
        "person_id": ["p1"],
        "distance_km": [15.0],
        "assigned_distance_class": [None],
        "donor_distance_class": ["lt10"],
    })
    table = _synthetic_workday_location_table()
    rng = np.random.RandomState(5)
    _, diagnostics = state.draw_states(
        workers, table, rng,
        far_threshold_km=200.0, absent_share_far=1.0, escort_persons=set(),
    )
    assert "unknown" in diagnostics["by_assigned_class"]
    assert None not in diagnostics["by_assigned_class"]
    assert diagnostics["by_assigned_class"]["unknown"]["at_workplace"] == 1


def test_draw_states_diagnostics_counts_sum_to_n_workers():
    workers = _synthetic_workers()
    table = _table_forcing_zero_keep_probability()
    rng = np.random.RandomState(42)
    result, diagnostics = state.draw_states(
        workers, table, rng,
        far_threshold_km=200.0, absent_share_far=1.0, escort_persons={"p2"},
    )
    assert diagnostics["n_workers"] == len(workers) == len(result)
    assert (diagnostics["n_at_workplace"] + diagnostics["n_home"] + diagnostics["n_absent"]
            == diagnostics["n_workers"])
    by_class_total = sum(sum(counts.values()) for counts in diagnostics["by_assigned_class"].values())
    assert by_class_total == diagnostics["n_workers"]


def test_draw_states_is_deterministic_for_a_fixed_seed():
    workers = _synthetic_workers()
    table = _table_forcing_zero_keep_probability()

    result_a, diagnostics_a = state.draw_states(
        workers, table, np.random.RandomState(777),
        far_threshold_km=200.0, absent_share_far=0.5, escort_persons={"p2"},
    )
    result_b, diagnostics_b = state.draw_states(
        workers, table, np.random.RandomState(777),
        far_threshold_km=200.0, absent_share_far=0.5, escort_persons={"p2"},
    )
    pd.testing.assert_frame_equal(result_a, result_b)
    assert diagnostics_a == diagnostics_b


def test_draw_states_never_redrawn_pair_stays_at_workplace():
    # The keep_probability("lt10", "25_50") == 1.0 pair (clipped) is never actually re-drawn
    # because lt10's rank (0) is not strictly greater than 25_50's rank (2); draw_states must
    # leave such a worker at_workplace regardless of the RNG draw.
    workers = pd.DataFrame({
        "person_id": ["p1"],
        "distance_km": [5.0],
        "assigned_distance_class": ["lt10"],
        "donor_distance_class": ["25_50"],
    })
    table = _synthetic_workday_location_table()
    rng = np.random.RandomState(0)
    result, diagnostics = state.draw_states(
        workers, table, rng,
        far_threshold_km=200.0, absent_share_far=1.0, escort_persons=set(),
    )
    assert result.loc[0, "commute_day_state"] == "at_workplace"
    assert bool(result.loc[0, "redraw_eligible"]) is False


def test_draw_states_guaranteed_kept_when_shares_are_equal():
    # p_keep == 1.0 for an ELIGIBLE pair (equal shares) means u < 1.0 always holds, so the
    # person is deterministically kept at_workplace regardless of the RNG draw.
    workers = pd.DataFrame({
        "person_id": ["p1"],
        "distance_km": [30.0],
        "assigned_distance_class": ["25_50"],
        "donor_distance_class": ["lt10"],
    })
    table = pd.DataFrame({
        "distance_class": ["lt10", "25_50"],
        "share_at_workplace": [0.50, 0.50],
    })
    rng = np.random.RandomState(9)
    result, diagnostics = state.draw_states(
        workers, table, rng,
        far_threshold_km=200.0, absent_share_far=1.0, escort_persons=set(),
    )
    assert bool(result.loc[0, "redraw_eligible"]) is True
    assert result.loc[0, "commute_day_state"] == "at_workplace"
    assert result.loc[0, "reason"] == "kept"


def test_constants():
    assert state.COMMUTE_DAY_SEED_OFFSET == 7301
    assert state.STATES == ("at_workplace", "home", "absent")
    assert state.CLASS_RANK["lt10"] == 0
    assert state.CLASS_RANK["gt200"] == 5
