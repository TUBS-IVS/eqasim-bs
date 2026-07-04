import sys, os
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from scripts.extract_mid_h4_status_by_kreis import parse_h4_rows  # noqa: E402

_H4_BLOCK = [
    "Gesamt 3.013 5.924 9 12 31 36 12",
    "Teilgebiete",
    "Braunschweig 648 1.105 10 8 30 36 16",
    "Wolfsburg 155 508 11 13 34 34 8",
    "Salzgitter 167 792 5 10 29 42 13",
    "Landkreis Gifhorn 530 706 5 13 26 43 14",
    "Landkreis Peine 316 669 5 13 36 35 11",
    "Landkreis Helmstedt 358 644 8 11 36 35 10",
    "Landkreis Wolfenbüttel 310 510 10 12 31 36 11",
    "Landkreis Goslar 528 990 17 15 30 29 9",
    "regionalstatistischer Gemeindetyp",
    "Regiopole 970 2.405 9 9 30 37 14",
]


def test_parse_h4_rows_extracts_zgb_and_eight_kreise():
    rows = parse_h4_rows(_H4_BLOCK)
    by_ars5 = {r["ars5"]: r for r in rows}
    assert set(by_ars5) == {"03ZGB", "03101", "03102", "03103",
                            "03151", "03153", "03154", "03157", "03158"}
    sz = by_ars5["03102"]
    assert (sz["kreis"], sz["n_weighted"], sz["n_unweighted"]) == ("Salzgitter", 167, 792)
    assert [sz["very_low"], sz["low"], sz["medium"], sz["high"], sz["very_high"]] == [5, 10, 29, 42, 13]
    gifhorn = by_ars5["03151"]
    assert gifhorn["kreis"] == "Landkreis Gifhorn"
    assert [gifhorn[k] for k in ("very_low", "low", "medium", "high", "very_high")] == [5, 13, 26, 43, 14]
    for r in rows:
        # The published table rounds each status share independently to whole
        # percentage points, so row sums may be 99-101 (e.g. Salzgitter = 99,
        # Landkreis Gifhorn = 101 in this real fixture) even though the underlying
        # weighted shares sum to 100. Allow +/-1 point of rounding slack instead
        # of asserting an exact 100, which would fail on genuine source data.
        total = sum(r[k] for k in ("very_low", "low", "medium", "high", "very_high"))
        assert abs(total - 100) <= 1, f"{r['kreis']}: status shares sum to {total}, expected ~100"
