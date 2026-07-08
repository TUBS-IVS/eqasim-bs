"""Tests for scripts/build_trip_class_target.py and the committed
target2026_trip_class_by_kreis.csv (Task 1 of the 2026-07-08
trip-class-kreis-control plan).

Covers:
- the build script reproduces the committed CSV byte-identically from the
  committed SrV source (reproducibility);
- every row's four trips_* shares sum to 1 within the same rounding tolerance
  the other target2026_* tables use (1e-3, see test_kreis_control_stage_wiring's
  economic_status pin test);
- the Wolfsburg (03103) row equals the Gesamt row (documented ASSUMPTION);
- 03101 pins are recomputed INDEPENDENTLY from the committed SrV source table
  (renormalise trips_* over their own row sum) -- NEVER pinned to the script's
  own output (double-implementation numeric gate).
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import pandas as pd  # noqa: E402
import pytest  # noqa: E402

from scripts.build_trip_class_target import build_trip_class_target, TRIP_CLASS_COLUMNS  # noqa: E402

_DATA = REPO / "eqasim-data" / "data" / "braunschweig"
_SRV_SOURCE = _DATA / "srv" / "srv2023_trip_classes_by_kreis.csv"
_COMMITTED_CSV = _DATA / "targets" / "target2026_trip_class_by_kreis.csv"


@pytest.mark.skipif(not _SRV_SOURCE.exists(), reason="committed SrV source table not present")
def test_build_matches_committed_csv_byte_identical():
    """Rebuilding from the committed SrV source must reproduce the committed CSV
    exactly (data rows only; the header text is asserted separately below)."""
    rebuilt = build_trip_class_target(_DATA)
    rebuilt[list(TRIP_CLASS_COLUMNS)] = rebuilt[list(TRIP_CLASS_COLUMNS)].round(4)
    committed = pd.read_csv(_COMMITTED_CSV, comment="#", dtype={"ars5": str})
    pd.testing.assert_frame_equal(
        rebuilt.reset_index(drop=True), committed.reset_index(drop=True), check_dtype=False)


@pytest.mark.skipif(not _COMMITTED_CSV.exists(), reason="committed target CSV not present")
def test_every_row_sums_to_one_within_rounding_tolerance():
    df = pd.read_csv(_COMMITTED_CSV, comment="#")
    sums = df[list(TRIP_CLASS_COLUMNS)].sum(axis=1)
    assert ((sums - 1.0).abs() < 1e-3).all()


@pytest.mark.skipif(not _COMMITTED_CSV.exists(), reason="committed target CSV not present")
def test_wolfsburg_row_equals_gesamt_row():
    df = pd.read_csv(_COMMITTED_CSV, comment="#", dtype={"ars5": str}).set_index("ars5")
    wolfsburg = df.loc["03103", list(TRIP_CLASS_COLUMNS)]
    gesamt = df.loc["Gesamt", list(TRIP_CLASS_COLUMNS)]
    assert wolfsburg.tolist() == gesamt.tolist()
    assert df.loc["03103", "source"] == "srv_region_total_assumption"
    assert df.loc["Gesamt", "source"] == "srv"


@pytest.mark.skipif(not _COMMITTED_CSV.exists(), reason="committed target CSV not present")
@pytest.mark.skipif(not _SRV_SOURCE.exists(), reason="committed SrV source table not present")
def test_braunschweig_03101_pins_recomputed_independently_from_srv_source():
    # INDEPENDENT recomputation from the committed SrV source (never from the
    # script's own output): renormalise the raw SrV trips_* shares for 03101
    # over their own row sum, exactly as the plan's Global Constraints specify.
    srv = pd.read_csv(_SRV_SOURCE, comment="#", dtype={"code": str})
    row = srv[(srv["level"] == "kreis") & (srv["code"] == "03101")].iloc[0]
    raw = {c: float(row[c]) for c in TRIP_CLASS_COLUMNS}
    total = sum(raw.values())
    expected = {c: round(v / total, 4) for c, v in raw.items()}

    target = pd.read_csv(_COMMITTED_CSV, comment="#", dtype={"ars5": str}).set_index("ars5")
    actual = target.loc["03101", list(TRIP_CLASS_COLUMNS)].to_dict()
    for c in TRIP_CLASS_COLUMNS:
        assert actual[c] == pytest.approx(expected[c], abs=1e-4), c
    assert int(target.loc["03101", "n_effective"]) == int(row["n_unweighted"])
    assert target.loc["03101", "source"] == "srv"


def test_header_contains_the_three_documented_decisions():
    text = _COMMITTED_CSV.read_text(encoding="utf-8")
    assert "ASSUMPTION (universe)" in text
    assert "0.63pp" in text
    assert "DECISION (level anchoring)" in text
    assert "ASSUMPTION (Wolfsburg)" in text
    assert "03103 is not covered by SrV" in text
    assert "prior_n = 0" in text
