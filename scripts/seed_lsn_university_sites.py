"""Seed the local-only table of university students per site (LSN-Online K3100101).

The values are pinned by hand from the LSN-Online table K3100101 (students per
institution and site commune) in the constant ``SITE_ROWS`` below, then written to
``OUTPUT_PATH`` (``SEMESTER``) and ``CENSUS_YEAR_OUTPUT_PATH`` (``CENSUS_YEAR_SEMESTER``). Re-run to regenerate; do not hand-edit the CSV.

Scope of the table: one row per (institution, site commune). ``scope`` is ``local``
when the site lies in a Kreis of the study region (``ZGB_KREISE``), else
``surrounding``. Sachsen-Anhalt institutions are not in LSN and stay curated in
``scripts/seed_nds_hochschulen.py``, which this script does not touch (the existing
``nds_hochschulen.csv`` keeps feeding the OFF path); their students are pinned in
``OUTSIDE_LSN_STUDENTS``.

Usage: python scripts/seed_lsn_university_sites.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# Anchored at the repository root, so the script writes to the same place from any working
# directory (as scripts/extract_zensus2022_students_by_age.py does).
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from braunschweig.calibration.srv_distance_targets import ZGB_KREISE  # noqa: E402

SOURCE_TABLE = "K3100101"
OUTPUT_PATH = REPO_ROOT / "eqasim-data/data/braunschweig/schools/lsn_university_sites.csv"
CENSUS_YEAR_OUTPUT_PATH = REPO_ROOT / "eqasim-data/data/braunschweig/schools/lsn_university_sites_ws2022_23.csv"

# Filled together with SITE_ROWS (hand-read from LSN-Online); main() refuses to run
# while any of the three is empty.
SEMESTER = "WS 2025/26"
ACCESS_DATE = "2026-11-27"
# Winter semester 2022/23 stands for the census day (2022-05-15); the level is a winter-semester stock
# and all institutions have a published figure for it (design decision of issue #140).
CENSUS_YEAR_SEMESTER = "WS 2022/23"

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
    ("TU Braunschweig", "Braunschweig, Stadt", "03101000", 15051),
    ("Hochsch. f. Bildende Künste Braunschweig", "Braunschweig, Stadt", "03101000", 873),
    ("TU Clausthal", "Clausthal-Zellerfeld, Stadt", "03153018", 2802),
    ("HS Braunschweig/Wolfenbüttel", "Salzgitter, Stadt", "03102000", 1737),
    ("HS Braunschweig/Wolfenbüttel", "Wolfsburg, Stadt", "03103000", 2366),
    ("HS Braunschweig/Wolfenbüttel", "Wolfenbüttel, Stadt", "03158037", 5123),
    ("HS Braunschweig/Wolfenbüttel", "Suderburg", "03360023", 983),
    ("Uni Hannover", "Hannover, Stadt", "03241001", 25377),
    ("HS Hannover", "Hannover, Stadt", "03241001", 8461),
    ("MHH Hannover", "Hannover, Stadt", "03241001", 4085),
    ("TiHo Hannover", "Hannover, Stadt", "03241001", 2306),
    ("Hochsch. f. Musik, Theater und Medien Hannover", "Hannover, Stadt", "03241001", 1345),
    ("Uni Göttingen", "Göttingen, Stadt", "03159016", 26855),
    ("Uni Hildesheim", "Hildesheim, Stadt", "03254021", 7342),
    ("HS Hildesheim/Holzminden/Göttingen", "Göttingen, Stadt", "03159016", 2249),
    ("HS Hildesheim/Holzminden/Göttingen", "Hildesheim, Stadt", "03254021", 2767),
    ("HS Hildesheim/Holzminden/Göttingen", "Holzminden, Stadt", "03255023", 990),
    ("Uni Lüneburg", "Lüneburg, Stadt", "03355022", 9267),
]

# The same 18 sites in the winter semester of the census year (the keys and their order equal
# SITE_ROWS; a test pins both against the committed transcription of the LSN table).
CENSUS_YEAR_SITE_ROWS: list[tuple[str, str, str, int]] = [
    ("TU Braunschweig", "Braunschweig, Stadt", "03101000", 16809),
    ("Hochsch. f. Bildende Künste Braunschweig", "Braunschweig, Stadt", "03101000", 937),
    ("TU Clausthal", "Clausthal-Zellerfeld, Stadt", "03153018", 3024),
    ("HS Braunschweig/Wolfenbüttel", "Salzgitter, Stadt", "03102000", 1877),
    ("HS Braunschweig/Wolfenbüttel", "Wolfsburg, Stadt", "03103000", 2516),
    ("HS Braunschweig/Wolfenbüttel", "Wolfenbüttel, Stadt", "03158037", 5290),
    ("HS Braunschweig/Wolfenbüttel", "Suderburg", "03360023", 1084),
    ("Uni Hannover", "Hannover, Stadt", "03241001", 28022),
    ("HS Hannover", "Hannover, Stadt", "03241001", 8911),
    ("MHH Hannover", "Hannover, Stadt", "03241001", 3916),
    ("TiHo Hannover", "Hannover, Stadt", "03241001", 2345),
    ("Hochsch. f. Musik, Theater und Medien Hannover", "Hannover, Stadt", "03241001", 1435),
    ("Uni Göttingen", "Göttingen, Stadt", "03159016", 27356),
    ("Uni Hildesheim", "Hildesheim, Stadt", "03254021", 8060),
    ("HS Hildesheim/Holzminden/Göttingen", "Göttingen, Stadt", "03159016", 1926),
    ("HS Hildesheim/Holzminden/Göttingen", "Hildesheim, Stadt", "03254021", 3147),
    ("HS Hildesheim/Holzminden/Göttingen", "Holzminden, Stadt", "03255023", 1175),
    ("Uni Lüneburg", "Lüneburg, Stadt", "03355022", 9490),
]

# Students of the three institutions in Sachsen-Anhalt (no LSN rows): table semester -> name as in
# _SURROUNDING of scripts/seed_nds_hochschulen.py -> (students, semester the figure is of).
# Source: Statistisches Landesamt Sachsen-Anhalt, GENESIS-Online table 21311-0003, read 2026-11-27, and
# for Magdeburg university in 2025/26 press release 186/2026 of 2026-07-17. Magdeburg-Stendal and Harz
# have no published figure for WS 2025/26 yet: their last published winter semester, 2024/25, is
# used (a counted fallback in the consumer, 2 of 21 institutions). These figures are not rows of the
# site CSVs written below.
OUTSIDE_LSN_STUDENTS: dict[str, dict[str, tuple[int, str]]] = {
    "WS 2025/26": {"OVGU Magdeburg": (13121, "WS 2025/26"),
                   "Hochschule Magdeburg-Stendal": (5169, "WS 2024/25"),
                   "Hochschule Harz": (2779, "WS 2024/25")},
    "WS 2022/23": {"OVGU Magdeburg": (12834, "WS 2022/23"),
                   "Hochschule Magdeburg-Stendal": (5252, "WS 2022/23"),
                   "Hochschule Harz": (3019, "WS 2022/23")},
}

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


def _display_path(path: Path) -> str:
    """Repository-relative path for the log where possible (a redirected output: as given)."""
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return str(path)


def _write_sites(rows, semester: str, output_path: Path) -> None:
    """Write one site table (header with its semester, then the rows) to ``output_path``."""
    frame = build_sites_frame(rows, semester)
    header = [
        f"# Source: LSN-Online {SOURCE_TABLE} (students per institution and site commune), "
        f"{semester}, accessed {ACCESS_DATE}",
        "# Script: scripts/seed_lsn_university_sites.py (values pinned in SITE_ROWS and "
        "CENSUS_YEAR_SITE_ROWS)",
        "# Licence: dl-de/by-2-0 (Landesamt fuer Statistik Niedersachsen)",
        "# Sachsen-Anhalt institutions are not in LSN: their students are pinned in "
        "OUTSIDE_LSN_STUDENTS of the script, not rows of this file; the institutions stay curated in "
        "scripts/seed_nds_hochschulen.py",
        f"# Rows: {len(frame)}; local {int((frame.scope == 'local').sum())}, "
        f"surrounding {int((frame.scope == 'surrounding').sum())}",
    ]
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8", newline="\n") as handle:
        handle.write("\n".join(header) + "\n")
        frame.to_csv(handle, index=False, lineterminator="\n")
    print(f"[seed_lsn_university_sites] wrote {_display_path(output_path)} "
          f"({len(frame)} rows, {semester})")


def main(argv=None) -> int:
    argparse.ArgumentParser(description=__doc__.splitlines()[0]).parse_args(argv)
    if not SITE_ROWS or not SEMESTER or not ACCESS_DATE:
        raise ValueError("SITE_ROWS, SEMESTER and ACCESS_DATE are empty: fill them from the "
                         f"LSN-Online table {SOURCE_TABLE} before running this script")
    for rows, semester, output_path in ((SITE_ROWS, SEMESTER, OUTPUT_PATH),
                                        (CENSUS_YEAR_SITE_ROWS, CENSUS_YEAR_SEMESTER,
                                         CENSUS_YEAR_OUTPUT_PATH)):
        _write_sites(rows, semester, output_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
