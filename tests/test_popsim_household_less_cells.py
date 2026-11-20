"""Persons of household-less 100 m cells move to the household cells of their square (#659)."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd
import pytest

# append, never insert(0): 'tests' at the front of sys.path would shadow the braunschweig package.
sys.path.append(str(Path(__file__).resolve().parent))

import popsim_control_build_fixture as fixture  # noqa: E402

from braunschweig.popsim import mid  # noqa: E402
from braunschweig.popsim import stage  # noqa: E402
from braunschweig.popsim.household_less_cells import move_persons_to_household_cells  # noqa: E402


def _frame(parents, **columns) -> pd.DataFrame:
    return pd.DataFrame({"parent": parents, **columns})


def test_persons_move_in_proportion_to_households():
    frame = _frame(["k"] * 3, age=[4.0, 6.0, 2.0])

    moved, report = move_persons_to_household_cells(
        frame, parent_col="parent", households=pd.Series([0, 3, 1]), person_cols=["age"])

    assert moved["age"].tolist() == pytest.approx([0.0, 9.0, 3.0], abs=1e-9)
    assert frame["age"].tolist() == [4.0, 6.0, 2.0]  # the input is not mutated
    assert report["donor_cells"] == 1
    assert report["persons_moved"] == pytest.approx(4.0)
    assert report["large_cells"] == 0
    assert report["parents_without_households"] == 0


def test_every_person_column_keeps_its_parent_total():
    frame = _frame(
        ["a", "a", "a", "b", "b", "b"],
        age=[3.0, 5.0, 7.0, 2.0, 0.0, 9.0],
        employed=[1.5, 2.5, 4.0, 0.7, 0.0, 3.1],  # not a threshold column
    )
    households = pd.Series([0, 2, 5, 4, 0, 1])

    moved, report = move_persons_to_household_cells(
        frame, parent_col="parent", households=households,
        person_cols=["age", "employed"], threshold_cols=["age"])

    for column in ("age", "employed"):
        assert moved.groupby("parent")[column].sum().to_numpy() == pytest.approx(
            frame.groupby("parent")[column].sum().to_numpy(), abs=1e-9)
    assert moved.loc[0, "employed"] == 0.0  # the non-threshold column moves with the donor
    assert report["donor_cells"] == 1  # cell 4 has 0 persons: not a donor


def test_large_cell_is_left_and_counted():
    frame = _frame(["k"] * 2, age=[50.0, 10.0])

    moved, report = move_persons_to_household_cells(
        frame, parent_col="parent", households=pd.Series([0, 4]), person_cols=["age"])

    assert moved["age"].tolist() == [50.0, 10.0]
    assert report["large_cells"] == 1
    assert report["large_cell_persons"] == pytest.approx(50.0)
    assert report["donor_cells"] == 0


def test_parent_without_households_is_left_and_counted():
    frame = _frame(["x", "x", "y", "y"], age=[3.0, 4.0, 1.0, 2.0])

    moved, report = move_persons_to_household_cells(
        frame, parent_col="parent", households=pd.Series([0, 0, 0, 5]),
        person_cols=["age"])

    assert moved["age"].tolist() == pytest.approx([3.0, 4.0, 0.0, 3.0])
    assert report["parents_without_households"] == 1
    assert report["persons_in_parents_without_households"] == pytest.approx(7.0)
    assert report["donor_cells"] == 1
    assert report["persons_moved"] == pytest.approx(1.0)


def test_threshold_uses_threshold_cols_only():
    # 15 persons in the age band, 10 more employed: above 20 over both, below over the age band.
    frame = _frame(["k"] * 2, age=[15.0, 6.0], employed=[10.0, 4.0])
    kwargs = dict(parent_col="parent", households=pd.Series([0, 2]),
                  person_cols=["age", "employed"])

    by_age, _ = move_persons_to_household_cells(frame, threshold_cols=["age"], **kwargs)
    by_all, report_all = move_persons_to_household_cells(frame, **kwargs)

    assert by_age["age"].tolist() == pytest.approx([0.0, 21.0])
    assert by_age["employed"].tolist() == pytest.approx([0.0, 14.0])
    assert by_all["age"].tolist() == [15.0, 6.0]
    assert report_all["large_cells"] == 1


def test_build_control_totals_is_unchanged_without_the_option():
    cells = fixture.make_cells()
    prepared = stage._add_aggregated_control_columns(
        cells, fixture.CONTROL_SOURCE, fixture.SEED_NAME, fixture.CONTROL_TIERS,
        **fixture.AGGREGATE_KWARGS)
    base_cols = fixture.base_columns()
    report: dict = {}

    built = mid.build_control_totals(
        prepared, prepared[["ZENSUS100m", "ZENSUS1km"]], base_cols,
        household_total_col=base_cols[0], person_cols=base_cols[1:3],
        move_persons_max=None, report=report)

    assert report == {}
    for name in fixture.GOLDEN_NAMES:
        pd.testing.assert_frame_equal(
            built[name], fixture.read_golden(name), check_exact=True,
            obj=f"control_totals_{name}")


def test_build_control_totals_moves_before_rounding():
    targets = pd.DataFrame({
        "ZENSUS100m": ["c0", "c1", "c2"],
        "HH": [0.0, 2.6, 1.4],
        "P1": [4.0, 6.0, 2.0],
        "P2": [1.5, 2.2, 0.3],
    })
    crosswalk = pd.DataFrame({"ZENSUS100m": ["c0", "c1", "c2"], "ZENSUS1km": ["k"] * 3})
    base_cols = ["HH", "P1", "P2"]

    plain = mid.build_control_totals(targets, crosswalk, base_cols)
    report: dict = {}
    moved = mid.build_control_totals(
        targets, crosswalk, base_cols, household_total_col="HH",
        person_cols=["P1", "P2"], threshold_cols=["P1"], move_persons_max=20.0,
        report=report)

    cells = moved["ZENSUS100m"].set_index("ZENSUS100m")
    # P2 after the move: [0, 3.325, 0.675] (sum 4.0) -> largest remainder [0, 3, 1].
    assert cells["P1_ZENSUS100m"].tolist() == [0, 9, 3]
    assert cells["P2_ZENSUS100m"].tolist() == [0, 3, 1]
    assert cells["HH_ZENSUS100m"].tolist() == plain["ZENSUS100m"]["HH_ZENSUS100m"].tolist()
    # The 1 km totals are the (rounded) float parent sums, unchanged by the move.
    totals = moved["ZENSUS1km"].iloc[0]
    assert (totals["P1_ZENSUS1km"], totals["P2_ZENSUS1km"]) == (12, 4)
    assert report["donor_cells"] == 1
    assert report["persons_moved"] == pytest.approx(4.0)


def test_build_control_totals_uses_the_integer_household_rule():
    # Parent k1: the float households 0.3 and 0.1 integerise to 0 (5.4 households in total
    # -> [0, 0, 5]), so both cells are donors although their float households are positive.
    # Parent k2: one plain donor next to two equal household cells.
    targets = pd.DataFrame({
        "ZENSUS100m": ["a0", "a1", "a2", "b0", "b1", "b2"],
        "HH": [0.3, 0.1, 5.0, 0.0, 2.0, 2.0],
        "P1": [4.0, 3.0, 10.0, 6.0, 1.0, 1.0],
    })
    crosswalk = pd.DataFrame({
        "ZENSUS100m": targets["ZENSUS100m"], "ZENSUS1km": ["k1"] * 3 + ["k2"] * 3})
    report: dict = {}

    built = mid.build_control_totals(
        targets, crosswalk, ["HH", "P1"], household_total_col="HH", person_cols=["P1"],
        move_persons_max=20.0, report=report)

    cells = built["ZENSUS100m"].set_index("ZENSUS100m")
    assert cells["HH_ZENSUS100m"].tolist() == [0, 0, 5, 0, 2, 2]
    assert cells["P1_ZENSUS100m"].tolist() == [0, 0, 17, 0, 4, 4]
    assert report["donor_cells"] == 3
    assert report["persons_moved"] == pytest.approx(4.0 + 3.0 + 6.0)
    assert report["parents_without_households"] == 0


def test_two_donors_in_one_parent_and_two_parents():
    frame = _frame(["a", "a", "a", "b", "b", "b"], age=[2.0, 3.0, 5.0, 4.0, 1.0, 1.0])

    moved, report = move_persons_to_household_cells(
        frame, parent_col="parent", households=pd.Series([0, 0, 5, 0, 1, 3]),
        person_cols=["age"])

    assert moved["age"].tolist() == pytest.approx([0.0, 0.0, 10.0, 0.0, 2.0, 4.0])
    assert report["donor_cells"] == 3
    assert report["persons_moved"] == pytest.approx(9.0)


@pytest.mark.parametrize("kwargs, match", [
    (dict(person_cols=[]), "person_cols"),
    (dict(person_cols=["P1"], threshold_cols=["P9"]), "P9"),
    (dict(person_cols=["P1"], move_persons_max=0.0), "move_persons_max"),
    (dict(person_cols=["P1"], move_persons_max=-5.0), "move_persons_max"),
])
def test_build_control_totals_rejects_a_bad_move_setup(kwargs, match):
    targets = pd.DataFrame({"ZENSUS100m": ["c0", "c1"], "HH": [0.0, 2.0], "P1": [1.0, 2.0]})
    crosswalk = pd.DataFrame({"ZENSUS100m": ["c0", "c1"], "ZENSUS1km": ["k"] * 2})
    kwargs = {"move_persons_max": 20.0, **kwargs}

    with pytest.raises(ValueError, match=match):
        mid.build_control_totals(
            targets, crosswalk, ["HH", "P1"], household_total_col="HH", **kwargs)


def test_report_separates_donor_kinds_and_counts_stuck_cells():
    # Square a: cell 0 has 0 published households, cell 2 has 0.4 that round to 0; both move to
    # cell 1. Square b has no household cell: its two household-less cells are stuck.
    frame = _frame(["a", "a", "a", "b", "b"], age=[4.0, 6.0, 2.0, 3.0, 1.0])

    _, report = move_persons_to_household_cells(
        frame, parent_col="parent", households=pd.Series([0, 3, 0, 0, 0]),
        published_households=pd.Series([0.0, 3.0, 0.4, 0.0, 0.0]), person_cols=["age"])

    assert report["donor_cells"] == 2
    assert report["donor_cells_published_zero"] == 1
    assert report["donor_cells_rounded_to_zero"] == 1
    assert report["stuck_cells"] == 2
    assert report["parents_without_households"] == 1
    assert report["persons_in_parents_without_households"] == pytest.approx(4.0)


def test_build_control_totals_keeps_the_rounded_parent_totals_despite_float_drift():
    # The person column sums to 11.5 before the move (rounds to 12) but to 11.499999999999998
    # after it in float arithmetic (would round to 11): the pre-move totals must be kept.
    targets = pd.DataFrame({
        "ZENSUS100m": ["c0", "c1", "c2", "c3"],
        "HH": [0.0, 1.0, 1.0, 1.0],
        "P": [3.3, 0.29999999999999927, 4.4, 3.5],
    })
    crosswalk = pd.DataFrame({"ZENSUS100m": list(targets["ZENSUS100m"]), "ZENSUS1km": ["k"] * 4})
    plain = mid.build_control_totals(targets, crosswalk, ["HH", "P"])
    report: dict = {}

    moved = mid.build_control_totals(
        targets, crosswalk, ["HH", "P"], household_total_col="HH", person_cols=["P"],
        move_persons_max=20.0, report=report)

    assert plain["ZENSUS1km"]["P_ZENSUS1km"].tolist() == [12]
    assert moved["ZENSUS1km"]["P_ZENSUS1km"].tolist() == [12]
    assert moved["ZENSUS100m"]["P_ZENSUS100m"].iloc[0] == 0
    assert report["max_parent_sum_change"] == 0
