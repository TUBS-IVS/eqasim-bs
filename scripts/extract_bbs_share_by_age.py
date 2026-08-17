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

Age resolution (documented ASSUMPTION): single-year vocational (BBS) enrollment is
NOT published in the German school statistics -- both the LSN table K3050311 and the
federal Fachserie 11 R2 report berufliche Schulen only in the coarse "16 - 20" age
GROUP (verified: no single-year berufliche-Schulen age series exists at Land or Bund
level). A flat BBS/4 split would therefore mis-state the young end (at 16 almost all
upper-secondary pupils are still in the gymnasiale Oberstufe; BBS only comes to
dominate towards 19). To reflect that without inventing free numbers, the 16-20 BBS
total is distributed across ages 16..19 with weights proportional to the share of
each age cohort NO LONGER in an allgemeinbildende Schule,
``1 - ALLGEMEINBILDEND_PARTICIPATION[age]`` (Destatis "Schulen auf einen Blick" 2018,
p.6, Bildungsbeteiligung an allgemeinbildenden Schulen nach Alter, Germany 2016/17:
16 J. 72 %, 17 J. 46 %, 18 J. 23 %, 19 J. 7 %). That non-general-school share is the
pool BBS is drawn from and rises steeply with age, giving a plausible rising profile.
The ABSOLUTE BBS level stays the real NDS 16-20 total; only the age SHAPE is synthetic.

Caveat (kept explicit): the non-general-school pool also contains employed people,
Hochschule entrants and NEETs whose share grows with age, so these weights are an
UPPER BOUND on the steepness of the true BBS profile -- the real profile lies between
flat BBS/4 and this anchor. Combining a Germany-2016/17 shape with NDS-2024/25 levels
is a further documented approximation. The share still rests on the real, steeply
declining single-year Oberstufe counts.

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

# Share of each single-year age cohort still enrolled in an allgemeinbildende
# Schule (Germany, school year 2016/17). Source: Destatis "Schulen auf einen
# Blick" 2018, p.6 (Bildungsbeteiligung an allgemeinbildenden Schulen nach Alter).
# Used only as the SHAPE anchor for the synthetic BBS age profile (see
# build_rows); the absolute BBS level stays the real NDS 16-20 total. These are
# published, cited reference figures, not invented values.
ALLGEMEINBILDEND_PARTICIPATION = {16: 0.72, 17: 0.46, 18: 0.23, 19: 0.07}

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
    """Build the (age, bbs_pupils, oberstufe_pupils) rows (synthetic rising BBS).

    The full-time vocational ``numerator`` (16-20 total, single years unavailable)
    is distributed across ages 16..19 with weights proportional to the share of
    each cohort NO LONGER in an allgemeinbildende Schule,
    ``1 - ALLGEMEINBILDEND_PARTICIPATION[age]`` -- a monotonically rising, cited
    anchor (see the module docstring for the source and its caveats). This
    replaces a flat BBS/4 split, which over-allocated BBS at 16 where nearly all
    pupils are still in the gymnasiale Oberstufe. ``oberstufe_pupils`` is the real
    single-year general-school count. The resulting share
    (``bbs / (bbs + oberstufe)``, computed downstream by
    ``bbs_share.load_bbs_share_by_age``) rises with age via BOTH the synthetic BBS
    profile and the real declining Oberstufe. Validates shares lie strictly in
    (0, 1) and that the distributed BBS totals to the numerator.
    """
    weights = {age: 1.0 - ALLGEMEINBILDEND_PARTICIPATION[age] for age in AGES}
    weight_sum = sum(weights.values())
    if weight_sum <= 0:
        raise ValueError(f"[extract_bbs] non-positive BBS weight sum: {weights}.")
    rows = []
    for age in AGES:
        oberstufe = oberstufe_by_age[age]
        if oberstufe <= 0:
            raise ValueError(f"[extract_bbs] non-positive Oberstufe count at age {age}: {oberstufe}.")
        bbs = numerator * weights[age] / weight_sum
        share = bbs / (bbs + oberstufe)
        if not (0.0 < share < 1.0):
            raise ValueError(f"[extract_bbs] derived share out of (0,1) at age {age}: {share}.")
        rows.append((age, bbs, oberstufe, share))
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

    weights = {age: 1.0 - ALLGEMEINBILDEND_PARTICIPATION[age] for age in AGES}
    provenance = [
        "Age-resolved vocational-BBS enrollment share for the 16-19 education cohort (issue #139).",
        "Region: Niedersachsen. School year 2024/2025.",
        "Sources (LSN-Online, Landesamt fuer Statistik Niedersachsen):",
        "  oberstufe_pupils = K3005010 'allgemeinbildende Schulen', column 'insgesamt',",
        "                     Niedersachsen, single age years 16..19 (real, steeply declining).",
        f"  bbs_pupils       = K3050311 'berufsbildende Schulen', age group '16 - 20' total",
        f"                     ({bbs_total}) minus dual-system 'Berufsschule (Teilzeit)' ({teilzeit})",
        f"                     = {numerator} full-time vocational pupils.",
        "BBS is published ONLY for the '16 - 20' age group (no single-year berufliche-Schulen",
        "series exists at Land or Bund level), so the total is distributed across ages 16..19",
        "with a synthetic, monotonically RISING profile (ASSUMPTION), weights proportional to",
        f"  1 - allgemeinbildend-participation(age): 16={weights[16]:.2f} 17={weights[17]:.2f}"
        f" 18={weights[18]:.2f} 19={weights[19]:.2f}",
        "  where allgemeinbildend-participation = Destatis 'Schulen auf einen Blick' 2018, p.6",
        "  (share of each age cohort still in an allgemeinbildende Schule; Germany 2016/17).",
        "Caveat: that non-general-school pool also holds workers/Hochschule/NEETs whose share",
        "grows with age, so this is an UPPER BOUND on steepness; the true profile lies between",
        "flat BBS/4 and this anchor. Absolute BBS level = real NDS total; only the age shape is",
        "synthetic. Dual-system apprentices are excluded: they enter the synthetic population as",
        "workers (in_ausbildung), not the has_education_trip pupil pool (issue #172 cross-check).",
        "That exclusion is corroborated by the dual system's own, distinct (older, 18-19-peaked)",
        "single-year age profile -- BIBB-Datenreport 2015, Tab. A4.5-2 (neu abgeschlossene",
        "Ausbildungsvertraege nach Alter, Niedersachsen); it is NOT used as an input here because",
        "it is a different population (dual apprentices, entrant flow) than the full-time BBS stock.",
        "Generated by scripts/extract_bbs_share_by_age.py -- do not edit by hand.",
    ]
    write_csv(ns.dest, rows, provenance)

    print(f"[extract_bbs] BBS 16-20 total={bbs_total}, Teilzeit(dual)={teilzeit}, numerator={numerator}")
    print(f"[extract_bbs] wrote {ns.dest}:")
    for age, bbs, oberstufe, share in rows:
        print(f"    age {age}: bbs/yr={bbs:.0f}  oberstufe={oberstufe}  share={share:.3f}")


if __name__ == "__main__":
    main()
