"""Tests for the stroll / dog-walk loops (issue #679, tasks C2 and C3).

The MiD 2023 diary records a stroll (W_ZWD 710) or a dog walk (711) as ONE trip whose length is
the whole loop. ``loops.flag_loops`` marks the synthetic leisure legs that are such loops: by the
donor's own detail code where it has one, else by a seeded draw from a probability estimated on
the MiD legs that carry the detail code. ``loops.collapse_loops`` takes the flagged loop
activities out of the chains before the chain solver runs, and ``loops.restore_loops`` puts them
back afterwards at the place of the activity they start from.
"""
from __future__ import annotations

import logging

import geopandas as gpd
import numpy as np
import pandas as pd
import pytest
from shapely.geometry import Point

from braunschweig.popsim.purpose_subtype import leisure_spec
from braunschweig.synthesis.locations.secondary_chainsolvers import loops

# Band 3 ([1200 s, inf)) of braunschweig.popsim.purpose_subtype.tt_band, the band function the
# module docstring names (it is defined in braunschweig.popsim.shop_subtype).
TRAVEL_TIME_S = 1800.0
BAND_OF_TRAVEL_TIME = 3

WALK_HOME_HOME = ("walk", BAND_OF_TRAVEL_TIME, True, True)
WALK_AWAY_HOME = ("walk", BAND_OF_TRAVEL_TIME, False, True)


def _trips(rows, synthetic_closures=()):
    """Rows of (person_id, trip_index, mode, preceding, following, W_ZWECK, W_ZWD).

    ``synthetic_closures`` lists the ``(person_id, trip_index)`` of the trips that are the plan
    repair's invented home trip (``is_synthetic_closure``); every other trip is from the diary.
    """
    frame = pd.DataFrame(
        rows,
        columns=["person_id", "trip_index", "mode", "preceding_purpose", "following_purpose",
                 "W_ZWECK", "W_ZWD"],
    ).assign(travel_time=TRAVEL_TIME_S)
    frame["is_synthetic_closure"] = [
        key in set(synthetic_closures) for key in zip(frame["person_id"], frame["trip_index"])]
    return frame


def _probabilities(cells=None, marginal=None, codeplan_sentinels=False):
    return loops.LoopProbabilities(
        cells={} if cells is None else cells,
        marginal={"walk": 0.0, "bicycle": 0.0} if marginal is None else marginal,
        n_observations={},
        labelled_codes=leisure_spec(codeplan_sentinels).group_codes,
    )


def _leisure_leg(person_id, mode, w_zwd, w_zweck=7):
    """A first trip from home into leisure, followed by the way back home."""
    return [
        (person_id, 0, mode, "home", "leisure", w_zweck, w_zwd),
        (person_id, 1, mode, "leisure", "home", 8, np.nan),
    ]


def test_labelled_stroll_dog_walk_and_ride_without_destination_are_loops():
    rows = (_leisure_leg(1, "walk", 710) + _leisure_leg(2, "walk", 711)
            + _leisure_leg(3, "bicycle", 710))
    df = _trips(rows)
    random = np.random.RandomState(1)

    flags, stats = loops.flag_loops(df, _probabilities(), random)

    assert flags.index.equals(df.index) and flags.dtype == bool
    assert flags[(df["following_purpose"] == "leisure")].tolist() == [True, True, True]
    assert not flags[(df["following_purpose"] == "home")].any()
    assert stats["by_label"] == {"walk": 2, "bicycle": 1}
    assert stats["draws"] == {"walk": 0, "bicycle": 0}
    assert set(stats) == {"in_scope", "by_label", "by_draw", "draws", "draws_from_marginal",
                          "missing_detail_code"}
    assert all(set(per_mode) == {"walk", "bicycle"} for per_mode in stats.values())


def test_other_codes_other_modes_other_purposes_and_missing_codes_are_no_loops_and_draw_nothing():
    # Probability 1.0 everywhere: a leg that wrongly drew would come out True.
    always = _probabilities(marginal={"walk": 1.0, "bicycle": 1.0})
    rows = (
        _leisure_leg(1, "walk", 701)                     # labelled, not a loop code
        + _leisure_leg(2, "walk", 706)                   # labelled, not a loop code
        + _leisure_leg(3, "car", 710)                    # motorised stroll
        + _leisure_leg(4, "walk", 710, w_zweck=10)       # other coarse purposes, with a
        + _leisure_leg(5, "walk", 2202, w_zweck=14)      # loop code and with a sentinel
        + _leisure_leg(6, "walk", 710, w_zweck=15)
        + _leisure_leg(7, "walk", 2202, w_zweck=16)
        + _leisure_leg(8, "walk", np.nan, w_zweck=np.nan)  # no donor code (closure, in-commuter)
        + _leisure_leg(9, "walk", np.nan)                # W_ZWECK 7 but no detail code at all
    )
    df = _trips(rows)
    random = np.random.RandomState(7)

    flags, stats = loops.flag_loops(df, always, random)

    assert not flags.any()
    assert stats["draws"] == {"walk": 0, "bicycle": 0}
    # Person 9's leisure walk has no detail code at all: no loop, no draw, but counted.
    assert stats["missing_detail_code"] == {"walk": 1, "bicycle": 0}
    # No random number consumed: the next draw is the first draw of a fresh stream.
    assert random.random_sample() == np.random.RandomState(7).random_sample()


def test_a_leg_without_detail_draws_with_the_cell_probability():
    # Person 1 starts at home and goes home after (cell p=1); person 2 starts at work (cell p=0).
    rows = (
        _leisure_leg(1, "walk", 2202)
        + [(2, 0, "car", "home", "work", 1, np.nan)]
        + [(2, 1, "walk", "work", "leisure", 7, 4402), (2, 2, "walk", "leisure", "home", 8, np.nan)]
        + _leisure_leg(3, "walk", 999)
    )
    df = _trips(rows)
    probabilities = _probabilities(cells={WALK_HOME_HOME: 1.0, WALK_AWAY_HOME: 0.0})

    flags, stats = loops.flag_loops(df, probabilities, np.random.RandomState(3))

    leisure = df["following_purpose"] == "leisure"
    assert flags[leisure].tolist() == [True, False, True]
    assert stats["in_scope"]["walk"] == 3
    assert stats["draws"]["walk"] == 3 and stats["by_draw"]["walk"] == 2
    assert stats["draws_from_marginal"]["walk"] == 0


def test_the_random_streams_of_the_secondary_location_stage_have_distinct_seed_offsets():
    # Every stream of the stage is RandomState(random_seed + offset); the solver's own stream is
    # random_seed itself (offset 0). Two equal offsets give two streams of identical uniforms, so
    # one feature's draws would mirror another's (the loop draw once shared 90215 with the SrV
    # location-type decider, both on in production).
    from braunschweig.synthesis.locations.secondary_chainsolvers import (
        deciders, srv_location_types)
    offsets = {f"{module.__name__.rsplit('.', 1)[-1]}.{name}": value
               for module in (deciders, srv_location_types, loops)
               for name, value in vars(module).items() if name.endswith("SEED_OFFSET")}

    # The scan finds every stream the stage seeds today (it cannot pass by finding nothing).
    assert {"deciders.SHOP_SUBTYPE_SEED_OFFSET", "deciders.LEISURE_SUBTYPE_SEED_OFFSET",
            "deciders.OTHER_SUBTYPE_SEED_OFFSET", "deciders.ESCORT_LOCATION_SEED_OFFSET",
            "srv_location_types.SRV_LOCATION_SEED_OFFSET", "loops.LOOP_SEED_OFFSET"} <= set(offsets)
    values = [0, *offsets.values()]
    assert len(set(values)) == len(values), offsets


def test_a_cell_without_an_estimate_uses_the_marginal_and_is_counted():
    df = _trips(_leisure_leg(1, "walk", 2202))
    probabilities = _probabilities(cells={WALK_AWAY_HOME: 0.0}, marginal={"walk": 1.0})

    flags, stats = loops.flag_loops(df, probabilities, np.random.RandomState(3))

    assert flags.iloc[0]
    assert stats["draws_from_marginal"]["walk"] == 1


def test_a_marginal_share_above_the_fallback_warn_rate_warns_and_cell_draws_do_not(caplog):
    cell_probabilities = _probabilities(cells={WALK_HOME_HOME: 0.5})
    marginal_probabilities = _probabilities(marginal={"walk": 0.5})
    df = _trips(_leisure_leg(1, "walk", 2202))

    with caplog.at_level(logging.INFO, logger=loops.logger.name):
        loops.flag_loops(df, cell_probabilities, np.random.RandomState(1))
    assert not [record for record in caplog.records if record.levelno >= logging.WARNING]

    caplog.clear()
    with caplog.at_level(logging.INFO, logger=loops.logger.name):
        loops.flag_loops(df, marginal_probabilities, np.random.RandomState(1))
    assert [record for record in caplog.records
            if record.levelno == logging.WARNING and "marginal" in record.getMessage()]


def test_a_drawing_leg_without_a_travel_time_raises_tagged_and_draws_nothing():
    df = _trips(_leisure_leg(41, "walk", 2202) + _leisure_leg(42, "walk", 2202))
    df.loc[df["person_id"] == 42, "travel_time"] = np.nan
    random = np.random.RandomState(9)

    with pytest.raises(ValueError, match=r"\[secondary_loops\] flag_loops: 1 of 2 legs .*\[42\]"):
        loops.flag_loops(df, _probabilities(marginal={"walk": 0.5}), random)
    assert random.random_sample() == np.random.RandomState(9).random_sample()


def test_a_repeated_index_raises_naming_the_function_and_the_duplicates():
    df = _trips(_leisure_leg(1, "walk", 2202) + _leisure_leg(2, "walk", 2202))
    df.index = [0, 0, 1, 1]

    with pytest.raises(ValueError, match=r"flag_loops needs a unique index.*2 of 4"):
        loops.flag_loops(df, _probabilities(marginal={"walk": 0.5}), np.random.RandomState(1))
    with pytest.raises(ValueError, match="way_home_follows needs a unique index"):
        loops.way_home_follows(df)


def test_a_mode_without_any_estimate_raises_naming_the_mode():
    df = _trips(_leisure_leg(1, "bicycle", 2202))
    probabilities = _probabilities(marginal={"walk": 0.5})

    random = np.random.RandomState(3)

    with pytest.raises(ValueError, match="bicycle"):
        loops.flag_loops(df, probabilities, random)
    assert random.random_sample() == np.random.RandomState(3).random_sample()


def test_the_same_seed_gives_the_same_flags_whatever_the_row_order():
    rows = []
    for person_id in range(1, 41):
        rows += _leisure_leg(person_id, "walk", 2202)
    df = _trips(rows)
    probabilities = _probabilities(marginal={"walk": 0.5})

    flags_a, _ = loops.flag_loops(df, probabilities, np.random.RandomState(11))
    flags_b, _ = loops.flag_loops(df, probabilities, np.random.RandomState(11))
    flags_shuffled, _ = loops.flag_loops(
        df.sample(frac=1.0, random_state=0), probabilities, np.random.RandomState(11))

    assert flags_a.equals(flags_b)
    # The draw order is (person_id, trip_index), not the row order of the frame.
    assert flags_shuffled.sort_index().equals(flags_a)
    assert 0 < int(flags_a.sum()) < 40


def test_a_code_without_detail_in_the_codeplan_variant_draws_otherwise_it_is_labelled():
    # 799 "Freizeit k.A." is a no-detail code: unlabelled (draws) with the codeplan sentinels on,
    # a labelled non-loop code (no draw) with them off.
    df = _trips(_leisure_leg(1, "walk", 799))
    always = {"walk": 1.0, "bicycle": 1.0}

    flags_on, stats_on = loops.flag_loops(
        df, _probabilities(marginal=always, codeplan_sentinels=True), np.random.RandomState(5))
    flags_off, stats_off = loops.flag_loops(
        df, _probabilities(marginal=always, codeplan_sentinels=False), np.random.RandomState(5))

    assert flags_on.iloc[0] and stats_on["draws"]["walk"] == 1
    assert not flags_off.iloc[0] and stats_off["draws"]["walk"] == 0


def test_way_home_follows_reads_the_next_trip_of_the_same_person():
    df = _trips([
        (1, 0, "walk", "home", "leisure", 7, 710),
        (1, 1, "walk", "leisure", "home", 8, np.nan),
        (2, 0, "walk", "home", "leisure", 7, 710),
        (2, 1, "walk", "leisure", "shop", 4, np.nan),
        (2, 2, "walk", "shop", "leisure", 7, 710),     # last trip of person 2
        (3, 0, "walk", "home", "leisure", 7, 710),     # only trip of person 3
    ]).sample(frac=1.0, random_state=1)                # the frame order must not matter

    result = loops.way_home_follows(df)

    assert result.index.equals(df.index)
    by_trip = dict(zip(zip(df["person_id"], df["trip_index"]), result))
    assert by_trip == {(1, 0): True, (1, 1): False, (2, 0): False, (2, 1): False,
                       (2, 2): False, (3, 0): False}


def test_out_and_back_needs_home_start_and_diary_way_home():
    rows = [
        (1, 0, "walk", "home", "leisure", 7, 710),       # home -> stroll -> home: out-and-back
        (1, 1, "walk", "leisure", "home", 8, np.nan),
        (2, 0, "car", "home", "shop", 4, 2202),
        (2, 1, "walk", "shop", "leisure", 7, 710),       # shop -> stroll -> home: loop
        (2, 2, "walk", "leisure", "home", 8, np.nan),
        (3, 0, "walk", "home", "leisure", 7, 710),       # home -> stroll -> invented home: loop
        (3, 1, "walk", "leisure", "home", 8, np.nan),
        (4, 0, "walk", "home", "leisure", 7, 710),       # home -> stroll -> stroll -> home:
        (4, 1, "walk", "leisure", "leisure", 7, 711),    # both loops
        (4, 2, "walk", "leisure", "home", 8, np.nan),
    ]
    df = _trips(rows, synthetic_closures=[(3, 1)]).sample(frac=1.0, random_state=2)
    is_stroll, _stats = loops.flag_loops(df, _probabilities(), np.random.RandomState(1))

    out_and_back = loops.out_and_back_strolls(df, is_stroll)

    assert out_and_back.index.equals(df.index) and out_and_back.dtype == bool
    by_trip = df.assign(out_and_back=out_and_back).sort_values(["person_id", "trip_index"])
    assert by_trip.loc[is_stroll.reindex(by_trip.index), "out_and_back"].tolist() == [
        True, False, False, False, False]
    assert not (out_and_back & ~is_stroll).any()


def test_cell_lookup_ignores_the_synthetic_closure():
    # Same diary twice: person 1's way home is the plan repair's invented closure, person 2's is
    # a diary trip. A drawn leg followed only by the closure uses the cell way_home_follows=False.
    df = _trips(_leisure_leg(1, "walk", 2202) + _leisure_leg(2, "walk", 2202),
                synthetic_closures=[(1, 1)])
    probabilities = _probabilities(cells={WALK_HOME_HOME: 1.0,
                                          ("walk", BAND_OF_TRAVEL_TIME, True, False): 0.0})

    flags, stats = loops.flag_loops(df, probabilities, np.random.RandomState(3))

    assert flags[df["following_purpose"] == "leisure"].tolist() == [False, True]
    assert stats["draws_from_marginal"]["walk"] == 0


def test_diary_way_home_requires_the_closure_column():
    df = _trips(_leisure_leg(1, "walk", 710))
    with pytest.raises(ValueError, match="is_synthetic_closure"):
        loops.diary_way_home_follows(df.drop(columns="is_synthetic_closure"))
    # A missing value is neither a diary trip nor an invented one.
    with pytest.raises(ValueError, match="is_synthetic_closure"):
        loops.diary_way_home_follows(df.assign(is_synthetic_closure=[False, None]))


def _mid_person(household_id, legs, *, hvm_imp=1, weight=1.0, kernwo=1):
    """One MiD person: ``legs`` of (W_ZWECK, W_ZWD), 30 minutes each, in W_ID order."""
    return [
        {"H_ID": household_id, "P_ID": 1, "W_ID": index + 1, "W_ZWECK": zweck, "W_ZWD": zwd,
         "hvm_imp": hvm_imp, "W_GEW": weight, "kernwo": kernwo, "W_RBW": 0,
         "W_SZS": 10, "W_SZM": 0, "W_AZS": 10, "W_AZM": 30}
        for index, (zweck, zwd) in enumerate(legs)
    ]


def _mid_table():
    rows = []
    household_id = 0

    def add(n, legs, **kwargs):
        nonlocal household_id
        for _ in range(n):
            household_id += 1
            rows.extend(_mid_person(household_id, legs, **kwargs))

    # Cell (walk, band 3, starts at home, way home follows): 40 labelled legs, 30 strolls of
    # weight 1 and 10 other labelled legs of weight 3 -> weighted share 30 / (30 + 30) = 0.5.
    add(30, [(7, 710), (8, 2202)], weight=1.0)
    add(10, [(7, 701), (8, 2202)], weight=3.0)
    # Cell (walk, band 3, starts at home, NO way home): 30 labelled legs, none a loop.
    add(30, [(7, 706)], weight=1.0)
    # Thin cell (bicycle, band 3, starts at home, way home follows): 5 legs, all rides.
    add(5, [(7, 710), (8, 2202)], hvm_imp=2, weight=1.0)
    # Out of the estimate: a sentinel, another coarse purpose, a weekend stroll.
    add(10, [(7, 2202), (8, 2202)], weight=1.0)
    add(10, [(14, 710), (8, 2202)], weight=1.0)
    add(10, [(7, 710), (8, 2202)], hvm_imp=2, weight=1.0, kernwo=6)
    return pd.DataFrame(rows)


def test_estimate_loop_probabilities_weights_drops_thin_cells_and_keeps_a_marginal():
    mid = _mid_table()

    estimate = loops.estimate_loop_probabilities(mid, min_obs=30, weekday_only=True)

    assert estimate.cells[WALK_HOME_HOME] == pytest.approx(0.5)
    assert estimate.cells[("walk", BAND_OF_TRAVEL_TIME, True, False)] == pytest.approx(0.0)
    # The 5-leg bicycle cell is under min_obs: absent from the cells, covered by the marginal.
    assert ("bicycle", BAND_OF_TRAVEL_TIME, True, True) not in estimate.cells
    assert estimate.marginal["bicycle"] == pytest.approx(1.0)
    # Walk marginal over both cells: strolls 30 of weight 1; other labelled legs 30 + 30.
    assert estimate.marginal["walk"] == pytest.approx(30.0 / 90.0)
    assert estimate.n_observations[WALK_HOME_HOME] == 40
    assert estimate.labelled_codes == leisure_spec(False).group_codes

    # The weekend stroll counts only when the weekday restriction is off.
    all_days = loops.estimate_loop_probabilities(mid, min_obs=30, weekday_only=False)
    assert all_days.marginal["bicycle"] == pytest.approx(1.0)
    assert all_days.n_observations["bicycle"] == 15 and estimate.n_observations["bicycle"] == 5


def _mid_persons(groups):
    """MiD table from ``(n_persons, legs)`` groups; each person walks, on a weekday, 30 min a leg."""
    rows = []
    household_id = 0
    for n_persons, legs in groups:
        for _ in range(n_persons):
            household_id += 1
            rows.extend(_mid_person(household_id, legs))
    return pd.DataFrame(rows)


def test_the_codeplan_variant_leaves_799_out_of_the_estimate_universe():
    # 30 strolls and 10 "Freizeit k.A." legs in one cell: 799 is a labelled non-loop code with the
    # codeplan sentinels off (share 30/40), a no-detail code outside the universe with them on.
    mid = _mid_persons([(30, [(7, 710), (8, 2202)]), (10, [(7, 799), (8, 2202)])])

    off = loops.estimate_loop_probabilities(mid, min_obs=30, weekday_only=True)
    on = loops.estimate_loop_probabilities(
        mid, min_obs=30, weekday_only=True, codeplan_sentinels=True)

    assert off.cells[WALK_HOME_HOME] == pytest.approx(0.75)
    assert off.n_observations[WALK_HOME_HOME] == 40
    assert on.cells[WALK_HOME_HOME] == pytest.approx(1.0)
    assert on.n_observations[WALK_HOME_HOME] == 30
    assert 799 in off.labelled_codes and 799 not in on.labelled_codes


def test_the_way_home_and_the_last_leg_of_a_mid_person_are_read_from_the_next_leg():
    # W_ZWECK 9 (back home, other kind) counts as a way home like 8; a stroll as the person's
    # last leg has no way home after it.
    mid = _mid_persons([(30, [(7, 710), (9, 2202)]), (30, [(7, 701)])])

    estimate = loops.estimate_loop_probabilities(mid, min_obs=30, weekday_only=True)

    assert estimate.cells[WALK_HOME_HOME] == pytest.approx(1.0)
    assert estimate.cells[("walk", BAND_OF_TRAVEL_TIME, True, False)] == pytest.approx(0.0)


def test_estimate_loop_probabilities_rejects_a_bad_min_obs_and_an_empty_universe():
    mid = _mid_table()
    with pytest.raises(ValueError, match="min_obs must be >= 1"):
        loops.estimate_loop_probabilities(mid, min_obs=0, weekday_only=True)

    # Shopping legs only: no labelled leisure leg to estimate from.
    shopping = _mid_persons([(5, [(4, 501), (8, 2202)])])
    with pytest.raises(ValueError, match="no labelled leisure leg"):
        loops.estimate_loop_probabilities(shopping, min_obs=1, weekday_only=True)


def test_estimate_warns_when_every_cell_is_below_min_obs(caplog):
    with caplog.at_level(logging.INFO, logger=loops.logger.name):
        estimate = loops.estimate_loop_probabilities(_mid_table(), min_obs=1000, weekday_only=True)

    assert estimate.cells == {} and set(estimate.marginal) == {"walk", "bicycle"}
    assert [record for record in caplog.records
            if record.levelno == logging.WARNING and "below min_obs" in record.getMessage()]


# ---------------------------------------------------------------------------
# Task C3: collapse the loop activities before the solve, restore them after it
# ---------------------------------------------------------------------------

CRS = "EPSG:25832"
LOCATION_COLUMNS = ["person_id", "activity_index", "location_id", "geometry"]
PRIMARY_PURPOSES = ("home", "work", "education")
NON_LOOP_MODES = ("car", "pt", "bicycle")


def _chains(*persons):
    """Trips table and loop flags from ``(person_id, chain)`` pairs such as ``(1, "home S home")``.

    The chain lists the activity purposes in order; ``S`` is a stroll (purpose ``leisure``, the
    trip into it flagged). Trip ``k`` leaves activity ``k`` and reaches ``k + 1``. Every trip of a
    person has its own mode, departure and arrival time, so a test can tell which trip survived.
    """
    rows = []
    for person_id, chain in persons:
        purposes = chain.split()
        n_trips = len(purposes) - 1
        for k in range(n_trips):
            is_loop = purposes[k + 1] == "S"
            departure = 3600.0 * (k + 1)
            rows.append({
                "person_id": person_id, "trip_index": k,
                "preceding_purpose": "leisure" if purposes[k] == "S" else purposes[k],
                "following_purpose": "leisure" if is_loop else purposes[k + 1],
                "mode": "walk" if is_loop else NON_LOOP_MODES[k % len(NON_LOOP_MODES)],
                "departure_time": departure, "arrival_time": departure + 60.0 * (k + 1),
                "is_first_trip": k == 0, "is_last_trip": k == n_trips - 1,
                "W_ZWECK": 7 if is_loop else 4, "W_ZWD": 710 if is_loop else 2202,
                "is_loop": is_loop,
            })
    df = pd.DataFrame(rows)
    df["travel_time"] = df["arrival_time"] - df["departure_time"]
    return df.drop(columns="is_loop"), df["is_loop"].astype(bool)


def _places(person_ids):
    """Household, home, work and education places; the household of person p is 100 + p."""
    person_ids = list(person_ids)
    return dict(
        df_persons=pd.DataFrame({"person_id": person_ids,
                                 "household_id": [100 + p for p in person_ids]}),
        df_home=gpd.GeoDataFrame({"household_id": [100 + p for p in person_ids],
                                  "geometry": [Point(p, 0) for p in person_ids]}, crs=CRS),
        df_work=gpd.GeoDataFrame({"person_id": person_ids,
                                  "location_id": [f"work_{p}" for p in person_ids],
                                  "geometry": [Point(p, 1) for p in person_ids]}, crs=CRS),
        df_education=gpd.GeoDataFrame({"person_id": person_ids,
                                       "location_id": [f"education_{p}" for p in person_ids],
                                       "geometry": [Point(p, 2) for p in person_ids]}, crs=CRS),
    )


def _activities(df_trips):
    """(person_id, activity_index, purpose): activity k is trip k's preceding purpose, the last one
    the last trip's following purpose (the convention of ``find_assignment_problems``)."""
    before = df_trips[["person_id", "trip_index", "preceding_purpose"]].set_axis(
        ["person_id", "activity_index", "purpose"], axis=1)
    last = df_trips[df_trips["is_last_trip"]]
    after = pd.DataFrame({"person_id": last["person_id"], "activity_index": last["trip_index"] + 1,
                          "purpose": last["following_purpose"]})
    return pd.concat([before, after]).sort_values(["person_id", "activity_index"]).reset_index(
        drop=True)


def _solve(df_trips):
    """Stand-in for the chain solver: one row per secondary activity of the (collapsed) chains, in
    the shape of ``results._extract_locations``. The place is named ``<purpose>@<person_id>`` and
    sits at ``(person_id, 10 + activity_index)``; the indices are those of the table it is given."""
    activities = _activities(df_trips)
    secondary = activities[~activities["purpose"].isin(PRIMARY_PURPOSES)]
    records = [(person_id, activity_index, f"{purpose}@{person_id}",
                Point(person_id, 10 + activity_index))
               for person_id, activity_index, purpose in secondary.itertuples(index=False)]
    return gpd.GeoDataFrame(pd.DataFrame.from_records(records, columns=LOCATION_COLUMNS), crs=CRS)


def _chain_of(df_trips, person_id=1):
    """A person's trips as (trip_index, preceding_purpose, following_purpose), in table order."""
    trips = df_trips[df_trips["person_id"] == person_id]
    return list(zip(trips["trip_index"], trips["preceding_purpose"], trips["following_purpose"]))


def _row(df_locations, person_id, activity_index):
    rows = df_locations[(df_locations["person_id"] == person_id)
                        & (df_locations["activity_index"] == activity_index)]
    assert len(rows) == 1, f"expected one row for ({person_id}, {activity_index}), got {len(rows)}"
    return rows.iloc[0]


def _collapse_solve_restore(*persons, eligible=None):
    df_trips, is_loop = _chains(*persons)
    person_ids = [person_id for person_id, _ in persons]
    eligible = set(person_ids) if eligible is None else eligible
    collapsed, index_map, loop_table = loops.collapse_loops(df_trips, is_loop, eligible)
    result = _solve(collapsed)
    restored = loops.restore_loops(result, index_map, loop_table, **_places(person_ids))
    return df_trips, collapsed, result, restored


def test_home_stroll_home_leaves_nothing_to_solve():
    df_trips, collapsed, result, restored = _collapse_solve_restore((1, "home S home"))

    assert _chain_of(collapsed) == [(0, "home", "home")]
    # The one trip left is the walk back, with its own times.
    assert collapsed["departure_time"].tolist() == [df_trips.loc[1, "departure_time"]]
    assert result.empty
    assert isinstance(restored, gpd.GeoDataFrame) and restored.crs == CRS
    stroll = _row(restored, 1, 1)
    assert stroll["location_id"] == "home_101" and stroll["geometry"].equals(Point(1, 0))
    assert stroll["loop_origin"] == "home" and len(restored) == 1


def test_a_person_whose_only_trip_leads_into_a_stroll_keeps_the_stroll():
    df_trips, collapsed, _, restored = _collapse_solve_restore((1, "home S"), (2, "home shop home"))

    # Person 1 has no trip left; person 2 has no loop and keeps its rows as they were.
    pd.testing.assert_frame_equal(collapsed, df_trips[df_trips["person_id"] == 2])
    stroll = _row(restored, 1, 1)
    assert stroll["location_id"] == "home_101" and stroll["geometry"].equals(Point(1, 0))
    assert stroll["loop_origin"] == "home"


def test_the_trip_after_a_stroll_starts_at_the_activity_before_it():
    df_trips, is_loop = _chains((1, "home S shop home"))
    df_before = df_trips.copy()

    collapsed, _, _ = loops.collapse_loops(df_trips, is_loop, {1})

    assert _chain_of(collapsed) == [(0, "home", "shop"), (1, "shop", "home")]
    kept_columns = ["mode", "departure_time", "arrival_time", "travel_time", "W_ZWECK", "W_ZWD"]
    assert (collapsed[kept_columns].to_numpy().tolist()
            == df_trips.loc[[1, 2], kept_columns].to_numpy().tolist())
    pd.testing.assert_frame_equal(df_trips, df_before)


def test_a_stroll_after_a_secondary_activity_copies_its_place():
    df_trips, is_loop = _chains((1, "work shop S home"))
    collapsed, index_map, loop_table = loops.collapse_loops(df_trips, is_loop, {1})
    result = _solve(collapsed)
    result_before = result.copy()

    restored = loops.restore_loops(result, index_map, loop_table, **_places([1]))

    assert _chain_of(collapsed) == [(0, "work", "shop"), (1, "shop", "home")]
    shop, stroll = _row(restored, 1, 1), _row(restored, 1, 2)
    assert shop["location_id"] == "shop@1" and shop["loop_origin"] is None
    assert stroll["location_id"] == "shop@1" and stroll["geometry"].equals(shop["geometry"])
    assert stroll["loop_origin"] == "secondary"
    pd.testing.assert_frame_equal(result, result_before)


@pytest.mark.parametrize("primary", ["work", "education"])
def test_a_stroll_after_work_copies_the_work_place(primary):
    _, _, _, restored = _collapse_solve_restore((1, f"home {primary} S home"))

    stroll = _row(restored, 1, 2)
    places = _places([1])[f"df_{primary}"]
    assert stroll["location_id"] == f"{primary}_1"
    assert stroll["geometry"].equals(places["geometry"].iloc[0])
    assert stroll["loop_origin"] == primary


def test_two_strolls_in_a_row_copy_the_same_place():
    df_trips, is_loop = _chains((1, "home shop S S home"))
    collapsed, index_map, loop_table = loops.collapse_loops(df_trips, is_loop, {1})
    restored = loops.restore_loops(_solve(collapsed), index_map, loop_table, **_places([1]))

    assert _chain_of(collapsed) == [(0, "home", "shop"), (1, "shop", "home")]
    assert collapsed["departure_time"].tolist() == df_trips.loc[[0, 3], "departure_time"].tolist()
    assert loop_table.to_dict("records") == [
        {"person_id": 1, "activity_index": 2, "origin_purpose": "secondary",
         "origin_activity_index": 1},
        {"person_id": 1, "activity_index": 3, "origin_purpose": "secondary",
         "origin_activity_index": 1},
    ]
    shop = _row(restored, 1, 1)
    for activity_index in (2, 3):
        stroll = _row(restored, 1, activity_index)
        assert stroll["location_id"] == "shop@1" and stroll["geometry"].equals(shop["geometry"])


def test_a_stroll_as_the_last_activity_of_the_day_is_restored():
    _, collapsed, _, restored = _collapse_solve_restore((1, "home shop S"))

    assert _chain_of(collapsed) == [(0, "home", "shop")]
    shop, stroll = _row(restored, 1, 1), _row(restored, 1, 2)
    assert stroll["location_id"] == "shop@1" and stroll["geometry"].equals(shop["geometry"])
    assert stroll["loop_origin"] == "secondary"


@pytest.mark.parametrize("start", ["leisure", "other"])
def test_a_stroll_as_the_first_activity_after_a_start_that_is_not_home(start):
    df_trips, collapsed, _, restored = _collapse_solve_restore((1, f"{start} S home"))

    # The walk home keeps its times and now starts at the plan's first activity.
    assert _chain_of(collapsed) == [(0, start, "home")]
    assert collapsed["departure_time"].tolist() == [df_trips.loc[1, "departure_time"]]
    first, stroll = _row(restored, 1, 0), _row(restored, 1, 1)
    assert first["location_id"] == f"{start}@1"
    assert stroll["location_id"] == f"{start}@1" and stroll["geometry"].equals(first["geometry"])
    assert stroll["loop_origin"] == "secondary"


def test_first_and_last_trip_flags_are_rebuilt():
    # The first and the last trip both lead into a stroll: the shop trip is first and last.
    df_trips, is_loop = _chains((1, "home S shop S"))

    collapsed, _, _ = loops.collapse_loops(df_trips, is_loop, {1})

    assert _chain_of(collapsed) == [(0, "home", "shop")]
    assert collapsed["is_first_trip"].tolist() == [True]
    assert collapsed["is_last_trip"].tolist() == [True]


def test_the_index_map_restores_every_original_activity_index():
    persons = [(1, "home S shop S S other S home"),   # loops after home, a shop and an other
               (2, "work leisure S escort S"),        # the plan ends with a stroll
               (3, "home shop home"),                 # eligible, no loop
               (4, "home S other home")]              # not eligible: the solver places its stroll
    df_trips, _, _, restored = _collapse_solve_restore(*persons, eligible={1, 2, 3})

    original = _activities(df_trips)
    original = original[~original["purpose"].isin(PRIMARY_PURPOSES)]
    keys = list(zip(restored["person_id"], restored["activity_index"]))
    assert len(keys) == len(set(keys))
    assert set(keys) == set(zip(original["person_id"], original["activity_index"]))

    purpose_of = dict(zip(zip(original["person_id"], original["activity_index"]),
                          original["purpose"]))
    placed = restored[restored["loop_origin"].isna()]
    for person_id, activity_index, location_id in zip(
            placed["person_id"], placed["activity_index"], placed["location_id"]):
        assert location_id == f"{purpose_of[(person_id, activity_index)]}@{person_id}"
    copied = restored[restored["loop_origin"].notna()]
    assert dict(zip(zip(copied["person_id"], copied["activity_index"]), copied["location_id"])) == {
        (1, 1): "home_101", (1, 3): "shop@1", (1, 4): "shop@1", (1, 6): "other@1",
        (2, 2): "leisure@2", (2, 4): "escort@2"}


def test_a_table_without_loops_comes_back_unchanged():
    df_trips, is_loop = _chains((1, "home shop home"), (2, "work leisure other home"))

    collapsed, index_map, loop_table = loops.collapse_loops(df_trips, is_loop, {1, 2})
    result = _solve(collapsed)
    restored = loops.restore_loops(result, index_map, loop_table, **_places([1, 2]))

    pd.testing.assert_frame_equal(collapsed, df_trips)
    assert index_map.empty and loop_table.empty
    pd.testing.assert_frame_equal(restored.drop(columns="loop_origin"), result)
    assert restored["loop_origin"].isna().all()


def test_persons_that_are_not_eligible_keep_their_loop():
    df_trips, is_loop = _chains((1, "home S home"), (2, "home S shop home"))

    collapsed, index_map, loop_table = loops.collapse_loops(df_trips, is_loop, {1})

    pd.testing.assert_frame_equal(collapsed[collapsed["person_id"] == 2],
                                  df_trips[df_trips["person_id"] == 2])
    assert set(index_map["person_id"]) == {1} and set(loop_table["person_id"]) == {1}


def test_only_linked_loops_and_the_first_loop_after_an_outside_stay_are_kept():
    df_trips, is_loop = _chains(
        (1, "home S shop S S home"),     # ordinary loops: all collapse
        (2, "home outside S S home"),    # the first loop after the outside stay is kept, the next
                                         # one collapses onto it
        (3, "home shop S home"),         # the stroll itself is linked: kept
        (4, "home outside S home"))      # linked AND after an outside stay: counted once, as linked
    # (3, 1) is a linked activity that is no loop: it keeps nothing.
    linked_activity_keys = {(3, 1), (3, 2), (4, 2)}

    to_collapse, kept = loops.select_collapsible_loops(df_trips, is_loop, linked_activity_keys)

    assert to_collapse.index.equals(df_trips.index) and to_collapse.dtype == bool
    collapsing = df_trips[to_collapse]
    assert sorted(zip(collapsing["person_id"], collapsing["trip_index"] + 1)) == [
        (1, 1), (1, 3), (1, 4), (2, 3)]
    assert kept == {"kept_loop_is_linked_activity": 2, "kept_after_outside": 1}
    # The second loop after the outside stay copies the kept loop's place, not the gate's.
    _, _, loop_table = loops.collapse_loops(df_trips, to_collapse)
    assert loop_table[loop_table["person_id"] == 2].to_dict("records") == [
        {"person_id": 2, "activity_index": 3, "origin_purpose": "secondary",
         "origin_activity_index": 2}]


#: Person 1 is collapsed (activities 0 home, 1 S, 2 shop, 3 S, 4 S, 5 other, 6 home: kept 0, 2, 5
#: and 6 at the local indices 0 to 3); person 2 keeps its loop and its indices.
TRANSLATION_CHAINS = ((1, "home S shop S S other home"), (2, "home S shop home"))


def _translation_index_map():
    df_trips, is_loop = _chains(*TRANSLATION_CHAINS)
    collapsed, index_map, loop_table = loops.collapse_loops(df_trips, is_loop, {1})
    return collapsed, index_map, loop_table


def _links(rows):
    """Passive joint links from ``(child, child activity, adult, adult activity)``."""
    return pd.DataFrame(rows, columns=["child_person_id", "child_activity_index",
                                       "adult_person_id", "adult_activity_index"]).assign(
        adult_purpose="shop")


def test_anchors_rows_and_both_link_sides_move_to_the_collapsed_chain_and_back():
    collapsed, index_map, loop_table = _translation_index_map()
    original_of = dict(zip(zip(index_map["person_id"], index_map["local_activity_index"]),
                           index_map["activity_index"]))

    def to_original(person_id, local_activity_index):
        # Persons that were not collapsed are not in the map and keep their index.
        return original_of.get((person_id, local_activity_index), local_activity_index)

    anchors = {(1, 2): Point(1, 2), (1, 5): Point(1, 5), (2, 2): Point(2, 2)}
    local_anchors = loops.to_collapsed_anchor_keys(anchors, index_map)
    assert local_anchors == {(1, 1): Point(1, 2), (1, 2): Point(1, 5), (2, 2): Point(2, 2)}
    assert {(p, to_original(p, a)): point for (p, a), point in local_anchors.items()} == anchors

    links = _links([(1, 5, 2, 2), (2, 2, 1, 2), (2, 1, 1, 6)])
    local_links = loops.to_collapsed_links(links, index_map)
    # Each side moves with its own person's map.
    assert local_links[["child_activity_index", "adult_activity_index"]].to_numpy().tolist() == [
        [2, 2], [2, 1], [1, 3]]
    for side in ("child", "adult"):
        back = [to_original(p, a) for p, a in zip(local_links[f"{side}_person_id"],
                                                    local_links[f"{side}_activity_index"])]
        assert back == links[f"{side}_activity_index"].tolist()
    pd.testing.assert_frame_equal(local_links.drop(columns=["child_activity_index",
                                                            "adult_activity_index"]),
                                  links.drop(columns=["child_activity_index",
                                                      "adult_activity_index"]))

    # Location rows: to the collapsed chain, through the solve-side restore and back.
    rows = gpd.GeoDataFrame(pd.DataFrame.from_records(
        [(1, 5, "other@1", Point(1, 15)), (1, 2, "shop@1", Point(1, 12)),
         (2, 2, "shop@2", Point(2, 12)), (2, 1, "leisure@2", Point(2, 11))],
        columns=LOCATION_COLUMNS), crs=CRS)
    rows_before = rows.copy()
    local_rows = loops.to_collapsed_activity_rows(rows, index_map, what="test rows")
    assert local_rows["activity_index"].tolist() == [2, 1, 2, 1]
    assert local_rows["activity_index"].dtype == rows["activity_index"].dtype
    pd.testing.assert_frame_equal(rows, rows_before)
    restored = loops.restore_loops(local_rows, index_map, loop_table, **_places([1, 2]))
    pd.testing.assert_frame_equal(
        pd.DataFrame(restored[restored["loop_origin"].isna()].drop(columns="loop_origin")),
        pd.DataFrame(rows))


@pytest.mark.parametrize("translate", [
    lambda index_map: loops.to_collapsed_anchor_keys({(1, 3): Point(0, 0)}, index_map),
    lambda index_map: loops.to_collapsed_activity_rows(
        pd.DataFrame({"person_id": [2, 1], "activity_index": [1, 3]}), index_map,
        what="portal rows"),
    lambda index_map: loops.to_collapsed_links(_links([(1, 3, 2, 2)]), index_map),
    lambda index_map: loops.to_collapsed_links(_links([(2, 2, 1, 3)]), index_map),
], ids=["anchor_key", "location_row", "link_child_side", "link_adult_side"])
def test_an_anchored_or_linked_activity_taken_out_as_a_loop_raises_naming_it(translate):
    # Activity 3 of person 1 is a collapsed stroll: an anchor or link on it has nowhere to go.
    _, index_map, _ = _translation_index_map()

    with pytest.raises(ValueError, match=r"\[secondary_loops\].*\(1, 3\)"):
        translate(index_map)


def test_flags_in_another_row_order_give_the_same_collapse():
    df_trips, is_loop = _chains((1, "home S shop S S home"), (2, "work S home"))
    shuffled = is_loop.sample(frac=1.0, random_state=3)
    assert not shuffled.index.equals(is_loop.index)

    expected = loops.collapse_loops(df_trips, is_loop, {1, 2})
    from_shuffled = loops.collapse_loops(df_trips, shuffled, {1, 2})

    for frame, expected_frame in zip(from_shuffled, expected):
        pd.testing.assert_frame_equal(frame, expected_frame)


def test_a_chain_that_does_not_start_at_trip_index_0_keeps_its_offset():
    df_trips, is_loop = _chains((1, "home S shop home"))
    df_trips["trip_index"] += 5

    collapsed, index_map, loop_table = loops.collapse_loops(df_trips, is_loop, {1})
    restored = loops.restore_loops(_solve(collapsed), index_map, loop_table, **_places([1]))

    assert _chain_of(collapsed) == [(5, "home", "shop"), (6, "shop", "home")]
    assert index_map[["local_activity_index", "activity_index"]].to_numpy().tolist() == [
        [5, 5], [6, 7], [7, 8]]
    assert loop_table.to_dict("records") == [
        {"person_id": 1, "activity_index": 6, "origin_purpose": "home",
         "origin_activity_index": 5}]
    # The shop, solved at index 6 of the collapsed chain, is activity 7 of the original one.
    assert _row(restored, 1, 7)["location_id"] == "shop@1"
    assert _row(restored, 1, 6)["location_id"] == "home_101"


def _flag_on_trip(index):
    def damage(df_trips, is_loop):
        is_loop = is_loop.copy()
        is_loop.iloc[index] = True
        return df_trips, is_loop
    return damage


@pytest.mark.parametrize("damage, message", [
    (lambda df, flags: (df, flags.set_axis(flags.index + 100)), "is_loop"),
    (_flag_on_trip(-1), "into a home, work or education activity"),
    (lambda df, flags: (df.assign(trip_index=[0, 2, 3]), flags), "contiguous"),
    (lambda df, flags: (df, pd.Series([pd.NA, True, False], index=flags.index, dtype="boolean")),
     r"\[secondary_loops\].*missing"),
], ids=["flags_on_another_index", "loop_into_home", "gap_in_trip_index", "missing_flag"])
def test_collapse_rejects_inputs_it_cannot_collapse(damage, message):
    df_trips, is_loop = damage(*_chains((1, "home S shop home")))

    with pytest.raises(ValueError, match=message):
        loops.collapse_loops(df_trips, is_loop, {1})


def _with_unknown_row(df_locations):
    extra = gpd.GeoDataFrame(pd.DataFrame.from_records(
        [(1, 9, "shop@1", Point(1, 19))], columns=LOCATION_COLUMNS), crs=CRS)
    return pd.concat([df_locations, extra], ignore_index=True)


@pytest.mark.parametrize("chain, damage, message", [
    ("work shop S home", lambda inputs: inputs.update(df_locations=inputs["df_locations"].iloc[:0]),
     r"secondary.*\(1, 2, 1\)"),
    ("home S home", lambda inputs: inputs.update(df_home=inputs["df_home"].iloc[:0]),
     r"home.*\(1, 1, 0\)"),
    ("home work S home", lambda inputs: inputs.update(df_work=inputs["df_work"].iloc[:0]),
     r"work.*\(1, 2, 1\)"),
    ("home shop S home",
     lambda inputs: inputs.update(df_locations=_with_unknown_row(inputs["df_locations"])),
     "index map"),
    ("home S home", lambda inputs: inputs.update(df_home=inputs["df_home"].to_crs("EPSG:4326")),
     "CRS"),
    ("home shop S home",
     lambda inputs: inputs.update(df_locations=inputs["df_locations"].assign(activity_index=1.5)),
     r"activity_index.*no whole number.*1\.5"),
    # The solver rows are not remapped: the shop keeps index 1, where the stroll goes back.
    ("home S shop home", lambda inputs: inputs.update(index_map=inputs["index_map"].iloc[:0]),
     r"occur twice.*\(1, 1\)"),
], ids=["secondary_origin_without_result_row", "home_origin_without_home",
        "work_origin_without_work", "result_row_unknown_to_the_index_map", "places_in_another_crs",
        "activity_index_not_a_whole_number", "rows_not_remapped"])
def test_restore_raises_when_a_place_is_missing_or_does_not_fit(chain, damage, message):
    df_trips, is_loop = _chains((1, chain))
    collapsed, index_map, loop_table = loops.collapse_loops(df_trips, is_loop, {1})
    inputs = dict(df_locations=_solve(collapsed), index_map=index_map, loops=loop_table,
                  **_places([1]))
    damage(inputs)

    with pytest.raises(ValueError, match=message):
        loops.restore_loops(inputs.pop("df_locations"), inputs.pop("index_map"),
                            inputs.pop("loops"), **inputs)


def test_restore_reads_whole_activity_indices_of_an_object_column_as_integers():
    # A concat with an empty solver frame (object columns) hands the restore an object column.
    df_trips, is_loop = _chains((1, "home shop S home"))
    collapsed, index_map, loop_table = loops.collapse_loops(df_trips, is_loop, {1})
    solved = _solve(collapsed)

    restored = loops.restore_loops(solved.astype({"activity_index": object}), index_map,
                                   loop_table, **_places([1]))

    assert restored["activity_index"].dtype == np.int64
    assert restored["activity_index"].tolist() == [1, 2]


def test_restore_accepts_places_without_a_crs_and_says_so(caplog):
    # primary.locations builds its work and education frames without setting a CRS.
    df_trips, is_loop = _chains((1, "home work S home"))
    collapsed, index_map, loop_table = loops.collapse_loops(df_trips, is_loop, {1})
    places = _places([1])
    for primary in ("work", "education"):
        places[f"df_{primary}"] = gpd.GeoDataFrame(
            {"person_id": [1], "location_id": [f"{primary}_1"], "geometry": [Point(1, 1)]})
        assert places[f"df_{primary}"].crs is None

    with caplog.at_level(logging.INFO, logger=loops.logger.name):
        restored = loops.restore_loops(_solve(collapsed), index_map, loop_table, **places)

    assert _row(restored, 1, 2)["location_id"] == "work_1" and restored.crs == CRS
    crs_records = [record.getMessage() for record in caplog.records
                   if record.levelno == logging.INFO and "CRS" in record.getMessage()]
    assert len(crs_records) == 1
    assert "work" in crs_records[0] and "education" in crs_records[0]
    assert "home" not in crs_records[0]


@pytest.mark.parametrize("damage, message", [
    (lambda trips: trips.drop(columns="wegkm_imp"), "wegkm_imp"),
    (lambda trips: trips[trips["trip_index"] != 0], "no trip into them"),
    (lambda trips: trips.assign(wegkm_imp=9994.0), "design codes"),
])
def test_the_diary_lengths_of_loops_refuse_trips_they_cannot_read(damage, message):
    """A loop's diary length must come from its own trip in, read through the shared MiD
    length guard; a missing column, a missing trip or a MiD design code stops the stage."""
    df_trips, is_loop = _chains((1, "home S home"))
    df_trips = df_trips.assign(is_synthetic_closure=False, wegkm_imp=1.5)
    _, _, loop_table = loops.collapse_loops(df_trips, is_loop, {1})

    with pytest.raises(ValueError, match=message):
        loops.loop_diary_distances(damage(df_trips), loop_table)
