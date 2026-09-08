"""Tests for scripts/build_participation_universe_targets.py, the target builder for the
work_by_employment and education_by_age Kreis controls (Plan B, issue #368, task 5).

All fixtures are synthetic: no committed CSV under eqasim-data/ is read here (that is
covered by the one-off loader-contract check run manually against the real files, see
the task-5 report). Covers:

- the work target is exactly MARGIN (employment_status classes) x SrV conditional rate,
  independently recomputed by hand in each assertion (double-implementation numeric gate);
- Wolfsburg (03103) uses its OWN employment_status margin combined with the SrV
  region-total (03ZGB) conditional rate, and the source column records that;
- the education target is the SrV conditional share directly, with Wolfsburg and Gesamt
  both taking the SrV region-total (03ZGB) row for the requested age band;
- fail-fast guards: a missing employed-class column, a missing source file, no Kreis
  rows, a Kreis-code set that does not equal the expected 7 SrV Kreise, a relabelled
  region-total row, not-exactly-one region-total row, an unknown education entry name, a
  NaN conditional rate, a NaN margin class, and a duplicate ars5 key (fix round 1,
  items 1, 2, 3, 5, 8).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pytest  # noqa: E402

from braunschweig.popsim.kreis_attribute_control import WORK_BY_EMPLOYMENT_CATEGORIES  # noqa: E402

# Canonical employment_status category order (attributes.EMPLOYMENT_STATUS_CATEGORIES); used
# only to decide which columns end up in the synthetic CSV a test writes.
_EMP_STATUS_COLUMN_ORDER = (
    "vollzeit", "teilzeit", "geringfuegig", "sonstiges", "erwerbstaetig_unspec",
    "in_ausbildung", "nicht_erwerbstaetig",
)

# The 7 SrV-surveyed ZGB Kreise (spatial.ZGB8 minus Wolfsburg 03103); fix round 1's item-3
# completeness guard requires every one of them to be present in a builder's source aggregate,
# so a fixture testing anything ELSE about the builders must supply all 7 (filled with an
# arbitrary but valid default row where the test does not care about that Kreis's numbers).
_ZGB_KREIS_CODES = ("03101", "03102", "03151", "03153", "03154", "03157", "03158")

_DEFAULT_EMP_STATUS_ROW = dict(vollzeit=0.30, teilzeit=0.10, geringfuegig=0.05, sonstiges=0.01,
                                erwerbstaetig_unspec=0.0, in_ausbildung=0.02, nicht_erwerbstaetig=0.52)


def _full_emp_status(overrides: dict) -> dict:
    """A 9-row employment_status fixture (7 SrV Kreise + 03103 + Gesamt), the default row for
    every code not named in `overrides`."""
    rows = {code: dict(_DEFAULT_EMP_STATUS_ROW) for code in (*_ZGB_KREIS_CODES, "03103", "Gesamt")}
    rows.update(overrides)
    return rows


_DEFAULT_WORK_AGG_TAIL = (100, 50, 50, 0.5, 0.68, 0.03)  # n_unweighted..p_work_nonemployed filler


def _full_work_agg(overrides: dict) -> list:
    """An 8-row srv2023_work_by_employment_by_kreis.csv fixture (7 SrV Kreise + 03ZGB total),
    the default tail for every code not named in `overrides` (mapping code -> tail tuple)."""
    rows = [(code, "kreis", *overrides.get(code, _DEFAULT_WORK_AGG_TAIL)) for code in _ZGB_KREIS_CODES]
    rows.append(("03ZGB", "total", *overrides.get("03ZGB", _DEFAULT_WORK_AGG_TAIL)))
    return rows


_DEFAULT_EDU_ROW = (200, 0.5)  # (n_unweighted, p_education) filler


def _full_edu_agg(band: str, overrides: dict) -> list:
    """An 8-row srv2023_education_by_age_by_kreis.csv fixture for one band (7 SrV Kreise +
    03ZGB total), the default row for every code not named in `overrides`."""
    rows = [(code, "kreis", band, *overrides.get(code, _DEFAULT_EDU_ROW)) for code in _ZGB_KREIS_CODES]
    rows.append(("03ZGB", "total", band, *overrides.get("03ZGB", _DEFAULT_EDU_ROW)))
    return rows


def _write_inputs(tmp_path, *, emp_status=None, work_agg=None, edu_agg=None) -> Path:
    """Write the minimal committed inputs build_participation_universe_targets.py reads,
    under tmp_path (returned as the `data` root the builder functions expect).

    Only the sections the caller actually passes are written, so a test exercising
    build_education_by_age_target need not fabricate an employment_status target and
    vice versa -- the builders under test never read a file their own function does
    not need.

    emp_status: {ars5: {category: share, ...}, ...}. The written CSV's category columns
        are exactly the ones PRESENT across the given rows (in canonical
        attributes.EMPLOYMENT_STATUS_CATEGORIES order), so a test can omit a class to
        exercise the missing-employed-class guard.
    work_agg: [(code, level, n_unweighted, n_employed_unweighted, n_nonemployed_unweighted,
        employed_share, p_work_employed, p_work_nonemployed), ...] rows for
        srv2023_work_by_employment_by_kreis.csv.
    edu_agg: [(code, level, band, n_unweighted, p_education), ...] rows for
        srv2023_education_by_age_by_kreis.csv.
    """
    if emp_status is not None:
        present_cols = [c for c in _EMP_STATUS_COLUMN_ORDER if any(c in row for row in emp_status.values())]
        targets_dir = tmp_path / "targets"
        targets_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "# synthetic employment_status target for test_participation_universe_targets.py\n",
            "ars5,source,n_effective," + ",".join(present_cols) + "\n",
        ]
        for ars5, row in emp_status.items():
            values = ",".join(str(row[c]) for c in present_cols)
            lines.append(f"{ars5},test,100,{values}\n")
        (targets_dir / "target2026_employment_status_by_kreis.csv").write_text("".join(lines), encoding="utf-8")

    if work_agg is not None:
        srv_dir = tmp_path / "srv"
        srv_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "# synthetic srv work-by-employment aggregate for test_participation_universe_targets.py\n",
            "code,level,n_unweighted,n_employed_unweighted,n_nonemployed_unweighted,"
            "employed_share,p_work_employed,p_work_nonemployed\n",
        ]
        for row in work_agg:
            lines.append(",".join(str(v) for v in row) + "\n")
        (srv_dir / "srv2023_work_by_employment_by_kreis.csv").write_text("".join(lines), encoding="utf-8")

    if edu_agg is not None:
        srv_dir = tmp_path / "srv"
        srv_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "# synthetic srv education-by-age aggregate for test_participation_universe_targets.py\n",
            "code,level,band,n_unweighted,p_education\n",
        ]
        for row in edu_agg:
            lines.append(",".join(str(v) for v in row) + "\n")
        (srv_dir / "srv2023_education_by_age_by_kreis.csv").write_text("".join(lines), encoding="utf-8")

    return tmp_path


# --------------------------------------------------------------------------- work_by_employment
def test_work_cells_are_margin_times_conditional_and_partition_to_one(tmp_path):
    from scripts.build_participation_universe_targets import build_work_by_employment_target
    data = _write_inputs(
        tmp_path,
        emp_status=_full_emp_status({
            "03101": dict(vollzeit=0.30, teilzeit=0.10, geringfuegig=0.05, sonstiges=0.01,
                          erwerbstaetig_unspec=0.0, in_ausbildung=0.02, nicht_erwerbstaetig=0.52),
            "03103": dict(vollzeit=0.28, teilzeit=0.10, geringfuegig=0.02, sonstiges=0.0,
                          erwerbstaetig_unspec=0.0, in_ausbildung=0.01, nicht_erwerbstaetig=0.59),
            "Gesamt": dict(vollzeit=0.30, teilzeit=0.10, geringfuegig=0.05, sonstiges=0.01,
                           erwerbstaetig_unspec=0.0, in_ausbildung=0.02, nicht_erwerbstaetig=0.52),
        }),
        work_agg=_full_work_agg({
            "03101": (100, 50, 50, 0.5, 0.70, 0.04),
            "03ZGB": (100, 50, 50, 0.5, 0.68, 0.03),
        }))
    df = build_work_by_employment_target(data).set_index("ars5")
    m = 0.30 + 0.10 + 0.05 + 0.02  # EMPLOYED_EMPLOYMENT_STATUS_CLASSES, no sonstiges
    assert df.loc["03101", "employed_work"] == pytest.approx(round(m * 0.70, 4))
    assert df.loc["03101", "nonemployed_work"] == pytest.approx(round((1 - m) * 0.04, 4))
    assert df.loc["03103", "employed_work"] == pytest.approx(round((0.28 + 0.10 + 0.02 + 0.01) * 0.68, 4))
    assert df.loc["03103", "source"] == "employment_status_target x srv_region_total"
    assert (df[list(WORK_BY_EMPLOYMENT_CATEGORIES)].sum(axis=1) - 1.0).abs().max() < 1e-3  # loader tolerance
    assert set(df.index) == set(_ZGB_KREIS_CODES) | {"03103", "Gesamt"}


def test_missing_employed_class_column_raises(tmp_path):
    # in_ausbildung is dropped from every row -> the column itself is absent from the CSV.
    data = _write_inputs(
        tmp_path,
        emp_status={
            "03101": dict(vollzeit=0.30, teilzeit=0.10, geringfuegig=0.05, sonstiges=0.01,
                          erwerbstaetig_unspec=0.0, nicht_erwerbstaetig=0.54),
            "03103": dict(vollzeit=0.28, teilzeit=0.10, geringfuegig=0.02, sonstiges=0.0,
                          erwerbstaetig_unspec=0.0, nicht_erwerbstaetig=0.60),
            "Gesamt": dict(vollzeit=0.30, teilzeit=0.10, geringfuegig=0.05, sonstiges=0.01,
                           erwerbstaetig_unspec=0.0, nicht_erwerbstaetig=0.54),
        })
    from scripts.build_participation_universe_targets import build_work_by_employment_target
    with pytest.raises(ValueError, match="in_ausbildung"):
        build_work_by_employment_target(data)


def test_missing_employment_status_file_raises(tmp_path):
    from scripts.build_participation_universe_targets import build_work_by_employment_target
    with pytest.raises(FileNotFoundError):
        build_work_by_employment_target(tmp_path)


def test_missing_srv_work_aggregate_file_raises(tmp_path):
    data = _write_inputs(
        tmp_path,
        emp_status={"03101": dict(vollzeit=0.30, teilzeit=0.10, geringfuegig=0.05, sonstiges=0.01,
                                  erwerbstaetig_unspec=0.0, in_ausbildung=0.02, nicht_erwerbstaetig=0.52),
                    "03103": dict(vollzeit=0.28, teilzeit=0.10, geringfuegig=0.02, sonstiges=0.0,
                                  erwerbstaetig_unspec=0.0, in_ausbildung=0.01, nicht_erwerbstaetig=0.59),
                    "Gesamt": dict(vollzeit=0.30, teilzeit=0.10, geringfuegig=0.05, sonstiges=0.01,
                                   erwerbstaetig_unspec=0.0, in_ausbildung=0.02, nicht_erwerbstaetig=0.52)})
    from scripts.build_participation_universe_targets import build_work_by_employment_target
    with pytest.raises(FileNotFoundError):
        build_work_by_employment_target(data)


def test_no_kreis_rows_in_work_aggregate_raises(tmp_path):
    data = _write_inputs(
        tmp_path,
        emp_status={"03103": dict(vollzeit=0.28, teilzeit=0.10, geringfuegig=0.02, sonstiges=0.0,
                                  erwerbstaetig_unspec=0.0, in_ausbildung=0.01, nicht_erwerbstaetig=0.59),
                    "Gesamt": dict(vollzeit=0.30, teilzeit=0.10, geringfuegig=0.05, sonstiges=0.01,
                                   erwerbstaetig_unspec=0.0, in_ausbildung=0.02, nicht_erwerbstaetig=0.52)},
        work_agg=[("03ZGB", "total", 100, 50, 50, 0.5, 0.68, 0.03)])
    from scripts.build_participation_universe_targets import build_work_by_employment_target
    with pytest.raises(ValueError, match="Kreis"):
        build_work_by_employment_target(data)


# --------------------------------------------------------------------------- education_by_age
def test_education_target_is_the_conditional_share_with_wolfsburg_and_gesamt_from_the_total(tmp_path):
    from scripts.build_participation_universe_targets import build_education_by_age_target
    data = _write_inputs(
        tmp_path,
        edu_agg=_full_edu_agg("education_6_17", {"03101": (200, 0.91), "03ZGB": (2000, 0.90)}))
    df = build_education_by_age_target(data, "education_6_17").set_index("ars5")
    assert df.loc["03101", "edu"] == 0.91 and df.loc["03101", "noedu"] == pytest.approx(0.09)
    assert df.loc["03103", "edu"] == 0.90 and df.loc["Gesamt", "n_effective"] == 2000


def test_unknown_education_entry_name_raises(tmp_path):
    data = _write_inputs(
        tmp_path,
        edu_agg=[("03101", "kreis", "education_6_17", 200, 0.91), ("03ZGB", "total", "education_6_17", 2000, 0.90)])
    from scripts.build_participation_universe_targets import build_education_by_age_target
    with pytest.raises(ValueError, match="education_6_17"):
        build_education_by_age_target(data, "education_6_18")


def test_education_missing_region_total_raises(tmp_path):
    # Full 7-Kreis coverage (so the item-3 completeness guard does not fire first) but no
    # 03ZGB total row at all.
    rows = [(code, "kreis", "education_6_17", 200, 0.5) for code in _ZGB_KREIS_CODES]
    data = _write_inputs(tmp_path, edu_agg=rows)
    from scripts.build_participation_universe_targets import build_education_by_age_target
    with pytest.raises(ValueError, match="region-total"):
        build_education_by_age_target(data, "education_6_17")


# --------------------------------------------------------------------------- fix round 1
def test_missing_kreis_row_in_work_aggregate_names_the_missing_code(tmp_path):
    # ITEM 3: one of the 7 SrV Kreise (03151) absent -> the target must not silently ship
    # 6 rows instead of 7; the error must name the missing code.
    partial = [row for row in _full_work_agg({}) if row[0] != "03151"]
    data = _write_inputs(
        tmp_path,
        emp_status=_full_emp_status({}),
        work_agg=partial)
    from scripts.build_participation_universe_targets import build_work_by_employment_target
    with pytest.raises(ValueError, match=r"03151"):
        build_work_by_employment_target(data)


def test_missing_kreis_row_in_education_aggregate_names_the_missing_code(tmp_path):
    # ITEM 3, education side.
    partial = [row for row in _full_edu_agg("education_6_17", {}) if row[0] != "03158"]
    data = _write_inputs(tmp_path, edu_agg=partial)
    from scripts.build_participation_universe_targets import build_education_by_age_target
    with pytest.raises(ValueError, match=r"03158"):
        build_education_by_age_target(data, "education_6_17")


def test_nan_conditional_rate_raises_instead_of_reaching_the_written_target(tmp_path):
    # ITEM 1: a NaN p_work_nonemployed (the SrV aggregate's own header allows NaN when a
    # conditioning class is empty in a Kreis) must raise, never reach a written share where
    # _check_shares_sum_to_one's sum-based guard cannot detect it.
    data = _write_inputs(
        tmp_path,
        emp_status=_full_emp_status({}),
        work_agg=_full_work_agg({"03101": (100, 50, 50, 0.5, 0.70, "")}))  # "" -> NaN on read
    from scripts.build_participation_universe_targets import build_work_by_employment_target
    with pytest.raises(ValueError, match=r"p_work_nonemployed.*03101|03101.*p_work_nonemployed"):
        build_work_by_employment_target(data)


def test_check_shares_sum_to_one_rejects_nan_sum_directly():
    # ITEM 1, second half: _check_shares_sum_to_one itself must not let a NaN row pass just
    # because abs(NaN - 1.0) > tolerance is False in numpy.
    import pandas as pd
    from scripts.build_participation_universe_targets import _check_shares_sum_to_one
    df = pd.DataFrame({
        "ars5": ["03101", "03102"],
        "a": [0.5, float("nan")],
        "b": [0.5, 0.5],
    })
    with pytest.raises(ValueError, match="non-finite"):
        _check_shares_sum_to_one(df, ("a", "b"), "test_context")


def test_nan_margin_class_raises_naming_file_kreis_and_column(tmp_path):
    # ITEM 2: a blank (NaN) teilzeit for 03101 must not silently shrink the margin via
    # pandas' skipna=True .sum() default; it must raise, naming the column.
    data = _write_inputs(
        tmp_path,
        emp_status=_full_emp_status({
            "03101": dict(vollzeit=0.30, teilzeit="", geringfuegig=0.05, sonstiges=0.01,
                          erwerbstaetig_unspec=0.0, in_ausbildung=0.02, nicht_erwerbstaetig=0.62),
        }),
        work_agg=_full_work_agg({}))
    from scripts.build_participation_universe_targets import build_work_by_employment_target
    with pytest.raises(ValueError, match=r"teilzeit.*03101|03101.*teilzeit"):
        build_work_by_employment_target(data)


def test_relabelled_region_total_row_raises_for_work(tmp_path):
    # ITEM 5: the written header asserts the conditional came from 03ZGB; a total row under
    # any other code must not be accepted silently.
    relabelled = [(code, level, *tail) if code != "03ZGB" else ("03XYZ", level, *tail)
                  for code, level, *tail in _full_work_agg({})]
    data = _write_inputs(tmp_path, emp_status=_full_emp_status({}), work_agg=relabelled)
    from scripts.build_participation_universe_targets import build_work_by_employment_target
    with pytest.raises(ValueError, match="03ZGB"):
        build_work_by_employment_target(data)


def test_relabelled_region_total_row_raises_for_education(tmp_path):
    relabelled = [(code, level, band, *tail) if code != "03ZGB" else ("03XYZ", level, band, *tail)
                  for code, level, band, *tail in _full_edu_agg("education_6_17", {})]
    data = _write_inputs(tmp_path, edu_agg=relabelled)
    from scripts.build_participation_universe_targets import build_education_by_age_target
    with pytest.raises(ValueError, match="03ZGB"):
        build_education_by_age_target(data, "education_6_17")


def test_duplicate_ars5_in_employment_status_raises(tmp_path):
    # ITEM 8: a duplicate ars5 key would make emp_by_ars5.loc[ars5, ...] return a DataFrame
    # instead of a Series, silently summing the duplicate rows together instead of raising.
    targets_dir = tmp_path / "targets"
    targets_dir.mkdir(parents=True, exist_ok=True)
    header = "ars5,source,n_effective," + ",".join(_EMP_STATUS_COLUMN_ORDER) + "\n"
    row = "0.30,0.10,0.05,0.01,0.0,0.02,0.52"
    lines = [
        "# synthetic duplicated employment_status target\n", header,
        f"03101,test,100,{row}\n", f"03101,test,100,{row}\n",  # duplicate key
        f"03103,test,100,{row}\n", f"Gesamt,test,100,{row}\n",
    ]
    (targets_dir / "target2026_employment_status_by_kreis.csv").write_text("".join(lines), encoding="utf-8")
    data = _write_inputs(tmp_path, work_agg=_full_work_agg({}))
    from scripts.build_participation_universe_targets import build_work_by_employment_target
    with pytest.raises(ValueError, match="duplicate"):
        build_work_by_employment_target(data)
