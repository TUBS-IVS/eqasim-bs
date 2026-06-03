"""Seed the committed Destatis Mikrozensus 2024 school-distance reference CSV.

Pinned from Destatis, "Schueler/innen und Studierende nach Entfernung ... fuer den
Hinweg zur Schule/Hochschule", Endergebnis Mikrozensus 2024, Anteile in %
(https://www.destatis.de/DE/Themen/Arbeit/Arbeitsmarkt/Erwerbstaetigkeit/Tabellen/pendler2.html).
Re-run to regenerate; do not hand-edit the CSV.

Usage: python scripts/seed_mikrozensus_school_distance.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# school_type -> (lt5, b5_10, b10_25, b25_50, ge50), shares in %.
_PINNED = {
    "allgemeinbildend": (64.4, 21.9, 11.9, 1.5, 0.2),
    "berufsbildend": (18.8, 20.9, 33.0, 17.9, 9.3),
    "hochschule": (33.9, 19.9, 17.8, 14.3, 13.7),
}


def main():
    rows = [{"school_type": st, "lt5": a, "b5_10": b, "b10_25": c,
             "b25_50": d, "ge50": e} for st, (a, b, c, d, e) in _PINNED.items()]
    df = pd.DataFrame(rows)
    out = Path("eqasim-data/data/braunschweig/mikrozensus/mikrozensus2024_school_distance_by_type.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"[seed_mikrozensus_school_distance] wrote {out} ({len(df)} rows)")


if __name__ == "__main__":
    main()
