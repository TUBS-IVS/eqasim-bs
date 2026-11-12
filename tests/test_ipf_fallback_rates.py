"""Fallback-rate observability of the IPF household formation (audit package D).

The rate lines are logging only: each test checks that the PRIMARY path is taken on
representative input (fallback counts 0) and that the formed households do not depend
on the observability arguments.
"""
from __future__ import annotations

import re

import numpy as np
import pandas as pd
import pytest

from braunschweig.ipf import attributed as att

CFG = {"min_adult_age": 18}


def _persons(rows):
    """rows: list of (commune_id, household_size_label, age)."""
    return pd.DataFrame({
        "commune_id": [r[0] for r in rows],
        "household_size": [r[1] for r in rows],
        "age": [r[2] for r in rows],
        "weight": 1.0,
    })


def _types(rows):
    """rows: list of (commune_id, hh_size, hh_type, weight)."""
    return pd.DataFrame(rows, columns=["commune_id", "hh_size", "hh_type", "weight"])


def _adults(commune, size, n):
    return [(commune, size, 30 + i) for i in range(n)]


def _run(df, types, capsys):
    out = att.form_households_age_aware(df, 1, types, CFG)
    return out, capsys.readouterr().out


def _line(text, needle):
    lines = [ln for ln in text.splitlines() if needle in ln]
    assert len(lines) == 1, text
    return lines[0]


def test_tier_line_primary_path_and_outputs_unchanged(capsys):
    df = _persons(_adults("A", "2", 20))
    types = _types([("A", "2", "other_multi", 1.0)])
    _, text = _run(df, types, capsys)
    line = _line(text, "hh_type allocation")
    assert "10 primary" in line
    assert "0 drawn from scope-wide fallback, 0 forced" in line
    assert "WARNING" not in line

    # The optional stats argument never alters the allocation.
    shares = att._prepare_type_shares(types)
    plain = att._allocate_types_for_bucket(shares[2], shares[1], "A", "2", 10)
    counted = att._allocate_types_for_bucket(
        shares[2], shares[1], "A", "2", 10, stats={})
    assert plain == counted


@pytest.mark.parametrize("types_rows,key", [
    ([("B", "2", "other_multi", 1.0)], "scope-wide"),   # commune missing -> hh_size fallback
    ([("B", "5", "other_multi", 1.0)], "other_multi"),  # hh_size missing -> forced default
])
def test_tier_line_warns_when_fallback_dominates(capsys, types_rows, key):
    df = _persons(_adults("A", "2", 20))
    _, text = _run(df, _types(types_rows), capsys)
    line = _line(text, "hh_type allocation")
    assert "WARNING" in line
    expected = {"scope-wide": "10 drawn from scope-wide fallback, 0 forced",
                "other_multi": "0 drawn from scope-wide fallback, 10 forced"}[key]
    assert expected in line


def test_orphan_merge_is_reported_and_skip_is_counted(capsys):
    # 40 adults in size-2 households plus one lone child in a size-1 bucket
    # (all-children household) that must be merged into an adult household.
    rows = _adults("A", "2", 40) + [("A", "1", 5)]
    types = _types([("A", "2", "other_multi", 1.0), ("A", "1", "single", 1.0)])
    out, text = _run(_persons(rows), types, capsys)
    line = _line(text, "adult-less orphan households")
    assert "merged 1/41 persons" in line
    assert "0 orphan households (0 persons" in line
    assert "WARNING" not in line
    # the orphan really ended up in an adult household
    assert out.groupby("household_id")["age"].max().min() >= 18

    # No adult anywhere in the commune: the orphan stays, is counted, and warns.
    out2, text2 = _run(_persons([("C", "1", 5)]), _types(
        [("C", "1", "single", 1.0)]), capsys)
    line2 = _line(text2, "adult-less orphan households")
    assert "merged 0/1 persons" in line2
    assert "1 orphan households (1 persons" in line2
    assert "WARNING" in line2
    assert len(out2) == 1


@pytest.mark.parametrize("hh_type,size,adults,children,promoted,warn", [
    ("single_parent", "2", 4, 4, 0, False),    # child slots already match the pool
    ("other_multi", "3", 6, 3, 3, True),       # every child slot is a promotion
])
def test_child_slot_promotion_is_reported(capsys, hh_type, size, adults, children,
                                          promoted, warn):
    rows = ([("A", size, 30 + i) for i in range(adults)]
            + [("A", size, 5 + i % 3) for i in range(children)])
    df = _persons(rows)
    types = _types([("A", size, hh_type, 1.0)])
    _, text = _run(df, types, capsys)
    line = _line(text, "child slots")
    m = re.search(r"promoted to non-child shells ([\d,]+)", line)
    assert int(m.group(1).replace(",", "")) == promoted
    assert ("WARNING" in line) is warn


def test_legacy_assign_types_warns_above_threshold(capsys):
    hh = pd.DataFrame({
        "household_id": range(10),
        "commune_id": "A",
        "household_size": 2,
    })
    df = hh.copy()
    ok = _types([("A", "2", "couple", 1.0)])
    att._assign_household_types(df, ok, 1)
    assert "WARNING" not in _line(capsys.readouterr().out, "assigned hh_type")
    # commune absent from the Zensus frame -> every household on the fallback
    bad = _types([("B", "2", "couple", 1.0)])
    att._assign_household_types(df, bad, 1)
    line = _line(capsys.readouterr().out, "assigned hh_type")
    assert "WARNING" in line
    assert "10 drawn from scope-wide fallback" in line
