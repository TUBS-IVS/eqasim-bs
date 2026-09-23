"""Model-side absence composition (issue #426): the same three-way household pattern split as
the SrV table srv2023_absence_composition_by_age_band.csv, computed on a pipeline persons table.

Synthetic persons only. Five households:
  hh 1 (adults 40/38, child 6): all absent_household            -> three whole_household
  hh 2 (adult 35 absent_individual, child 4 + adult 33 present)  -> adult alone
  hh 3 (adult 30 + child 2 absent_individual, adult 31 present)  -> adult alone (other adult home),
                                                                   child WITH an absent adult
  hh 4 (child 15 absent_individual, adults 45/44 present)        -> child, no adult away
  hh 5 (single 70 absent_household)                              -> whole_household
"""
import numpy as np
import pandas as pd
import pytest

from braunschweig.analysis.synthesis import absence_composition as M
from braunschweig.calibration import srv_absence as A


def _persons():
    return pd.DataFrame({
        "person_id": list(range(1, 14)),
        "household_id": [1, 1, 1, 2, 2, 2, 3, 3, 3, 4, 4, 4, 5],
        "age": [40, 38, 6, 35, 4, 33, 30, 2, 31, 15, 45, 44, 70],
        "day_absence_state": ["absent_household"] * 3
                             + ["absent_individual", "present", "present"]
                             + ["absent_individual", "absent_individual", "present"]
                             + ["absent_individual", "present", "present"]
                             + ["absent_household"],
    })


def _reference_with_children_row(n_absent: int, k_whole: int, k_with_adult: int, k_no_adult: int) -> pd.DataFrame:
    """A reference table of the committed shape with every row empty except '0-17'."""
    rows = []
    for band in list(A.AGE_BAND_LABELS) + [A.CHILDREN_ROW, A.ALL_BAND]:
        row = {"band": band, "age_min": 0, "age_max": 200, "n_absent_unweighted": 0,
               **{c: 0 for c in A.COMPOSITION_COUNT_COLUMNS}, **{c: np.nan for c in A.COMPOSITION_SHARE_COLUMNS}}
        if band == A.CHILDREN_ROW:
            counts = (k_whole, k_with_adult, k_no_adult)
            row.update({"n_absent_unweighted": n_absent,
                        **dict(zip(A.COMPOSITION_COUNT_COLUMNS, counts)),
                        **{c: k / n_absent for c, k in zip(A.COMPOSITION_SHARE_COLUMNS, counts)}})
        rows.append(row)
    return pd.DataFrame(rows, columns=A.COMPOSITION_COLUMNS)


def test_model_composition_children_and_all_rows():
    table = M.model_absence_composition_by_band(_persons()).set_index("band")
    assert list(table.index) == list(A.AGE_BAND_LABELS) + [A.CHILDREN_ROW, A.ALL_BAND]
    children = table.loc[A.CHILDREN_ROW]
    assert children["n_absent_unweighted"] == 3                       # ages 6, 2, 15
    assert children["n_whole_household_unweighted"] == 1              # child 6
    assert children["n_partial_with_absent_adult_unweighted"] == 1    # child 2 (adult 30 away)
    assert children["n_partial_no_absent_adult_unweighted"] == 1      # child 15
    assert children["p_whole_household"] == pytest.approx(1 / 3)
    everyone = table.loc[A.ALL_BAND]
    assert everyone["n_absent_unweighted"] == 8
    assert everyone["n_whole_household_unweighted"] == 4              # 40, 38, 6, 70
    assert everyone["n_partial_with_absent_adult_unweighted"] == 1    # child 2 only
    assert everyone["n_partial_no_absent_adult_unweighted"] == 3      # 35, 30, 15


def test_persons_to_absence_frame_raises_without_the_state_column():
    with pytest.raises(ValueError, match="day_absence_state"):
        M.persons_to_absence_frame(_persons().drop(columns=["day_absence_state"]))


def test_persons_to_absence_frame_raises_on_an_unknown_state():
    bad = _persons(); bad.loc[0, "day_absence_state"] = "away"
    with pytest.raises(ValueError, match="away"):
        M.persons_to_absence_frame(bad)


def test_persons_to_absence_frame_raises_when_nobody_is_absent():
    """A persons table with the column but no absent person means the draw is off or broken; a
    silent empty composition would look like a measurement."""
    nobody = _persons(); nobody["day_absence_state"] = "present"
    with pytest.raises(ValueError, match="no absent person"):
        M.persons_to_absence_frame(nobody)


def test_compare_composition_evaluates_the_children_row_against_the_wilson_bound():
    model = M.model_absence_composition_by_band(_persons())          # 0-17: 1/3 each
    reference = _reference_with_children_row(73, 35, 12, 26)         # Wilson: [.369,.592] [.097,.266] [.256,.471]
    table = M.compare_composition(model, reference)
    assert list(table.columns) == M.COMPARISON_COLUMNS
    children = table[table["band"] == A.CHILDREN_ROW].set_index("pattern")
    assert children["evaluated"].all() and not table[table["band"] != A.CHILDREN_ROW]["evaluated"].any()
    assert children.loc[A.PATTERN_WHOLE, "inside_interval"] is False              # 0.333 < 0.369
    assert children.loc[A.PATTERN_PARTIAL_WITH_ADULT, "inside_interval"] is False  # 0.333 > 0.266
    assert children.loc[A.PATTERN_PARTIAL_NO_ADULT, "inside_interval"] is True     # 0.256 <= 0.333 <= 0.471
    assert children.loc[A.PATTERN_WHOLE, "srv_ci_low"] == pytest.approx(0.368775, abs=1e-5)
    assert children.loc[A.PATTERN_WHOLE, "p_srv_unweighted"] == pytest.approx(35 / 73)
    # an empty reference row cannot be evaluated: no interval, no verdict, never a substituted False
    band = table[(table["band"] == "0-5") & (table["pattern"] == A.PATTERN_WHOLE)].iloc[0]
    assert np.isnan(band["srv_ci_low"]) and band["inside_interval"] is None


def test_compare_composition_raises_on_a_reference_with_missing_rows():
    model = M.model_absence_composition_by_band(_persons())
    with pytest.raises(ValueError, match="rows"):
        M.compare_composition(model, _reference_with_children_row(73, 35, 12, 26).iloc[:-1])


@pytest.mark.parametrize("bad_age", ["unknown", None, -1])
def test_persons_to_absence_frame_raises_on_an_invalid_age(bad_age):
    """Pipeline ages must be valid: a non-numeric, missing or negative age is a pipeline defect,
    never silently coerced to NaN (which would drop the person from every band)."""
    bad = _persons().astype({"age": object}); bad.loc[1, "age"] = bad_age
    with pytest.raises(ValueError, match="age") as error:
        M.persons_to_absence_frame(bad)
    assert "2" in str(error.value)                     # the offending person_id is named
