"""Tests for the overnight open-day-end decision (braunschweig.popsim.overnight_open_end)."""
import logging

import pandas as pd
import pytest

from braunschweig.popsim import overnight_open_end as ooe


def _answers(rows):
    return pd.DataFrame(rows, columns=["H_ID", "P_ID", "P_STREISE"])


def _facts(rows):
    """rows: (H_ID, P_ID, ends_at_home, n_direct_legs, first_so1, starts_arriving_home)."""
    df = pd.DataFrame(
        rows,
        columns=["H_ID", "P_ID", "ends_at_home", "n_direct_legs", "first_so1", "starts_arriving_home"],
    )
    return df.set_index(["H_ID", "P_ID"])


def _flags(codes):
    rows = [(1, i + 1, c) for i, c in enumerate(codes)]
    return ooe.overnight_trip_flags(_answers(rows))


def test_codes_2_and_3_are_overnight_1_and_609_are_not():
    flags = _flags([2, 3, 1, 609])
    assert flags.tolist() == [True, True, False, False]
    assert flags.name == "overnight_trip"
    assert flags.index.names == ["H_ID", "P_ID"]


def test_child_inherits_from_any_household_member():
    flags = ooe.overnight_trip_flags(_answers([(1, 1, 2), (1, 2, 1), (1, 3, 402)]))
    assert flags.loc[(1, 3)]
    assert not flags.loc[(1, 2)]


def test_child_without_flagged_member_is_not_overnight_and_counted(caplog):
    with caplog.at_level(logging.INFO):
        flags = ooe.overnight_trip_flags(_answers([(1, 1, 1), (1, 2, 402), (2, 1, 3), (2, 2, 402)]))
    assert not flags.loc[(1, 2)]
    assert flags.loc[(2, 2)]
    text = caplog.text
    assert "[popsim.overnight_open_end]" in text
    assert "child without flagged member 1" in text


def test_no_usable_answer_codes_are_false_and_counted(caplog):
    with caplog.at_level(logging.INFO):
        flags = _flags([9, 610, 2])
    assert flags.tolist() == [False, False, True]
    assert "no usable answer 2" in caplog.text


def test_unknown_code_raises():
    with pytest.raises(ValueError) as exc:
        _flags([5, 5, 2])
    assert "5" in str(exc.value) and "2" in str(exc.value)


def _flag_true(h=1, p=1):
    return pd.Series([True], index=pd.MultiIndex.from_tuples([(h, p)], names=["H_ID", "P_ID"]), name="overnight_trip")


def test_departure_day_ending_away_keeps_open():
    facts = _facts([(1, 1, False, 3, 1, False)])
    out = ooe.keeps_open_end(facts, _flag_true())
    assert out.tolist() == [True]
    assert out.name == "keeps_open_end"


def test_middle_day_stays_closed():
    facts = _facts([(1, 1, False, 3, 2, False)])
    assert ooe.keeps_open_end(facts, _flag_true()).tolist() == [False]


def test_arrive_home_then_leave_again_keeps_open():
    facts = _facts([(1, 1, False, 3, 2, True)])
    assert ooe.keeps_open_end(facts, _flag_true()).tolist() == [True]


def test_unknown_start_code_is_not_a_middle_day():
    facts = _facts([(1, 1, False, 3, 809, False)])
    assert ooe.keeps_open_end(facts, _flag_true()).tolist() == [True]


def test_day_ending_at_home_or_without_direct_legs_is_never_kept_open():
    facts = _facts([(1, 1, True, 3, 1, False), (1, 2, False, 0, 1, False)])
    idx = facts.index
    overnight = pd.Series([True, True], index=idx, name="overnight_trip")
    assert ooe.keeps_open_end(facts, overnight).tolist() == [False, False]


def test_fact_row_without_an_answer_raises():
    facts = _facts([(1, 1, False, 3, 1, False), (9, 9, False, 3, 1, False)])
    with pytest.raises(KeyError) as exc:
        ooe.keeps_open_end(facts, _flag_true())
    assert "1" in str(exc.value)


def test_answer_row_without_facts_is_unused():
    facts = _facts([(1, 1, False, 3, 1, False)])
    overnight = pd.Series(
        [True, True], index=pd.MultiIndex.from_tuples([(1, 1), (5, 5)], names=["H_ID", "P_ID"]),
        name="overnight_trip",
    )
    assert ooe.keeps_open_end(facts, overnight).index.tolist() == [(1, 1)]


def test_attach_src_keeps_open_end_matches_keeps_open_end():
    facts = _facts([
        (1, 1, False, 3, 1, False),
        (1, 2, False, 3, 2, False),
        (2, 1, True, 2, 1, False),
        (3, 1, False, 4, 2, True),
    ])
    overnight = pd.Series(True, index=facts.index, name="overnight_trip")
    expected = ooe.keeps_open_end(facts, overnight)
    persons = pd.DataFrame({
        "source_H_ID": [3, 1, 1, 2, 1],
        "source_P_ID": [1, 1, 2, 1, 1],
        "src_ends_at_home": [False, False, False, True, False],
        "src_n_direct_legs": [4, 3, 3, 2, 3],
        "src_first_so1": [2, 1, 2, 1, 1],
        "src_starts_arriving_home": [True, False, False, False, False],
    })
    out = ooe.attach_src_keeps_open_end(persons, overnight)
    assert "src_keeps_open_end" not in persons.columns
    keys = list(zip(persons["source_H_ID"], persons["source_P_ID"]))
    assert out["src_keeps_open_end"].tolist() == [bool(expected.loc[k]) for k in keys]
