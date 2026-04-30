"""List all 'Tabelle A ... ' titles to find table-of-contents."""
from __future__ import annotations
from pathlib import Path
import pdfplumber, re

PDF = Path("eqasim-data/data/braunschweig/Ergebnistabellen_MiD2023_Version2_infas_7555_Großraum_Braunschweig.pdf")

with pdfplumber.open(str(PDF)) as pdf:
    for i, p in enumerate(pdf.pages):
        try:
            t = p.extract_text() or ""
        except Exception:
            continue
        for line in t.split("\n")[:5]:
            if re.match(r"^Tabelle [AB] ", line):
                print(f"p{i+1:3d}: {line.strip()[:130]}")
                break
