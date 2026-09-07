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
  rows, not-exactly-one region-total row, and an unknown education entry name.
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
        emp_status={
            "03101": dict(vollzeit=0.30, teilzeit=0.10, geringfuegig=0.05, sonstiges=0.01,
                          erwerbstaetig_unspec=0.0, in_ausbildung=0.02, nicht_erwerbstaetig=0.52),
            "03103": dict(vollzeit=0.28, teilzeit=0.10, geringfuegig=0.02, sonstiges=0.0,
                          erwerbstaetig_unspec=0.0, in_ausbildung=0.01, nicht_erwerbstaetig=0.59),
            "Gesamt": dict(vollzeit=0.30, teilzeit=0.10, geringfuegig=0.05, sonstiges=0.01,
                           erwerbstaetig_unspec=0.0, in_ausbildung=0.02, nicht_erwerbstaetig=0.52),
        },
        work_agg=[
            ("03101", "kreis", 100, 50, 50, 0.5, 0.70, 0.04),
            ("03ZGB", "total", 100, 50, 50, 0.5, 0.68, 0.03),
        ])
    df = build_work_by_employment_target(data).set_index("ars5")
    m = 0.30 + 0.10 + 0.05 + 0.02  # EMPLOYED_EMPLOYMENT_STATUS_CLASSES, no sonstiges
    assert df.loc["03101", "employed_work"] == pytest.approx(round(m * 0.70, 4))
    assert df.loc["03101", "nonemployed_work"] == pytest.approx(round((1 - m) * 0.04, 4))
    assert df.loc["03103", "employed_work"] == pytest.approx(round((0.28 + 0.10 + 0.02 + 0.01) * 0.68, 4))
    assert df.loc["03103", "source"] == "employment_status_target x srv_region_total"
    assert (df[list(WORK_BY_EMPLOYMENT_CATEGORIES)].sum(axis=1) - 1.0).abs().max() < 1e-3  # loader tolerance
    assert set(df.index) == {"03101", "03103", "Gesamt"}


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
        edu_agg=[
            ("03101", "kreis", "education_6_17", 200, 0.91),
            ("03ZGB", "total", "education_6_17", 2000, 0.90),
        ])
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
    data = _write_inputs(tmp_path, edu_agg=[("03101", "kreis", "education_6_17", 200, 0.91)])
    from scripts.build_participation_universe_targets import build_education_by_age_target
    with pytest.raises(ValueError, match="region-total"):
        build_education_by_age_target(data, "education_6_17")
