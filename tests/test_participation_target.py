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
