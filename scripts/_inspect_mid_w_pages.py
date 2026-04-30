"""Scan the MiD 2023 Großraum BS PDF for W-tables and Mobilitätsquote."""
from __future__ import annotations
import sys
from pathlib import Path
import pdfplumber

PDF = Path("eqasim-data/data/braunschweig/Ergebnistabellen_MiD2023_Version2_infas_7555_Großraum_Braunschweig.pdf")

KEYWORDS = [
    "Mobilitätsquote",
    "mobil an einem",
    "mobil am Stichtag",
    "mobil im Stichtag",
    "Wegezweck",
    "Hauptzweck",
    "Aktivität",
]

def main():
    with pdfplumber.open(str(PDF)) as pdf:
        n = len(pdf.pages)
        print(f"total pages: {n}")
        for i in range(n):
            try:
                t = pdf.pages[i].extract_text() or ""
            except Exception as e:
                print(f"  page {i+1}: error {e}")
                continue
            head = t.split("\n", 1)[0].strip()[:120]
            # Detect W-table headings
            if head.startswith("W ") or head.startswith("Tabelle W"):
                print(f"PAGE {i+1:3d} HEAD: {head}")
            # Detect keywords
            for kw in KEYWORDS:
                if kw.lower() in t.lower():
                    print(f"  page {i+1} contains '{kw}'")
                    break

if __name__ == "__main__":
    sys.exit(main())
