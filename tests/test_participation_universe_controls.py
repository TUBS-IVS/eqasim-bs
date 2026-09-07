"""Age-range universes for Kreis attribute controls (Plan B, issue #368, task 1).

Covers the `max_age` field of `KreisAttributeControl` (Task 1's enabling mechanism for the
later education-by-age-range controls): the rendered seed expression gains an upper age
clause when `max_age` is set, and `controls_builder.person_total_by_kreis_age_range` derives
the matching per-Kreis person total from the single-year census age columns. The `max_age=None`
path must stay byte-identical to the pre-existing behaviour (no `max_age` field at all).
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from braunschweig.popsim import control_spec as cs
from braunschweig.popsim.kreis_attribute_control import KreisAttributeControl
from braunschweig.popsim.stage.controls_builder import (
    person_total_by_kreis_age_range, person_total_by_kreis_min_age)


def _entry(min_age=None, max_age=None):
    return KreisAttributeControl(
        name="edu_test", seed_column="education_flag", level="person",
        categories=(("edu", "== 'edu'"), ("noedu", "== 'noedu'")),
        target_csv_relpath="x.csv", target_columns=("edu", "noedu"), tier="hard",
        min_age=min_age, max_age=max_age)


def test_max_age_renders_an_upper_age_clause_after_the_lower_one():
    exprs = [c.seed_expressions["mid"] for c in cs.attribute_kreis_controls([_entry(6, 17)])]
    assert exprs[0] == "(persons.education_flag == 'edu') & (persons.HP_ALTER >= 6) & (persons.HP_ALTER <= 17)"


def test_max_age_none_renders_byte_identical_to_before_the_field_existed():
    assert [c.seed_expressions["mid"] for c in cs.attribute_kreis_controls([_entry(14, None)])] == [
        "(persons.education_flag == 'edu') & (persons.HP_ALTER >= 14)",
        "(persons.education_flag == 'noedu') & (persons.HP_ALTER >= 14)"]
    assert cs.attribute_kreis_controls([_entry()])[0].seed_expressions["mid"] == "(persons.education_flag == 'edu')"


def _cells():
    cols = {f"{s}_AGE_{y}": [1.0, 2.0] for s in ("M", "F") for y in range(0, 101)}
    return pd.DataFrame(cols), pd.Series(["03101", "03102"])


def test_person_total_by_kreis_age_range_sums_only_the_band():
    cells, kreis = _cells()
    out = person_total_by_kreis_age_range(cells, kreis, 6, 17)
    assert out == {"03101": 2 * 12 * 1.0, "03102": 2 * 12 * 2.0}


def test_person_total_by_kreis_min_age_is_unchanged_by_the_delegation():
    cells, kreis = _cells()
    assert person_total_by_kreis_min_age(cells, kreis, 14) == person_total_by_kreis_age_range(cells, kreis, 14, 100)
    assert person_total_by_kreis_min_age(cells, kreis, 14)["03101"] == 2 * 87 * 1.0


def test_person_total_by_kreis_age_range_raises_without_any_band_column():
    cells, kreis = _cells()
    with pytest.raises(RuntimeError, match="age_range"):
        person_total_by_kreis_age_range(cells.drop(columns=[c for c in cells.columns if "_AGE_" in c]), kreis, 6, 17)


def test_person_total_by_kreis_min_age_error_names_itself_not_the_delegate():
    """person_total_by_kreis_min_age delegates to person_total_by_kreis_age_range and
    rewrites that helper's RuntimeError message to carry ITS OWN name (so a caller of the
    min_age entry point never sees an error naming a function it never called). This
    substitution is a plain string.replace on the delegate's message text: if that text is
    ever edited so the substring no longer matches, the replace silently becomes a no-op
    and the raised error would name the wrong function -- this test pins the substitution
    so such a drift fails loudly."""
    cells, kreis = _cells()
    with pytest.raises(RuntimeError, match="person_total_by_kreis_min_age") as excinfo:
        person_total_by_kreis_min_age(
            cells.drop(columns=[c for c in cells.columns if "_AGE_" in c]), kreis, 14)
    assert "person_total_by_kreis_age_range" not in str(excinfo.value)
