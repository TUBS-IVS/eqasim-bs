"""
Seed CSV for the curated mapping of higher-education institutions in
ZGB-8 to their host commune (8-digit AGS).

This script is the **committed source of truth** for
``eqasim-data/data/braunschweig/education/hochschul_orte_zgb.csv``,
following the same seed-script-only convention as
``scripts/seed_mid_constraint_tables.py``: the CSV itself lives under
``eqasim-data/`` (gitignored) and is regenerated from this script on
demand.

Source: Hochschulkompass (https://www.hochschulkompass.de/) — institutional
master records, ``--reference-year`` defaults to ``latest``. License: HRK
terms allow reuse for non-commercial research with attribution.

AGS commune codes follow the German Gemeindeschlüssel (Stand 2024).
Multi-campus institutions (e.g. Ostfalia HaW) are listed once per
campus row so per-Studienort capacity from DESTATIS table 21311-0007
can be joined directly on (institution, commune_id).

Usage::

    python scripts/seed_hochschul_orte_zgb.py
    python scripts/seed_hochschul_orte_zgb.py \
        --out eqasim-data/data/braunschweig/education/hochschul_orte_zgb.csv
"""
from __future__ import annotations

import argparse
import csv
from pathlib import Path


HEADER_COMMENT = (
    "# Curated mapping of higher-education institutions in ZGB-8 to their\n"
    "# host commune (8-digit AGS, AGS-12 truncated by trailing 4 zeros).\n"
    "# Source: Hochschulkompass (https://www.hochschulkompass.de/).\n"
    "# License: HRK terms allow reuse for non-commercial research with\n"
    "# attribution. Regenerate via scripts/seed_hochschul_orte_zgb.py.\n"
)


# (institution_id, institution, campus, commune_id, kreis_id, notes)
ROWS = [
    (
        "tu_braunschweig",
        "Technische Universität Braunschweig",
        "",
        "03101000", "03101",
        "Largest ZGB Hochschule; multiple inner-city sites count as Braunschweig",
    ),
    (
        "hbk_braunschweig",
        "Hochschule für Bildende Künste Braunschweig",
        "",
        "03101000", "03101",
        "",
    ),
    (
        "ostfalia_wolfenbuettel",
        "Ostfalia Hochschule für angewandte Wissenschaften",
        "Wolfenbüttel",
        "03158021", "03158",
        "Stadt Wolfenbüttel (Hauptcampus)",
    ),
    (
        "ostfalia_wolfsburg",
        "Ostfalia Hochschule für angewandte Wissenschaften",
        "Wolfsburg",
        "03103000", "03103",
        "",
    ),
    (
        "ostfalia_salzgitter",
        "Ostfalia Hochschule für angewandte Wissenschaften",
        "Salzgitter",
        "03102000", "03102",
        "",
    ),
    (
        "welfen_akademie",
        "Welfen-Akademie e.V. Braunschweig",
        "",
        "03101000", "03101",
        "Private Berufsakademie; small enrolment",
    ),
]


COLUMNS = ["institution_id", "institution", "campus",
           "commune_id", "kreis_id", "notes"]


def write_csv(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as fh:
        fh.write(HEADER_COMMENT)
        writer = csv.writer(fh)
        writer.writerow(COLUMNS)
        writer.writerows(ROWS)
    print(f"[seed-hochschul-orte] wrote {len(ROWS)} rows to {path}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out",
        type=Path,
        default=Path("eqasim-data/data/braunschweig/education/hochschul_orte_zgb.csv"),
    )
    args = parser.parse_args()
    write_csv(args.out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
