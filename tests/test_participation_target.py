"""Tests for scripts/build_participation_target.py and the committed
target2026_<purpose>_participation_by_kreis.csv tables (Task 2 of feature #224).

Covers:
- required columns and the two-share partition (<purpose>_yes + <purpose>_no == 1);
- every SrV Kreis + Wolfsburg (03103) + Gesamt row is present;
- the Wolfsburg row equals the SrV region-total (03ZGB) share, source
  "srv_region_total" (SrV-only attribute, no MiD pattern transfer -- see brief);
- fail-fast guards mirror build_trip_class_target: missing source file, no Kreis
  rows, and not-exactly-one region-total row.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from scripts.build_participation_target import build_participation_target  # noqa: E402

DATA = REPO / "eqasim-data" / "data" / "braunschweig"
_SRV_SOURCE = DATA / "srv" / "srv2023_participation_by_kreis.csv"

_EXPECTED_ARS5 = {"03101", "03102", "03151", "03153", "03154", "03157", "03158", "03103", "Gesamt"}


def test_work_target_rows_and_partition():
    df = build_participation_target(DATA, "work")
    assert set(df.columns) == {"ars5", "source", "n_effective", "work_yes", "work_no"}
    assert (abs(df["work_yes"] + df["work_no"] - 1.0) < 1e-9).all()
    assert "03103" in set(df["ars5"]) and "Gesamt" in set(df["ars5"])
    assert _EXPECTED_ARS5 <= set(df["ars5"])
    assert len(df) == len(_EXPECTED_ARS5)


def test_wolfsburg_row_equals_srv_region_total_directly():
    """03103 is not covered by SrV; its share must be exactly the 03ZGB total
    row's work share (no MiD pattern transfer -- that is specific to trip_class
    immobility, not participation, per the documented DECISION)."""
    srv = pd.read_csv(_SRV_SOURCE, comment="#", dtype={"code": str})
    total_row = srv[srv["level"] == "total"].iloc[0]

    df = build_participation_target(DATA, "work").set_index("ars5")
    assert float(df.loc["03103", "work_yes"]) == pytest.approx(float(total_row["work"]), abs=1e-9)
    assert float(df.loc["03103", "work_yes"]) == pytest.approx(float(df.loc["Gesamt", "work_yes"]), abs=1e-9)
    assert df.loc["03103", "source"] == "srv_region_total"
    assert df.loc["Gesamt", "source"] == "srv"


def test_kreis_row_recomputed_independently_from_srv_source():
    # INDEPENDENT recomputation from the committed SrV source, never pinned to
    # the script's own output (double-implementation numeric gate).
    srv = pd.read_csv(_SRV_SOURCE, comment="#", dtype={"code": str})
    row = srv[(srv["level"] == "kreis") & (srv["code"] == "03101")].iloc[0]

    df = build_participation_target(DATA, "work").set_index("ars5")
    assert float(df.loc["03101", "work_yes"]) == pytest.approx(float(row["work"]), abs=1e-9)
    assert float(df.loc["03101", "work_no"]) == pytest.approx(1.0 - float(row["work"]), abs=1e-9)
    assert int(df.loc["03101", "n_effective"]) == int(row["n_unweighted"])
    assert df.loc["03101", "source"] == "srv"


def test_missing_source_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        build_participation_target(tmp_path, "work")


def test_no_kreis_rows_raises(tmp_path):
    (tmp_path / "srv").mkdir(parents=True)
    src = tmp_path / "srv" / "srv2023_participation_by_kreis.csv"
    src.write_text("code,level,n_unweighted,work,education,leisure\n03ZGB,total,10,0.3,0.2,0.4\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Kreis"):
        build_participation_target(tmp_path, "work")


def test_missing_or_duplicate_total_row_raises(tmp_path):
    (tmp_path / "srv").mkdir(parents=True)
    src = tmp_path / "srv" / "srv2023_participation_by_kreis.csv"
    src.write_text(
        "code,level,n_unweighted,work,education,leisure\n"
        "03101,kreis,100,0.3,0.2,0.4\n",
        encoding="utf-8")
    with pytest.raises(ValueError, match="region-total"):
        build_participation_target(tmp_path, "work")


# --------------------------------------------------------- source-contract guards (issue #405)
_SRV_KREIS_CODES = ("03101", "03102", "03151", "03153", "03154", "03157", "03158")


def _write_source(tmp_path, kreis_rows) -> Path:
    """Write a synthetic srv2023_participation_by_kreis.csv from `kreis_rows`
    ((code, n_unweighted) pairs) plus the 03ZGB region-total row, and return the data root."""
    (tmp_path / "srv").mkdir(parents=True, exist_ok=True)
    lines = ["code,level,n_unweighted,work,education,leisure,escort\n"]
    for code, n_unweighted in kreis_rows:
        lines.append(f"{code},kreis,{n_unweighted},0.3,0.2,0.4,0.1\n")
    lines.append("03ZGB,total,700,0.31,0.21,0.41,0.11\n")
    (tmp_path / "srv" / "srv2023_participation_by_kreis.csv").write_text("".join(lines), encoding="utf-8")
    return tmp_path


def _full_source_rows(overrides: dict):
    """All 8 expected Kreis rows: the 7 surveyed ones with 100 persons and Wolfsburg's zero
    row, with `overrides` (code -> n_unweighted) applied."""
    counts = {code: 100 for code in _SRV_KREIS_CODES}
    counts["03103"] = 0
    counts.update(overrides)
    return list(counts.items())


def test_the_committed_shape_builds(tmp_path):
    """The guards below must reject only broken sources -- this pins that the shipped shape
    (7 surveyed Kreise plus Wolfsburg's zero row) still passes all of them."""
    df = build_participation_target(_write_source(tmp_path, _full_source_rows({})), "work")
    assert set(df["ars5"]) == _EXPECTED_ARS5
    assert df.set_index("ars5").loc["03103", "source"] == "srv_region_total"


@pytest.mark.parametrize("bad_count", ["n/a", "", -5, 12.5])
def test_an_unreadable_kreis_count_raises_instead_of_becoming_a_fallback(tmp_path, bad_count):
    """A count that cannot be read is a BROKEN source, never an empty Kreis.

    The fallback is only valid for an explicit zero: coercing a missing, non-numeric, negative
    or fractional count to zero would route a corrupted Kreis onto the documented region-total
    substitution and ship a plausible-looking target built from an unusable source, with nothing
    in the output saying so (CLAUDE.md: no silent fallbacks).
    """
    data = _write_source(tmp_path, _full_source_rows({"03102": bad_count}))
    with pytest.raises(ValueError, match="n_unweighted"):
        build_participation_target(data, "work")


def test_a_duplicated_kreis_row_raises_instead_of_being_emitted_twice(tmp_path):
    """The completeness check is set-based, so a duplicate passes it; without an explicit
    uniqueness guard the duplicated row reaches the written target as an ambiguous ars5 key."""
    data = _write_source(tmp_path, _full_source_rows({}) + [("03102", 100)])
    with pytest.raises(ValueError, match="03102"):
        build_participation_target(data, "work")
