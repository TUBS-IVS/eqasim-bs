"""Seed the local-only table of university students per site (LSN-Online K3100101).

The values are pinned by hand from the LSN-Online table K3100101 (students per
institution and site commune) in the constant ``SITE_ROWS`` below, then written to
``OUTPUT_PATH``. Re-run to regenerate; do not hand-edit the CSV.

Scope of the table: one row per (institution, site commune). ``scope`` is ``local``
when the site lies in a Kreis of the study region (``ZGB_KREISE``), else
``surrounding``. Sachsen-Anhalt institutions are not in LSN and stay curated in
``scripts/seed_nds_hochschulen.py``, which this script does not touch (the existing
``nds_hochschulen.csv`` keeps feeding the OFF path).

Usage: python scripts/seed_lsn_university_sites.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from braunschweig.calibration.srv_distance_targets import ZGB_KREISE  # noqa: E402

SOURCE_TABLE = "K3100101"
OUTPUT_PATH = Path("eqasim-data/data/braunschweig/schools/lsn_university_sites.csv")

# Filled together with SITE_ROWS (hand-read from LSN-Online); main() refuses to run
# while any of the three is empty.
SEMESTER = "WS 2024/25"
ACCESS_DATE = "2026-11-25"

# (institution, site_commune_name, site_ags8, students_persons)
#
# Layout: one tuple per institution and site commune, counts are persons (not cases),
# site_ags8 is the 8-digit AGS of the site commune as a string with leading zero.
#
# Read from LSN-Online table K3100101 ("Studierende und Studienanfaenger/innen", Haupt- und
# Nebenhoerer, Faechergruppe insgesamt, Geschlecht insgesamt), column "Studierende insgesamt",
# level Hochschulstandort. Institution names as in the table. An institution without site rows
# in the table (one site) carries its institution total; sites shown as "-" are left out.
SITE_ROWS: list[tuple[str, str, str, int]] = [
    ("TU Braunschweig", "Braunschweig, Stadt", "03101000", 15632),
    ("Hochsch. f. Bildende Künste Braunschweig", "Braunschweig, Stadt", "03101000", 902),
    ("TU Clausthal", "Clausthal-Zellerfeld, Stadt", "03153018", 2759),
    ("HS Braunschweig/Wolfenbüttel", "Salzgitter, Stadt", "03102000", 1669),
    ("HS Braunschweig/Wolfenbüttel", "Wolfsburg, Stadt", "03103000", 2363),
    ("HS Braunschweig/Wolfenbüttel", "Wolfenbüttel, Stadt", "03158037", 5092),
    ("HS Braunschweig/Wolfenbüttel", "Suderburg", "03360023", 926),
    ("Uni Hannover", "Hannover, Stadt", "03241001", 26074),
    ("HS Hannover", "Hannover, Stadt", "03241001", 8441),
    ("MHH Hannover", "Hannover, Stadt", "03241001", 4030),
    ("TiHo Hannover", "Hannover, Stadt", "03241001", 2331),
    ("Hochsch. f. Musik, Theater und Medien Hannover", "Hannover, Stadt", "03241001", 1365),
    ("Uni Göttingen", "Göttingen, Stadt", "03159016", 26824),
    ("Uni Hildesheim", "Hildesheim, Stadt", "03254021", 7784),
    ("HS Hildesheim/Holzminden/Göttingen", "Göttingen, Stadt", "03159016", 2123),
    ("HS Hildesheim/Holzminden/Göttingen", "Hildesheim, Stadt", "03254021", 2956),
    ("HS Hildesheim/Holzminden/Göttingen", "Holzminden, Stadt", "03255023", 1048),
    ("Uni Lüneburg", "Lüneburg, Stadt", "03355022", 9127),
]

_COLUMNS = ["institution", "site_commune", "site_ags8", "site_kreis_ars5", "scope",
            "students_persons", "semester"]


def build_sites_frame(rows, semester: str = "") -> pd.DataFrame:
    """Validate the hand-read rows and derive Kreis code and scope.

    Raises ValueError naming institution and AGS for a malformed ags8, a negative or
    non-integer count, or a duplicate (institution, site_ags8).
    """
    records = []
    seen = set()
    for institution, commune, ags8, students in rows:
        if not isinstance(ags8, str) or len(ags8) != 8 or not ags8.isdigit():
            raise ValueError(f"site_ags8 must be an 8-digit string (ags8), got {ags8!r} "
                             f"for institution {institution!r}")
        if isinstance(students, bool) or not isinstance(students, int):
            raise ValueError(f"students_persons must be an integer, got {students!r} "
                             f"for {institution!r} / {ags8}")
        if students < 0:
            raise ValueError(f"negative students_persons {students} for {institution!r} / {ags8}")
        key = (institution, ags8)
        if key in seen:
            raise ValueError(f"duplicate site row for institution {institution!r}, ags8 {ags8}")
        seen.add(key)
        kreis = ags8[:5]
        records.append({
            "institution": institution, "site_commune": commune, "site_ags8": ags8,
            "site_kreis_ars5": kreis, "scope": "local" if kreis in ZGB_KREISE else "surrounding",
            "students_persons": students, "semester": semester})
    return pd.DataFrame(records, columns=_COLUMNS)


def main(argv=None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    if not SITE_ROWS or not SEMESTER or not ACCESS_DATE:
        raise ValueError("SITE_ROWS, SEMESTER and ACCESS_DATE are empty: fill them from the "
                         f"LSN-Online table {SOURCE_TABLE} before running this script")
    frame = build_sites_frame(SITE_ROWS, SEMESTER)
    header = [
        f"# Source: LSN-Online {SOURCE_TABLE} (students per institution and site commune), "
        f"{SEMESTER}, accessed {ACCESS_DATE}",
        "# Script: scripts/seed_lsn_university_sites.py (values pinned in SITE_ROWS)",
        "# Licence: dl-de/by-2-0 (Landesamt fuer Statistik Niedersachsen)",
        "# Sachsen-Anhalt institutions are not in LSN and stay curated in "
        "scripts/seed_nds_hochschulen.py",
        f"# Rows: {len(frame)}; local {int((frame.scope == 'local').sum())}, "
        f"surrounding {int((frame.scope == 'surrounding').sum())}",
    ]
    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_PATH, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(header) + "\n")
        frame.to_csv(handle, index=False, lineterminator="\n")
    print(f"[seed_lsn_university_sites] wrote {OUTPUT_PATH} ({len(frame)} rows)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
