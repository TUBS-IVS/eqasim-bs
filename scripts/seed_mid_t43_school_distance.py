"""Seed the committed MiD 2023 Tabelle 43 reference CSV.

Pinned from MiD 2023 Tabelle 43
"Kita- und Schulweglaengen nach Raumtyp und Altersgruppe", mean
routed trip length in km. Re-run to regenerate; do not hand-edit the CSV.

Usage: python scripts/seed_mid_t43_school_distance.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# RegioStaR-7 code -> (km_0_6, km_7_10, km_11_13, km_14_17), routed mean km.
_PINNED = {
    71: (2, 2, 4, 4),
    72: (2, 2, 4, 4),
    73: (3, 3, 6, 7),
    74: (3, 5, 9, 9),
    75: (3, 2, 4, 5),
    76: (3, 4, 6, 7),
    77: (3, 5, 9, 10),
}
_LABELS = {
    71: "Metropole", 72: "Regiopole/Grossstadt", 73: "Mittelstadt staedt.",
    74: "kleinstaedt./doerfl.", 75: "laendl. zentrale Stadt",
    76: "laendl. Mittelstadt", 77: "laendl. kleinst./doerfl.",
}


def main():
    rows = []
    for rs7, (a, b, c, d) in sorted(_PINNED.items()):
        rows.append({"regiostar7": rs7, "label": _LABELS[rs7],
                     "km_0_6": a, "km_7_10": b, "km_11_13": c, "km_14_17": d})
    df = pd.DataFrame(rows)
    out = Path("eqasim-data/data/braunschweig/mid/mid2023_T43_school_distance_by_rs7.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"[seed_mid_t43] wrote {out} ({len(df)} RS7 rows)")


if __name__ == "__main__":
    main()
