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


# --------------------------------------------------------------------------- #
# Task 2: the work_by_employment / education_flag SEED columns
#
# The three measured defects the new derivation must NOT reproduce (spec Plan B, #368):
#   1. compute_has_purpose_trip counts rbW legs (W_RBW == 1) the trip builder DROPS, so a
#      donor with only rbW work legs is seeded "has work" and realises no work trip;
#   2. it passes the 803/804 diary non-response codes through for later age-band
#      imputation, seeding deliberately immobile plan sources as mobile;
#   3. its education code set is static, while the realised plan additionally maps
#      W_ZWECK 13 to education when escort_passive_education is on.
# The functions below therefore use DIRECT legs only, no imputation, no code
# pass-through, and an education code set that follows the active flag.
# --------------------------------------------------------------------------- #

def _persons_wege():
    persons = pd.DataFrame({
        "H_ID": [1, 1, 2, 3], "P_ID": [1, 2, 1, 1], "HP_ALTER": [40, 12, 70, 30],
        "employment_status": ["vollzeit", "nicht_erwerbstaetig", "nicht_erwerbstaetig", "in_ausbildung"],
        "member_imputed": [False, False, False, False],
        "source_H_ID": [1, 1, 2, 1], "source_P_ID": [1, 2, 1, 1]})   # person 3/1 borrows 1/1's diary
    wege = pd.DataFrame({
        "H_ID": [1, 1, 1, 2, 2], "P_ID": [1, 1, 2, 1, 1], "W_ID": [1, 2, 1, 1, 2],
        "W_ZWECK": [1, 8, 3, 2, 8], "W_RBW": [0, 0, 0, 1, 1]})          # 2/1 has ONLY rbW work legs
    return persons, wege


def test_direct_purpose_leg_ignores_rbw_legs_and_never_passes_through_codes():
    from braunschweig.popsim.mid.participation import compute_has_direct_purpose_leg
    persons, wege = _persons_wege()
    flag = compute_has_direct_purpose_leg(persons, wege, {1, 2})
    assert flag.tolist() == [1, 0, 0, 0]      # 2/1's rbW work legs do not count; no 803/804 carry-through


def test_work_by_employment_labels_follow_employment_status_and_the_plan_source():
    from braunschweig.popsim.mid.participation import derive_work_by_employment_seed
    persons, wege = _persons_wege()
    out = derive_work_by_employment_seed(persons, wege)
    assert out["work_by_employment"].tolist() == [
        "employed_work", "nonemployed_nowork", "nonemployed_nowork", "employed_work"]
    # 3/1 is in_ausbildung (employed by decision Q5) and borrows 1/1's direct work leg


def test_work_by_employment_requires_employment_status_first():
    from braunschweig.popsim.mid.participation import derive_work_by_employment_seed
    persons, wege = _persons_wege()
    with pytest.raises(KeyError, match="employment_status"):
        derive_work_by_employment_seed(persons.drop(columns=["employment_status"]), wege)


def test_education_flag_counts_code_13_only_with_the_passive_escort_flag():
    from braunschweig.popsim.mid.participation import derive_education_flag_seed
    persons, wege = _persons_wege()
    wege13 = pd.concat([wege, pd.DataFrame({"H_ID": [2], "P_ID": [1], "W_ID": [3], "W_ZWECK": [13], "W_RBW": [0]})])
    assert derive_education_flag_seed(persons, wege13, escort_passive_education=False)["education_flag"].tolist() == ["noedu", "edu", "noedu", "noedu"]
    assert derive_education_flag_seed(persons, wege13, escort_passive_education=True)["education_flag"].tolist() == ["noedu", "edu", "edu", "noedu"]


def test_map_flag_from_plan_source_raises_on_an_unresolved_source():
    from braunschweig.popsim.mid.participation import map_flag_from_plan_source
    persons, _ = _persons_wege()
    real_flag = pd.Series([1, 0], index=pd.MultiIndex.from_arrays([[1, 1], [1, 2]]))  # 2/1 missing
    with pytest.raises(ValueError, match="absent from the donor frame"):
        map_flag_from_plan_source(persons, real_flag, household_id="H_ID", person_id="P_ID", name="work_by_employment")


def test_education_by_age_entry_names_are_derived_from_the_bounds():
    """Controller ruling R1: the education-by-age entry NAMES are derived from
    EDUCATION_AGE_BOUNDS, never re-listed, so a later task cannot register an entry name
    the bounds do not cover (or vice versa)."""
    from braunschweig.popsim.kreis_attribute_control import (
        EDUCATION_AGE_BOUNDS, EDUCATION_BY_AGE_ENTRY_NAMES, EDUCATION_FLAG_CATEGORIES,
        WORK_BY_EMPLOYMENT_CATEGORIES)
    assert EDUCATION_AGE_BOUNDS == {
        "education_0_5": (0, 5), "education_6_17": (6, 17), "education_18plus": (18, None)}
    assert EDUCATION_BY_AGE_ENTRY_NAMES == tuple(EDUCATION_AGE_BOUNDS)
    assert WORK_BY_EMPLOYMENT_CATEGORIES == (
        "employed_work", "employed_nowork", "nonemployed_work", "nonemployed_nowork")
    assert EDUCATION_FLAG_CATEGORIES == ("edu", "noedu")
