"""Extract the committed Kita (kindergarten) Plaetze reference from the LSN file.

One-time extraction. Reads the LSN K2300112 "Kindertageseinrichtungen, taetige
Personen und Plaetze" Excel-2003 SpreadsheetML, keeps the ZGB-8 units (kreisfreie
Staedte at the 3-digit Kreis level + Landkreis Einheits-/Samtgemeinden at the
6-digit level), and writes nds_kitas_zgb.csv. The synpp pipeline reads ONLY the CSV.

Usage:
  python scripts/extract_nds_kitas.py `
    --xml "C:/Users/bienzeisler/Downloads/Kitas NDS/K2300112_Kindertageseinrichtungen_Plaetze_2025.xml" `
    --out eqasim-data/data/braunschweig/schools/nds_kitas_zgb.csv
"""
from __future__ import annotations

import argparse
import xml.etree.ElementTree as ET
from pathlib import Path

import pandas as pd

_NS = "{urn:schemas-microsoft-com:office:spreadsheet}"
ZGB_KREISFREI = {"101", "102", "103"}
ZGB_LANDKREISE = {"151", "153", "154", "157", "158"}


def _read_rows(xml_path):
    root = ET.parse(xml_path).getroot()
    rows = []
    for row in root.iter(_NS + "Row"):
        cells = []
        for c in row.findall(_NS + "Cell"):
            d = c.find(_NS + "Data")
            cells.append((d.text or "").strip() if d is not None else "")
        rows.append(cells)
    return rows


def _to_float(s):
    s = (s or "").replace(".", "").replace(",", ".").strip()
    try:
        return float(s)
    except ValueError:
        return None


def parse_kita_rows(rows):
    """From the raw SpreadsheetML cell rows return dicts with lsn_code, name,
    einrichtungen, plaetze for the data rows (those whose first cell starts with a
    numeric code)."""
    out = []
    for cells in rows:
        if not cells or not cells[0]:
            continue
        head = cells[0].split(None, 1)
        code = head[0]
        if not code.isdigit():
            continue
        name = head[1].strip() if len(head) > 1 else ""
        einr = _to_float(cells[1]) if len(cells) > 1 else None
        plaetze = _to_float(cells[6]) if len(cells) > 6 else None
        if plaetze is None:
            continue
        out.append({"lsn_code": code, "name": name,
                    "einrichtungen": einr if einr is not None else 0.0,
                    "plaetze": plaetze})
    return out


def filter_zgb_units(parsed):
    """Keep kreisfreie Staedte (3-digit code in ZGB_KREISFREI) + Landkreis
    Einheits-/Samtgemeinden (6-digit code whose Kreis prefix is in
    ZGB_LANDKREISE). Drops the 3-digit Landkreis totals (would double-count)."""
    keep = []
    for r in parsed:
        c = r["lsn_code"]
        if len(c) == 3 and c in ZGB_KREISFREI:
            keep.append(r)
        elif len(c) == 6 and c[:3] in ZGB_LANDKREISE:
            keep.append(r)
    return keep


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xml", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()

    parsed = parse_kita_rows(_read_rows(args.xml))
    units = filter_zgb_units(parsed)
    df = pd.DataFrame(units, columns=["lsn_code", "name", "einrichtungen", "plaetze"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(args.out, index=False)
    print("[extract_nds_kitas] %d ZGB units, %d facilities, %d Plaetze -> %s"
          % (len(df), int(df["einrichtungen"].sum()), int(df["plaetze"].sum()), args.out))


if __name__ == "__main__":
    main()
