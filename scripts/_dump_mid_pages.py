"""Dump specific MiD 2023 GR-BS PDF pages."""
from __future__ import annotations
import sys
from pathlib import Path
import pdfplumber

PDF = Path("eqasim-data/data/braunschweig/Ergebnistabellen_MiD2023_Version2_infas_7555_Großraum_Braunschweig.pdf")

PAGES = [int(x) for x in sys.argv[1:]] or [231, 232, 233]

with pdfplumber.open(str(PDF)) as pdf:
    for p in PAGES:
        print(f"\n========== PAGE {p} ==========")
        t = pdf.pages[p-1].extract_text() or ""
        print(t)
