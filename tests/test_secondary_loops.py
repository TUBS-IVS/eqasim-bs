"""Tests for the stroll / dog-walk loop flag (issue #679, task C2).

The MiD 2023 diary records a stroll (W_ZWD 710) or a dog walk (711) as ONE trip whose length is
the whole loop. ``loops.flag_loops`` marks the synthetic leisure legs that are such loops: by the
donor's own detail code where it has one, else by a seeded draw from a probability estimated on
the MiD legs that carry the detail code.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim.purpose_subtype import leisure_spec
from braunschweig.synthesis.locations.secondary_chainsolvers import loops

TRAVEL_TIME_S = 1800.0  # band 3 of shop_subtype.tt_band ([1200, inf))
BAND_OF_TRAVEL_TIME = 3

WALK_HOME_HOME = ("walk", BAND_OF_TRAVEL_TIME, True, True)
WALK_AWAY_HOME = ("walk", BAND_OF_TRAVEL_TIME, False, True)


def _trips(rows):
    """Rows of (person_id, trip_index, mode, preceding, following, W_ZWECK, W_ZWD)."""
    return pd.DataFrame(
        rows,
        columns=["person_id", "trip_index", "mode", "preceding_purpose", "following_purpose",
                 "W_ZWECK", "W_ZWD"],
    ).assign(travel_time=TRAVEL_TIME_S)


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


def test_a_cell_without_an_estimate_uses_the_marginal_and_is_counted():
    df = _trips(_leisure_leg(1, "walk", 2202))
    probabilities = _probabilities(cells={WALK_AWAY_HOME: 0.0}, marginal={"walk": 1.0})

    flags, stats = loops.flag_loops(df, probabilities, np.random.RandomState(3))

    assert flags.iloc[0]
    assert stats["draws_from_marginal"]["walk"] == 1


def test_a_mode_without_any_estimate_raises_naming_the_mode():
    df = _trips(_leisure_leg(1, "bicycle", 2202))
    probabilities = _probabilities(marginal={"walk": 0.5})

    with pytest.raises(ValueError, match="bicycle"):
        loops.flag_loops(df, probabilities, np.random.RandomState(3))


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
