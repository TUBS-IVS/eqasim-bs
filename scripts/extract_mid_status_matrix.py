"""
Extract the MiD 2023 economic-status matrix (handbook "Abbildung 2", p.16)
into a committed long-format CSV.

Source: MiD2023_HandbuchZurDatennutzung.pdf (local-only copy expected at
eqasim-data/data/braunschweig/mid/raw/). The matrix is a vector graphic;
each cell is a filled PDF rectangle, so the class per cell is recovered
EXACTLY from the rectangle fill colours (pdfplumber page.rects,
non_stroking_color) instead of reading the figure visually.

Construct (handbook p.16): weighted household size = 1.0 for the first
member aged 14+, +0.5 per further member 14+, +0.3 per member <14; the
matrix maps (weighted size row, net-income class column) -> one of five
statuses (very_low..very_high). Row labels are the 30 achievable weighted
sizes; column bounds are the 15 MiD monthly net-income classes in EUR.

Writes: eqasim-data/data/braunschweig/mid/mid2023_economic_status_matrix.csv

Usage:
    python scripts/extract_mid_status_matrix.py [--pdf <path>] [--out <path>]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pdfplumber

REPO = Path(__file__).resolve().parents[1]
PDF_DEFAULT = (REPO / "eqasim-data" / "data" / "braunschweig" / "mid" / "raw"
               / "MiD2023_HandbuchZurDatennutzung.pdf")
OUT_DEFAULT = (REPO / "eqasim-data" / "data" / "braunschweig" / "mid"
               / "mid2023_economic_status_matrix.csv")

# Page index (0-based) of Abbildung 2 in the handbook.
MATRIX_PAGE_INDEX = 15

# PDF fill colour -> status class. Black cells carry an EMPTY colour tuple.
COLOR_TO_STATUS = {
    (0.0, 0.42, 0.431): "very_low",
    (0.031, 0.651, 0.29): "low",
    (): "medium",
    (0.529, 0.804, 0.824): "high",
    (0.6, 0.0, 0.0): "very_high",
}

# The 30 achievable weighted-size row labels, TOP row of the figure first.
ROW_LABELS_TOP_FIRST = [4.5, 4.3, 4.1, 4.0, 3.9, 3.8, 3.7, 3.6, 3.5, 3.4, 3.3,
                        3.2, 3.1, 3.0, 2.9, 2.8, 2.7, 2.6, 2.5, 2.4, 2.3, 2.2,
                        2.1, 2.0, 1.9, 1.8, 1.6, 1.5, 1.3, 1.0]

# The 15 income class bounds (EUR/month, net). -1 = open top class.
INCOME_BOUNDS = [(0, 500), (500, 900), (900, 1500), (1500, 2000), (2000, 2600),
                 (2600, 3000), (3000, 3600), (3600, 4000), (4000, 4600),
                 (4600, 5000), (5000, 5600), (5600, 6000), (6000, 6600),
                 (6600, 7000), (7000, -1)]

# Plot area x-range on the page; excludes the y-axis label column (x < 60)
# and the legend swatches (x > 460).
PLOT_X_MIN, PLOT_X_MAX = 60.0, 460.0

HEADER = """\
# Source: MiD 2023 "Handbuch zur Datennutzung", Abbildung 2 (p.16):
#   "Bestimmung des oekonomischen Status ueber Haushaltsnettoeinkommen und
#   gewichtete Haushaltsgroesse". Extracted from the PDF VECTOR FILLS by
#   scripts/extract_mid_status_matrix.py (cell rectangle colour -> class),
#   not transcribed visually.
# wsize_row: weighted household size (1.0 first member 14+, +0.5 per further
#   member 14+, +0.3 per member <14); the 30 achievable values; sizes above
#   4.5 use the 4.5 row.
# income_col 0..14 with [income_lo_eur, income_hi_eur) monthly net household
#   income; income_hi_eur = -1 means open top class (>= 7000).
"""


def extract_matrix(pdf_path: Path) -> list:
    with pdfplumber.open(pdf_path) as pdf:
        page = pdf.pages[MATRIX_PAGE_INDEX]
        cells = []
        for r in page.rects:
            if not r.get("fill"):
                continue
            color = tuple(r["non_stroking_color"] or ())
            if color not in COLOR_TO_STATUS or color == (1.0, 1.0, 1.0):
                continue
            if not (PLOT_X_MIN < r["x0"] < PLOT_X_MAX):
                continue
            cells.append((round(r["top"], 1), round(r["x0"], 1),
                          COLOR_TO_STATUS[color]))
    rows = sorted(set(t for t, _, _ in cells))
    cols = sorted(set(x for _, x, _ in cells))
    if len(rows) != len(ROW_LABELS_TOP_FIRST) or len(cols) != len(INCOME_BOUNDS):
        raise ValueError(
            f"matrix extraction failed: found {len(rows)} rows x {len(cols)} "
            f"cols, expected 30 x 15. The PDF layout changed or the page "
            f"index {MATRIX_PAGE_INDEX} is wrong for {pdf_path}.")
    if len(cells) != 450:
        raise ValueError(f"expected 450 matrix cells, found {len(cells)}.")
    records = []
    for top, x0, status in cells:
        wsize = ROW_LABELS_TOP_FIRST[rows.index(top)]
        col = cols.index(x0)
        lo, hi = INCOME_BOUNDS[col]
        records.append((wsize, col, lo, hi, status))
    records.sort()
    return records


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, default=PDF_DEFAULT)
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    args = parser.parse_args(argv)
    if not args.pdf.exists():
        raise FileNotFoundError(
            f"Handbook PDF not found: {args.pdf}. Copy "
            "MiD2023_HandbuchZurDatennutzung.pdf there (local-only).")
    records = extract_matrix(args.pdf)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        f.write(HEADER)
        f.write("wsize_row,income_col,income_lo_eur,income_hi_eur,status\n")
        for wsize, col, lo, hi, status in records:
            f.write(f"{wsize},{col},{lo},{hi},{status}\n")
    print(f"wrote {args.out} ({len(records)} cells)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
