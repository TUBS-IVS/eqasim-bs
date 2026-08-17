"""Tests for the Task-2 MiD P9 per-Kreis employment_status blend-ready frame.

Feature #172 (per-Kreis employment_status popsim control) blends a MiD P9 side
with an SrV V_ERW side (Task 1, ``srv2023_employment_status_by_kreis.csv``,
columns ``code`` + the 7 employment_status class shares + ``n_unweighted``).
This task builds the MiD-P9 side in the SAME blend-ready shape via
``braunschweig.popsim.mid_p9.mid_p9_employment_status_by_kreis``, so a later
task can align and blend the two frames.

The P9 parsing itself is shared (not duplicated) with the existing
``braunschweig.analysis.population_validation.controls.employment_status_target``
/ ``employment_target`` loaders via the new ``braunschweig.popsim.mid_p9``
reader module; a regression test below pins ``employment_status_target``'s
values so the refactor stays behaviour-preserving.
"""
from __future__ import annotations

import pandas as pd
import pytest

from braunschweig.analysis.population_validation import controls as C
from braunschweig.popsim.attributes import EMPLOYMENT_STATUS_CATEGORIES
from braunschweig.popsim.mid_p9 import mid_p9_employment_status_by_kreis

DATA = "eqasim-data/data"

# Mirrors the header of the real committed
# eqasim-data/data/braunschweig/mid/mid2023_P9.csv (kreis label, ars5, the
# n_weighted/n_unweighted sample-size columns already carried by the source
# table, the 7 employment_status class percentage columns, then keine_angabe).
_P9_HEADER = (
    "kreis,ars5,n_weighted,n_unweighted,vollzeit,teilzeit,geringfuegig,"
    "sonstiges,erwerbstaetig_unspec,in_ausbildung,nicht_erwerbstaetig,keine_angabe\n"
)


def _write_p9(tmp_path, body: str) -> str:
    mid = tmp_path / "braunschweig" / "mid"
    mid.mkdir(parents=True)
    (mid / "mid2023_P9.csv").write_text(_P9_HEADER + body, encoding="utf-8")
    return str(tmp_path)


# A minimal 2-Kreis + 1 region-aggregate ("Gesamt"/"03ZGB") fixture, matching
# the real P9 file's row shape (kreis label "Gesamt" pairs with ars5 "03ZGB").
_FIXTURE_BODY = (
    "Gesamt,03ZGB,4982.0,10350.0,35.0,12.0,3.0,1.0,0.0,2.0,46.0,0.0\n"
    "Braunschweig,03101,1010.0,1902.0,35.0,12.0,3.0,1.0,0.0,2.0,47.0,0.0\n"
    "Wolfsburg,03103,286.0,874.0,29.0,11.0,3.0,0.0,0.0,1.0,55.0,0.0\n"
)


def test_frame_shape_and_normalisation(tmp_path):
    data_path = _write_p9(tmp_path, _FIXTURE_BODY)
    frame = mid_p9_employment_status_by_kreis(data_path)

    expected_columns = ["code", *EMPLOYMENT_STATUS_CATEGORIES, "n_unweighted"]
    assert list(frame.columns) == expected_columns

    # Every row's 7 class shares sum to 1.0 (within floating-point tolerance).
    row_sums = frame[list(EMPLOYMENT_STATUS_CATEGORIES)].sum(axis=1)
    assert (abs(row_sums - 1.0) < 1e-6).all()

    # code carries the 5-digit ARS Kreis codes plus one "Gesamt" aggregate row.
    assert set(frame["code"]) == {"Gesamt", "03101", "03103"}

    # n_unweighted is present and non-null for every row.
    assert frame["n_unweighted"].notna().all()


def test_gesamt_row_uses_the_source_tables_own_aggregate(tmp_path):
    """The 'Gesamt' row is the P9 table's OWN published region aggregate
    (kreis='Gesamt', ars5='03ZGB'), not a value re-derived here -- consistent
    with the "no invented reference values" rule: the source already publishes
    a real region-wide aggregate, so this reuses it rather than approximating
    a new one from the per-Kreis rows."""
    data_path = _write_p9(tmp_path, _FIXTURE_BODY)
    frame = mid_p9_employment_status_by_kreis(data_path)
    gesamt = frame[frame["code"] == "Gesamt"].iloc[0]
    # 35+12+3+1+0+2+46 = 99 (keine_angabe=0 excluded from the denominator).
    assert gesamt["vollzeit"] == pytest.approx(35.0 / 99.0)
    assert gesamt["n_unweighted"] == 10350


def test_n_unweighted_taken_from_source_column(tmp_path):
    """ASSUMPTION under test: n_unweighted is read verbatim from the P9 CSV's
    own n_unweighted column (the per-Kreis P9 respondent base), not a derived
    or invented constant."""
    data_path = _write_p9(tmp_path, _FIXTURE_BODY)
    frame = mid_p9_employment_status_by_kreis(data_path)
    by_code = frame.set_index("code")["n_unweighted"]
    assert by_code["03101"] == 1902
    assert by_code["03103"] == 874


def test_denominator_excludes_keine_angabe(tmp_path):
    """Same convention as employment_status_target: class shares are computed
    over the sum of the SEVEN class columns, excluding keine_angabe."""
    body = "Braunschweig,03101,1,1,35,12,3,1,0,2,46,5\n"
    data_path = _write_p9(tmp_path, body)
    frame = mid_p9_employment_status_by_kreis(data_path)
    row = frame[frame["code"] == "03101"].iloc[0]
    # denom = 35+12+3+1+0+2+46 = 99 (keine_angabe=5 excluded).
    assert row["vollzeit"] == pytest.approx(35.0 / 99.0)


def test_raises_on_non_positive_class_total(tmp_path):
    """No-silent-fallback (CLAUDE.md): a Kreis row whose seven class columns
    sum to <= 0 raises instead of silently producing a NaN/inf share."""
    body = "Braunschweig,03101,1,1,0,0,0,0,0,0,0,0\n"
    data_path = _write_p9(tmp_path, body)
    with pytest.raises(ValueError, match="03101"):
        mid_p9_employment_status_by_kreis(data_path)


def test_frame_on_real_p9_data_sums_to_one_and_has_gesamt():
    """End-to-end against the real committed MiD 2023 P9 table (not just a
    synthetic fixture), proving the PRIMARY reader path actually works on
    representative input (no-silent-fallback rule: test the primary method)."""
    frame = mid_p9_employment_status_by_kreis(DATA)
    assert not frame.empty
    assert "Gesamt" in set(frame["code"])
    row_sums = frame[list(EMPLOYMENT_STATUS_CATEGORIES)].sum(axis=1)
    assert (abs(row_sums - 1.0) < 1e-6).all()
    assert frame["n_unweighted"].gt(0).all()


# --- Regression: the DRY refactor of controls.employment_status_target must
# not change any value it returns (behaviour-preserving refactor). --------

def test_employment_status_target_unchanged_after_refactor(tmp_path):
    """Pins the exact values employment_status_target returns on a small
    fixture, independent of mid_p9_employment_status_by_kreis, so a shared-
    reader refactor of employment_status_target cannot silently change its
    output."""
    body = "Braunschweig,03101,1,1,35,12,3,1,0,2,46,5\n"
    data_path = _write_p9(tmp_path, body)
    tgt = C.employment_status_target(data_path)
    got = dict(zip(tgt["category"], tgt["target_share"]))
    # denom = 35+12+3+1+0+2+46 = 99 (keine_angabe=5 excluded), matching the
    # pre-refactor formula (class_value / substantive_row_total).
    expected = {
        "vollzeit": 35.0 / 99.0,
        "teilzeit": 12.0 / 99.0,
        "geringfuegig": 3.0 / 99.0,
        "sonstiges": 1.0 / 99.0,
        "erwerbstaetig_unspec": 0.0 / 99.0,
        "in_ausbildung": 2.0 / 99.0,
        "nicht_erwerbstaetig": 46.0 / 99.0,
    }
    assert got.keys() == expected.keys()
    for cat, expected_share in expected.items():
        assert got[cat] == pytest.approx(expected_share)
