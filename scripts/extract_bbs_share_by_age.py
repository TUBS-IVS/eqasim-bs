"""Extract the age-resolved vocational-BBS enrollment share for the 16-19 cohort.

Issue #139. The education gravity model (``braunschweig.synthesis.locations.
education_gravity``) splits age-16-19 pupils between the vocational berufsbildende
Schule (BBS; sparse, regional catchment, long trips) and the local gymnasiale
Oberstufe. Without an age-resolved reference it falls back to a single scalar
(``education_bbs_share``, historically 0.681) applied to all ages -- the primary,
age-resolved path is never taken (silent-fallback rule). This script derives the
committed reference ``nds_bbs_share_by_age.csv`` from two Landesamt fuer Statistik
Niedersachsen (LSN) school-statistics tables.

Sources (LSN-Online SpreadsheetML exports, Niedersachsen, school year 2024/2025;
raw exports are local-only, kept under ``<schools>/raw/`` and NOT committed):
  * ``K3005010`` -- Schuelerinnen und Schueler an ALLGEMEINBILDENDEN Schulen,
    by single age year x Schulform x region. Provides ``oberstufe_pupils(age)``
    as the "Schuelerinnen und Schueler insgesamt" column for Niedersachsen at
    each single age 16..19 (all general schools; at 16-19 essentially the
    academic upper secondary with local catchment).
  * ``K3050311`` -- BERUFSBILDENDE Schulen, by Schulform x age GROUP x region.
    BBS is only published for the age GROUP "16 - 20" (= ages 16..19), so a single
    16-19 vocational total is available, not single years.

Numerator decision (issue #139, cross-checked against #172): the BBS "16 - 20"
total (``Schulformen insgesamt``) is reduced by the dual-system part-time
Berufsschule ("Berufsschule (Teilzeit)"). Those are employed apprentices
(``in_ausbildung``), represented in the synthetic population predominantly as
WORKERS with a work trip, so they do not enter the education-pupil pool
(``education_gravity`` selects on ``has_education_trip``). Including them would
inflate the vocational share of the actual pupil pool. The retained numerator is
the FULL-TIME vocational enrollment ``bbs_total_16_20 - berufsschule_teilzeit_16_20``
(Berufsfachschule, Berufliches Gymnasium, Fachoberschule, Berufsschule Vollzeit,
Fachschule, ...).

Age resolution (documented ASSUMPTION): BBS enrollment is not available per single
year, so it is distributed UNIFORMLY across the four years 16..19
(``bbs_per_year = numerator / 4``). The age-resolved rise of the share
(``bbs / (bbs + oberstufe)``) is therefore driven by the real, steeply declining
single-year Oberstufe counts. This is a transparent approximation; real BBS
enrollment also rises with age, so the true profile is likely somewhat steeper.

Output CSV schema (consumed by ``braunschweig.data.schools.bbs_share``):
``age,bbs_pupils,oberstufe_pupils`` with a ``#``-prefixed provenance header.

Usage::

    python scripts/extract_bbs_share_by_age.py            # default raw/ + dest
    python scripts/extract_bbs_share_by_age.py \
        --allgemeinbildend <K3005010.xml> --beruflich <K3050311.xml> \
        --dest eqasim-data/data/braunschweig/schools/nds_bbs_share_by_age.csv
"""
from __future__ import annotations

import argparse
import os
import xml.etree.ElementTree as ET
from pathlib import Path

_SS = "urn:schemas-microsoft-com:office:spreadsheet"
_CELL = f"{{{_SS}}}Cell"
_ROW = f"{{{_SS}}}Row"
_DATA = f"{{{_SS}}}Data"
_INDEX = f"{{{_SS}}}Index"

AGES = (16, 17, 18, 19)
REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHOOLS = REPO_ROOT / "eqasim-data" / "data" / "braunschweig" / "schools"
DEFAULT_ALLGEMEIN = _SCHOOLS / "raw" / "lsn_K3005010_allgemeinbildende_schulen_nach_alter_nds.xml"
DEFAULT_BERUFLICH = _SCHOOLS / "raw" / "lsn_K3050311_berufsbildende_schulen_nach_alter_nds.xml"
DEFAULT_DEST = _SCHOOLS / "nds_bbs_share_by_age.csv"


def _parse_rows(path):
    """Yield each SpreadsheetML row as a 1-indexed-aware list of cell strings.

    SpreadsheetML omits empty cells and encodes column jumps via ``ss:Index``;
    this expands them so column positions are stable across rows.
    """
    tree = ET.parse(path)
    rows = []
    for row in tree.getroot().iter(_ROW):
        cells = []
        col = 0
        for cell in row.findall(_CELL):
            idx = cell.get(_INDEX)
            if idx is not None:
                # ss:Index is 1-based; pad missing leading columns.
                target = int(idx) - 1
                while col < target:
                    cells.append(None)
                    col += 1
            data = cell.find(_DATA)
            cells.append(data.text if data is not None else None)
            col += 1
        rows.append(cells)
    return rows


def _to_int(value):
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except ValueError:
        return None


def _first_row_starting_with(rows, label):
    """Return the first row whose first non-empty cell text starts with ``label``."""
    for row in rows:
        for cell in row:
            if cell is not None and str(cell).strip():
                if str(cell).strip().startswith(label):
                    return row
                break
    return None


def extract_beruflich_numerator(beruflich_path):
    """Return (bbs_total_16_20, berufsschule_teilzeit_16_20, numerator) for Niedersachsen.

    Columns of a Schulform row: [name, Insges, "unter 16", "16 - 20", "20 - 25",
    "25 u. aelter"]. The "16 - 20" group total is the 4th value (index 3 among the
    non-name numeric cells). The first "Schulformen insgesamt" / "Berufsschule
    (Teilzeit)" rows belong to the Niedersachsen state block (the first region).
    """
    rows = _parse_rows(beruflich_path)

    def group_16_20(label):
        row = _first_row_starting_with(rows, label)
        if row is None:
            raise ValueError(f"[extract_bbs] {beruflich_path}: row {label!r} not found.")
        nums = [_to_int(c) for c in row if _to_int(c) is not None]
        if len(nums) < 3:
            raise ValueError(f"[extract_bbs] {beruflich_path}: row {label!r} has too few numeric cells: {nums}.")
        # nums = [Insges, unter16, 16-20, 20-25, 25+]; the "16 - 20" group is index 2.
        return nums[2]

    bbs_total = group_16_20("Schulformen insgesamt")
    teilzeit = group_16_20("Berufsschule (Teilzeit)")
    numerator = bbs_total - teilzeit
    if not (0 < teilzeit < bbs_total):
        raise ValueError(
            f"[extract_bbs] implausible BBS counts: total={bbs_total}, teilzeit={teilzeit}."
        )
    return bbs_total, teilzeit, numerator


def extract_oberstufe_by_age(allgemeinbildend_path):
    """Return {age: oberstufe_pupils} for Niedersachsen, single years 16..19.

    Age rows are labelled "16 - 17" (= age 16) ... "19 - 20" (= age 19); the
    "Schuelerinnen und Schueler insgesamt" total is the first numeric cell. The
    first occurrence of each label is the Niedersachsen state block (first region).
    """
    rows = _parse_rows(allgemeinbildend_path)
    label_for = {f"{a} - {a + 1}": a for a in AGES}
    out = {}
    for row in rows:
        first = next((str(c).strip() for c in row if c is not None and str(c).strip()), None)
        if first in label_for and label_for[first] not in out:
            nums = [_to_int(c) for c in row if _to_int(c) is not None]
            if not nums:
                raise ValueError(f"[extract_bbs] {allgemeinbildend_path}: age row {first!r} has no numeric cell.")
            out[label_for[first]] = nums[0]  # "insgesamt" column
        if len(out) == len(AGES):
            break
    missing = [a for a in AGES if a not in out]
    if missing:
        raise ValueError(f"[extract_bbs] {allgemeinbildend_path}: missing age rows {missing}.")
    return out


def build_rows(numerator, oberstufe_by_age):
    """Build the (age, bbs_pupils, oberstufe_pupils) rows (flat BBS/4 assumption).

    ``bbs_pupils`` is the full-time vocational numerator spread uniformly over the
    four years; ``oberstufe_pupils`` is the real single-year general-school count.
    The resulting ``bbs / (bbs + oberstufe)`` share (computed downstream by
    ``bbs_share.load_bbs_share_by_age``) is age-resolved via the declining
    Oberstufe. Validates the derived shares lie strictly in (0, 1).
    """
    bbs_per_year = numerator / len(AGES)
    rows = []
    for age in AGES:
        oberstufe = oberstufe_by_age[age]
        if oberstufe <= 0:
            raise ValueError(f"[extract_bbs] non-positive Oberstufe count at age {age}: {oberstufe}.")
        share = bbs_per_year / (bbs_per_year + oberstufe)
        if not (0.0 < share < 1.0):
            raise ValueError(f"[extract_bbs] derived share out of (0,1) at age {age}: {share}.")
        rows.append((age, bbs_per_year, oberstufe, share))
    return rows


def write_csv(dest, rows, provenance):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="") as handle:
        for line in provenance:
            handle.write(f"# {line}\n")
        handle.write("age,bbs_pupils,oberstufe_pupils\n")
        for age, bbs, oberstufe, _share in rows:
            handle.write(f"{age},{bbs:.1f},{oberstufe}\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--allgemeinbildend", default=str(DEFAULT_ALLGEMEIN))
    ap.add_argument("--beruflich", default=str(DEFAULT_BERUFLICH))
    ap.add_argument("--dest", default=str(DEFAULT_DEST))
    ns = ap.parse_args(argv)

    for path in (ns.allgemeinbildend, ns.beruflich):
        if not os.path.exists(path):
            raise SystemExit(
                f"[extract_bbs] missing source {path!r}. Drop the LSN SpreadsheetML "
                f"exports (K3005010 allgemeinbildend, K3050311 berufsbildend) under "
                f"{_SCHOOLS / 'raw'} or pass --allgemeinbildend/--beruflich."
            )

    bbs_total, teilzeit, numerator = extract_beruflich_numerator(ns.beruflich)
    oberstufe_by_age = extract_oberstufe_by_age(ns.allgemeinbildend)
    rows = build_rows(numerator, oberstufe_by_age)

    provenance = [
        "Age-resolved vocational-BBS enrollment share for the 16-19 education cohort (issue #139).",
        "Region: Niedersachsen. School year 2024/2025.",
        "Sources (LSN-Online, Landesamt fuer Statistik Niedersachsen):",
        "  oberstufe_pupils = K3005010 'allgemeinbildende Schulen', column 'insgesamt',",
        "                     Niedersachsen, single age years 16..19.",
        f"  bbs_pupils       = K3050311 'berufsbildende Schulen', age group '16 - 20' total",
        f"                     ({bbs_total}) minus dual-system 'Berufsschule (Teilzeit)' ({teilzeit})",
        f"                     = {numerator} full-time vocational pupils, spread uniformly over",
        "                     ages 16..19 (BBS not published per single year -- documented",
        "                     ASSUMPTION; the age-resolved rise comes from the declining Oberstufe).",
        "Dual-system apprentices are excluded because they enter the synthetic population as",
        "workers (in_ausbildung), not the has_education_trip pupil pool (issue #172 cross-check).",
        "Generated by scripts/extract_bbs_share_by_age.py -- do not edit by hand.",
    ]
    write_csv(ns.dest, rows, provenance)

    print(f"[extract_bbs] BBS 16-20 total={bbs_total}, Teilzeit(dual)={teilzeit}, numerator={numerator}")
    print(f"[extract_bbs] wrote {ns.dest}:")
    for age, bbs, oberstufe, share in rows:
        print(f"    age {age}: bbs/yr={bbs:.0f}  oberstufe={oberstufe}  share={share:.3f}")


if __name__ == "__main__":
    main()
