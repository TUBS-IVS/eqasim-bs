"""
Extract the Raumtyp (RegioStaR-7) block of MiD 2023 Tabelle A P13 into a CSV.

Source: MiD 2023 Grossraum Braunschweig (infas 7555), Tabelle A P13 "Entfernung
Arbeitsstaette", page 77, "Raumtyp" block.

Writes:
    eqasim-data/data/braunschweig/mid/mid2023_P13_commute_distance_by_rs7.csv

Usage:
    python scripts/extract_mid_p13_rs7.py [--pdf <path>] [--out <path>]
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import pandas as pd
import pdfplumber

REPO = Path(__file__).resolve().parents[1]
PDF_DEFAULT = (
    REPO
    / "eqasim-data"
    / "data"
    / "braunschweig"
    / "Ergebnistabellen_MiD2023_Version2_infas_7555_Großraum_Braunschweig.pdf"
)
OUT_DEFAULT = (
    REPO / "eqasim-data" / "data" / "braunschweig" / "mid"
    / "mid2023_P13_commute_distance_by_rs7.csv"
)

# P13 page in the PDF (1-indexed).
P13_PAGE = 77

# RegioStaR-7 crosswalk.  The PDF uses latin-1-decoded labels with umlaut
# replacement characters (e.g. "?" for ä/ö/ü/ß).  We match by fixed prefix
# patterns that survive the encoding loss and are unique within the Raumtyp block.
# The labels here are the canonical German labels used in regiostar.py.
RS7_CROSSWALK = [
    (72, "Stadtregion - Regiopole und Grossstadt",
     "Stadtregion - Regiopole"),
    (73, "Stadtregion - Mittelstaedte, staedtischer Raum",
     "Stadtregion - Mittelst"),
    (74, "Stadtregion - kleinstaedtischer, doerflicher Raum",
     "Stadtregion - kleinst"),
    (75, "laendliche Region - zentrale Stadt",
     "ndliche Region - zentrale"),
    (76, "laendliche Region - Mittelstaedte, staedtischer Raum",
     "ndliche Region - Mittelst"),
    (77, "laendliche Region - kleinstaedtischer, doerflicher Raum",
     "ndliche Region - kleinst"),
]

# P13 column names (matching mid2023_P13.csv).
P13_COLUMNS = [
    "d_0", "d_0_5", "d_5_10", "d_10_20",
    "d_20_30", "d_30_50", "d_50_100", "d_100p",
    "keine_feste_arbeit", "keine_angabe",
]
NUM_COLS = len(P13_COLUMNS)  # 10 percentage columns

# Validation oracle (MiD 2023 Tabelle A P13, Raumtyp block).
# Values: (n_weighted, n_unweighted, [d_0..d_100p], keine_feste_arbeit, keine_angabe, mittel)
_ORACLE = {
    72: (277,  704, [1, 22, 23, 16, 10, 18,  3,  1],  5, 0, 17.9),
    73: (129,  318, [1, 21, 17, 17, 12, 19,  6,  0],  8, 0, 18.4),
    74: (287,  370, [1,  7, 12, 29, 28, 15,  3,  0],  3, 0, 20.2),
    75: (29,    66, [0, 35, 15,  2,  5,  5,  8,  4], 21, 4, 19.7),
    76: (55,    79, [2, 32, 11, 20,  5,  9, 19,  0],  3, 0, 20.9),
    77: (60,    46, [4, 25,  9,  7, 12, 16,  0, 21],  7, 0, 40.5),
}

_NUM_PAT = re.compile(r"(?:\d{1,3}(?:\.\d{3})+|\d+(?:,\d+)?|\*)")


def _to_number(tok: str):
    """Convert a token to float; ``*`` (suppressed cell) -> None."""
    if tok == "*":
        return None
    return float(tok.replace(".", "").replace(",", "."))


def _extract_raumtyp_block(lines: list[str]) -> list[str]:
    """Return the lines in the FIRST 'Raumtyp' block on the page."""
    section_headers = {
        "Teilgebiete",
        "regionalstatistischer Gemeindetyp",
        "Raumtyp",
        "Kreistyp",
        "Geschlecht",
        "Alter",
    }
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "Raumtyp":
            start = i + 1
            break
    if start is None:
        return []
    out = []
    for line in lines[start:]:
        if line.strip() in section_headers:
            break
        if line.strip():
            out.append(line)
    return out


def _parse_raumtyp_rows(block_lines: list[str]) -> list[dict]:
    """Parse the 6 Raumtyp rows from the block lines."""
    rows = []
    for line in block_lines:
        stripped = line.strip()
        # Match each RS7 entry by the unique PDF prefix pattern.
        matched_rs7 = None
        matched_label = None
        for rs7, label, pdf_fragment in RS7_CROSSWALK:
            if pdf_fragment in stripped:
                matched_rs7 = rs7
                matched_label = label
                break
        if matched_rs7 is None:
            continue

        # Find the numeric tokens after the label text.
        # Strategy: split on whitespace, find the first run of numeric tokens.
        tokens = stripped.split()
        first_num = None
        for i, tok in enumerate(tokens):
            if _NUM_PAT.fullmatch(tok):
                first_num = i
                break
        if first_num is None or first_num == 0:
            continue
        values = tokens[first_num:]
        # We need at least 2 (n_w, n_u) + NUM_COLS + 1 (mittel) = 13 tokens.
        if len(values) < 2 + NUM_COLS + 1:
            continue

        n_w = int(_to_number(values[0]))
        n_u = int(_to_number(values[1]))
        pct = [int(_to_number(v)) for v in values[2:2 + NUM_COLS]]
        mittel = _to_number(values[2 + NUM_COLS])

        row = {
            "regiostar7": matched_rs7,
            "label": matched_label,
            "n_weighted": n_w,
            "n_unweighted": n_u,
        }
        for col, val in zip(P13_COLUMNS, pct):
            row[col] = val
        row["mittel"] = mittel
        rows.append(row)
    return rows


def _assert_oracle(rows: list[dict]) -> None:
    """Validate parsed rows against the oracle; raise AssertionError on mismatch."""
    parsed_by_rs7 = {r["regiostar7"]: r for r in rows}
    for rs7, (exp_nw, exp_nu, exp_d, exp_kfa, exp_ka, exp_m) in _ORACLE.items():
        assert rs7 in parsed_by_rs7, f"RS7 {rs7} not found in parsed rows"
        r = parsed_by_rs7[rs7]
        assert r["n_weighted"] == exp_nw, (
            f"RS7 {rs7}: n_weighted {r['n_weighted']} != oracle {exp_nw}"
        )
        assert r["n_unweighted"] == exp_nu, (
            f"RS7 {rs7}: n_unweighted {r['n_unweighted']} != oracle {exp_nu}"
        )
        d_cols = ["d_0", "d_0_5", "d_5_10", "d_10_20",
                  "d_20_30", "d_30_50", "d_50_100", "d_100p"]
        for col, exp_val in zip(d_cols, exp_d):
            assert r[col] == exp_val, (
                f"RS7 {rs7}: {col} {r[col]} != oracle {exp_val}"
            )
        assert r["keine_feste_arbeit"] == exp_kfa, (
            f"RS7 {rs7}: keine_feste_arbeit {r['keine_feste_arbeit']} != oracle {exp_kfa}"
        )
        assert r["keine_angabe"] == exp_ka, (
            f"RS7 {rs7}: keine_angabe {r['keine_angabe']} != oracle {exp_ka}"
        )
        assert abs(r["mittel"] - exp_m) < 0.05, (
            f"RS7 {rs7}: mittel {r['mittel']} != oracle {exp_m}"
        )


def extract(pdf_path: Path) -> list[dict]:
    """Parse the Raumtyp block from P13 page 77 and return the 6 row dicts."""
    with pdfplumber.open(str(pdf_path)) as pdf:
        text = pdf.pages[P13_PAGE - 1].extract_text() or ""
    lines = text.split("\n")
    block = _extract_raumtyp_block(lines)
    if not block:
        raise RuntimeError(
            f"Could not find 'Raumtyp' section header on page {P13_PAGE} of {pdf_path}"
        )
    rows = _parse_raumtyp_rows(block)
    if len(rows) != 6:
        raise RuntimeError(
            f"Expected 6 Raumtyp rows, got {len(rows)}. "
            f"Parsed rows: {[r.get('regiostar7') for r in rows]}"
        )
    return rows


def write_csv(rows: list[dict], out_path: Path) -> None:
    """Write the parsed rows to CSV with a provenance comment header."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    provenance = (
        "# Source: MiD 2023 Grossraum Braunschweig (infas 7555), "
        "Tabelle A P13, page 77, \"Raumtyp\" block\n"
    )
    col_order = (
        ["regiostar7", "label", "n_weighted", "n_unweighted"]
        + P13_COLUMNS
        + ["mittel"]
    )
    df = pd.DataFrame(rows, columns=col_order)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(provenance)
        df.to_csv(fh, index=False)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract MiD P13 Raumtyp block into a CSV."
    )
    parser.add_argument("--pdf", type=Path, default=PDF_DEFAULT,
                        help="Path to the MiD 2023 PDF (default: repo convention).")
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT,
                        help="Output CSV path.")
    args = parser.parse_args()

    if not args.pdf.exists():
        sys.stderr.write(f"[p13-rs7] PDF not found: {args.pdf}\n")
        return 2

    rows = extract(args.pdf)

    # Assert oracle BEFORE writing.
    _assert_oracle(rows)

    write_csv(rows, args.out)

    print(f"[p13-rs7] Wrote {len(rows)} rows to {args.out}")
    print(f"{'rs7':>4}  {'n_w':>5}  {'n_u':>5}  "
          f"{'d[0..100p]':>28}  {'kfa':>4}  {'ka':>3}  {'mittel':>7}")
    for r in rows:
        d_vals = [r[c] for c in ["d_0", "d_0_5", "d_5_10", "d_10_20",
                                  "d_20_30", "d_30_50", "d_50_100", "d_100p"]]
        print(
            f"{r['regiostar7']:>4}  {r['n_weighted']:>5}  {r['n_unweighted']:>5}  "
            f"{str(d_vals):>28}  {r['keine_feste_arbeit']:>4}  "
            f"{r['keine_angabe']:>3}  {r['mittel']:>7.1f}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
