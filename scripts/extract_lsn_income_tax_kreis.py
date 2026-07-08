"""
Extract a per-Kreis income-tax aggregate for the ZGB region from the LSN
A9170102 table (Lohn- und Einkommensteuer in Niedersachsen, einheitliche
Schichtung der Steuerpflichtigen, tax year 2022).

Source: LSN-Online Tabelle A9170102, downloaded as SpreadsheetML XML and stored
LOCALLY (never committed) at
    eqasim-data/data/braunschweig/lsn/raw/lsn_A9170102_income_tax_brackets_by_kreis_2022.xml

Writes a SMALL committed aggregate (one row per ZGB Kreis + Niedersachsen):
    eqasim-data/data/braunschweig/lsn/lsn2022_income_tax_by_kreis.csv

Purpose: full-count REGISTER arbiter for survey disagreements on the per-Kreis
income/economic-status ordering (MiD H4 vs SrV 2023; see the 2026-07-08
control-sourcing spec). CAVEAT (do not drop): "Gesamtbetrag der Einkuenfte" of
taxpayers is NOT net household income - only the ordering / relative level of
Kreise is a valid signal, never the absolute EUR values.

Usage:
    python scripts/extract_lsn_income_tax_kreis.py [--xml <path>] [--out <path>]
"""

from __future__ import annotations

import argparse
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
XML_DEFAULT = (
    REPO / "eqasim-data" / "data" / "braunschweig" / "lsn" / "raw"
    / "lsn_A9170102_income_tax_brackets_by_kreis_2022.xml"
)
OUT_DEFAULT = (
    REPO / "eqasim-data" / "data" / "braunschweig" / "lsn"
    / "lsn2022_income_tax_by_kreis.csv"
)

SS_NS = "{urn:schemas-microsoft-com:office:spreadsheet}"

# LSN region tokens (first token of the region header row) -> (ars5, name).
# "0" is the Niedersachsen total; the ZGB Kreis tokens are the 3-digit Kreis
# codes without the "03" Bundesland prefix.
REGIONS = {
    "0": ("03NDS", "Niedersachsen"),
    "101": ("03101", "Braunschweig"),
    "102": ("03102", "Salzgitter"),
    "103": ("03103", "Wolfsburg"),
    "151": ("03151", "Gifhorn"),
    "153": ("03153", "Goslar"),
    "154": ("03154", "Helmstedt"),
    "157": ("03157", "Peine"),
    "158": ("03158", "Wolfenbuettel"),
}

# Lower bounds (EUR "Gesamtbetrag der Einkuenfte") of the brackets counted as
# high income for the share_ge_50k_eur column.
HIGH_INCOME_LOWER_BOUNDS = ("50000", "125000", "250000")

HEADER = """\
# Source: LSN-Online Tabelle A9170102 "Lohn- und Einkommensteuer in
#   Niedersachsen, einheitliche Schichtung der Steuerpflichtigen", tax year
#   2022, Gebietsstand 01.11.2021. Extracted from the SpreadsheetML XML in
#   eqasim-data/data/braunschweig/lsn/raw/ (local-only) by
#   scripts/extract_lsn_income_tax_kreis.py.
# Universe: Lohn- und Einkommensteuerpflichtige (taxpayer units), first
#   "Insgesamt" block per region (later blocks are subgroup breakdowns).
# mean_gde_eur = Gesamtbetrag der Einkuenfte (1000 EUR column x 1000) / taxpayers.
# share_ge_50k = taxpayers with Gesamtbetrag der Einkuenfte >= 50000 EUR / all.
# CAVEAT: taxable income of taxpayer units != net household income. Use ONLY
#   as an ordering / relative-level arbiter across Kreise (register-grade),
#   never as an absolute household-income reference.
"""


def parse_rows(xml_path: Path) -> list:
    """All spreadsheet rows as lists of cell texts (None for empty cells)."""
    tree = ET.parse(xml_path)
    rows = []
    for row in tree.iter(f"{SS_NS}Row"):
        vals = []
        for cell in row.iter(f"{SS_NS}Cell"):
            data = cell.find(f"{SS_NS}Data")
            vals.append(None if data is None else data.text)
        rows.append(vals)
    return rows


def extract(xml_path: Path) -> list:
    """Per-region aggregates from the FIRST 'Insgesamt' block of each region.

    The table repeats an 'Insgesamt' + bracket block per subgroup within one
    region; only the first block is the all-taxpayers universe, so a region is
    closed as soon as its second 'Insgesamt' row appears.
    """
    rows = parse_rows(xml_path)
    results = {}
    closed = set()
    current = None
    for r in rows:
        if len(r) == 1 and r[0] and r[0].strip():
            token = r[0].split()[0].strip()
            if token in REGIONS:
                current = REGIONS[token]
            continue
        if current is None or not r or not r[0]:
            continue
        label = r[0].strip()
        if label == "Insgesamt":
            if current in results:
                closed.add(current)
                continue
            results[current] = {
                "n_taxpayers": float(r[1]),
                # column 3 = Gesamtbetrag der Einkuenfte in 1000 EUR
                "gde_total_keur": float(r[3]),
                "n_ge_50k": 0.0,
            }
            continue
        if current in results and current not in closed:
            lower = label.replace(" ", "").split("-")[0]
            if lower in HIGH_INCOME_LOWER_BOUNDS:
                results[current]["n_ge_50k"] += float(r[1])

    missing = [name for key, name in REGIONS.values() if (key, name) not in
               {(k[0], k[1]) for k in results}]
    if missing:
        raise ValueError(
            f"LSN A9170102 extraction: regions missing from {xml_path}: {missing}. "
            "The table layout changed or the wrong table was downloaded."
        )

    out = []
    for (ars5, name), v in sorted(results.items()):
        if v["n_taxpayers"] <= 0:
            raise ValueError(f"LSN A9170102: non-positive taxpayer count for {name}.")
        out.append({
            "kreis": name,
            "ars5": ars5,
            "n_taxpayers": int(v["n_taxpayers"]),
            "mean_gde_eur": round(v["gde_total_keur"] * 1000.0 / v["n_taxpayers"], 1),
            "share_ge_50k": round(v["n_ge_50k"] / v["n_taxpayers"], 4),
        })
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", type=Path, default=XML_DEFAULT)
    parser.add_argument("--out", type=Path, default=OUT_DEFAULT)
    args = parser.parse_args(argv)

    if not args.xml.exists():
        raise FileNotFoundError(
            f"LSN raw XML not found: {args.xml}. Download LSN-Online Tabelle "
            "A9170102 (year 2022, all Kreise) as XML and place it there (local-only)."
        )

    records = extract(args.xml)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    cols = ["kreis", "ars5", "n_taxpayers", "mean_gde_eur", "share_ge_50k"]
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        f.write(HEADER)
        f.write(",".join(cols) + "\n")
        for rec in records:
            f.write(",".join(str(rec[c]) for c in cols) + "\n")
    print(f"wrote {args.out} ({len(records)} regions)")
    for rec in records:
        print(f"  {rec['kreis']:14s} mean_gde_eur={rec['mean_gde_eur']:>9.1f} "
              f"share_ge_50k={rec['share_ge_50k']:.4f} n={rec['n_taxpayers']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
