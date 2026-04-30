"""Search for 'mobil' / 'unterwegs' across PDF to locate Mobilitätsquote."""
from __future__ import annotations
from pathlib import Path
import pdfplumber, re

PDF = Path("eqasim-data/data/braunschweig/Ergebnistabellen_MiD2023_Version2_infas_7555_Großraum_Braunschweig.pdf")

KEYS = ["mobilitätsquote", "anteil mobil", "mobil am stichtag", "unterwegs", "nicht mobil", "Außer-Haus"]

with pdfplumber.open(str(PDF)) as pdf:
    for i, p in enumerate(pdf.pages):
        try:
            t = p.extract_text() or ""
        except Exception:
            continue
        head = t.split("\n", 1)[0].strip()[:120]
        # look for table headers with W or M (Mobilität) prefix in title
        if re.match(r"^(Tabelle [AB]\s+)?[WMP] ?\d", head):
            # capture if interesting
            if any(k in t.lower() for k in KEYS):
                print(f"PAGE {i+1}: {head}")
                # print a 12-line snippet around first key
                for kw in KEYS:
                    idx = t.lower().find(kw)
                    if idx >= 0:
                        snippet = t[max(0, idx-100):idx+400]
                        print(f"   [{kw}] ...{snippet}...")
                        break
