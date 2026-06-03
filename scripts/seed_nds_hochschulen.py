"""Seed the committed Hochschule enrollment reference for the education gravity model.

NDS rows pinned from the LSN "Studierende ... SS 2025 nach Hochschulart/Hochschule"
(Statistik Niedersachsen, statistik.niedersachsen.de). The cross-border Magdeburg
rows (Sachsen-Anhalt) are NOT in the LSN file; their enrollment is curated from
Destatis / Statistisches Landesamt Sachsen-Anhalt (Hochschulstatistik 2024) so
that eastern-ZGB students (Helmstedt/Wolfsburg, ~40-60 km to Magdeburg) get a
realistic commute tail. Coordinates are approximate main-campus points (WGS84);
exact intra-campus position is irrelevant at 40-100 km. Re-run to regenerate;
do not hand-edit the CSV.

Usage: python scripts/seed_nds_hochschulen.py
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

# institution, scope, ars5 (local only), enrollment, lon, lat (surrounding only)
_LOCAL = [
    ("TU Braunschweig", "03101", 14776),
    ("HBK Braunschweig", "03101", 851),
    ("TU Clausthal", "03153", 2811),
    ("Ostfalia", "03158", 9373),   # split across ZGB Ostfalia communes by the
                                   # facilities builder (03158/03102/03103)
]
# institution, lon, lat, enrollment
_SURROUNDING = [
    ("Universitaet Hannover", 9.7177, 52.3822, 24057),
    ("Hochschule Hannover", 9.7320, 52.3460, 7966),
    ("MHH Hannover", 9.8064, 52.3836, 3654),
    ("TiHo Hannover", 9.7680, 52.3580, 2074),
    ("HMTM Hannover", 9.7410, 52.3780, 1270),
    ("Universitaet Goettingen", 9.9358, 51.5413, 25513),
    ("Universitaet Hildesheim", 9.9530, 52.1440, 7292),
    ("HAWK Hildesheim", 9.9520, 52.1550, 5717),
    ("OVGU Magdeburg", 11.6455, 52.1392, 13800),
    ("Hochschule Magdeburg-Stendal", 11.6760, 52.1395, 5400),
    # Hochschule Harz (Wernigerode, Sachsen-Anhalt) ~60 km from Goslar/Salzgitter;
    # curated from Destatis / Stat. Landesamt Sachsen-Anhalt 2024.
    ("Hochschule Harz", 10.7853, 51.8344, 2800),
    # Leuphana Universitaet Lueneburg ~95 km north (Gifhorn tail); enrollment from
    # the LSN SS2025 file (Universitaet Lueneburg 8461).
    ("Leuphana Lueneburg", 10.4023, 53.2285, 8461),
]


def main():
    rows = []
    for inst, ars5, enr in _LOCAL:
        rows.append({"institution": inst, "scope": "local", "ars5": ars5,
                     "enrollment": enr, "lon": "", "lat": ""})
    for inst, lon, lat, enr in _SURROUNDING:
        rows.append({"institution": inst, "scope": "surrounding", "ars5": "",
                     "enrollment": enr, "lon": lon, "lat": lat})
    df = pd.DataFrame(rows, columns=["institution", "scope", "ars5",
                                     "enrollment", "lon", "lat"])
    out = Path("eqasim-data/data/braunschweig/schools/nds_hochschulen.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(out, index=False)
    print(f"[seed_nds_hochschulen] wrote {out} ({len(df)} rows: "
          f"{(df.scope=='local').sum()} local, {(df.scope=='surrounding').sum()} surrounding)")


if __name__ == "__main__":
    main()
