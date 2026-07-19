"""
Build the SrV 2023 per-Kreis trip-participation aggregate
(srv2023_participation_by_kreis.csv) from the raw SrV microdata.

Reads raw SrV data:
    eqasim-data/data/braunschweig/srv/srv2023_raw/SrV2023_Haushalte.csv
    eqasim-data/data/braunschweig/srv/srv2023_raw/SrV2023_Personen.csv
    eqasim-data/data/braunschweig/srv/srv2023_raw/SrV2023_Wege.csv

Computes per-Kreis participation shares (weighted by GEWICHT_P_ZENSUS) for three
trip purposes: work, education, leisure. Participation = share of weighted persons
with at least one trip of that purpose on the reporting day.

The Kreis (5-digit ARS) is derived as the first 5 digits of the zero-padded
8-digit household AGS and attached to persons via an HHNR join -- exactly as
in scripts/extract_srv_kreis_tables.py and
scripts/extract_srv_trip_classes_kreis.py. It is NOT the SrV survey design
stratum (ST_CODE): ST_CODE identifies the sampled-municipality stratum used
for the survey design and does not correspond 1:1 to a Kreis.

Purpose mapping (SrV E_ZWECK_9):
    - work: {1, 2} (Arbeit, berufliche Tätigkeit)
    - education: {3, 4} (Ausbildung, Schule)
    - leisure: {7} (Freizeit/Privat)

Filtered universe: persons with MITTL_WERKTAG == 1 (average weekday, Di-Do).

Output (committed): eqasim-data/data/braunschweig/srv/srv2023_participation_by_kreis.csv
with columns code (5-digit ARS), level ("kreis" or "total"), n_unweighted (int),
and float share columns work, leisure, education. Region-total row coded "03ZGB"
with level="total".

Usage:
    python scripts/build_srv_participation_aggregate.py [--data <eqasim-data/data/braunschweig>] [--out-dir <srv dir>]
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DATA_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig"

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("build_srv_participation_aggregate")

# SrV E_ZWECK_9 purpose codes
PURPOSE = {
    "work": {1, 2},
    "education": {3, 4},
    "leisure": {7},
}

HEADER = """\
# SrV 2023 trip-participation per-Kreis aggregate, built by
# scripts/build_srv_participation_aggregate.py from raw SrV microdata
# (eqasim-data/data/braunschweig/srv/srv2023_raw/SrV2023_Haushalte.csv,
# SrV2023_Personen.csv, and SrV2023_Wege.csv).
#
# Participation = share of weighted persons (GEWICHT_P_ZENSUS) with at least one
# trip of the given purpose (E_ZWECK_9) on the reporting day. Universe: persons
# with MITTL_WERKTAG == 1 (average weekday, Di-Do).
#
# Kreis is derived as the first 5 digits of the zero-padded 8-digit household
# AGS, attached to persons via an HHNR join. It is NOT the SrV survey design
# stratum (ST_CODE).
#
# Purpose mapping (E_ZWECK_9):
#   work = {1, 2} (Arbeit, berufliche Tätigkeit)
#   education = {3, 4} (Ausbildung, Schule)
#   leisure = {7} (Freizeit/Privat)
#
# Columns: code (5-digit ARS), level ("kreis" or "total"), n_unweighted (int),
# work/leisure/education (float shares, 0.0..1.0). Region-total row coded "03ZGB"
# with level="total".
"""


def compute_participation(persons: pd.DataFrame, wege: pd.DataFrame) -> pd.DataFrame:
    """
    Compute per-Kreis trip-participation shares from persons and trips.

    Parameters
    ----------
    persons : pd.DataFrame
        Persons table with columns: HHNR, PNR, kreis (5-digit ARS, derived from
        the household AGS -- see ``load_kreis_by_hhnr``), GEWICHT_P_ZENSUS.
    wege : pd.DataFrame
        Trips table with columns: HHNR, PNR, E_ZWECK_9.

    Returns
    -------
    pd.DataFrame
        Aggregated participation by Kreis and region total, with columns:
        code (5-digit ARS str), level ("kreis" or "total"), n_unweighted (int),
        work (float), leisure (float), education (float).
    """
    persons = persons.copy()
    persons["pid"] = persons["HHNR"].astype(str) + "_" + persons["PNR"].astype(str)
    persons["ars5"] = persons["kreis"].astype(str).str.zfill(5)
    persons["w"] = pd.to_numeric(persons["GEWICHT_P_ZENSUS"], errors="coerce").fillna(0.0)

    wege = wege.copy()
    wege["pid"] = wege["HHNR"].astype(str) + "_" + wege["PNR"].astype(str)

    # Identify persons with at least one trip of each purpose
    havers = {
        p: set(wege.loc[wege["E_ZWECK_9"].isin(codes), "pid"])
        for p, codes in PURPOSE.items()
    }

    # Aggregate by Kreis
    rows = []
    for ars5, g in persons.groupby("ars5"):
        tot = g["w"].sum()
        row = {"code": ars5, "level": "kreis", "n_unweighted": int(len(g))}
        for p in PURPOSE:
            row[p] = float(g.loc[g["pid"].isin(havers[p]), "w"].sum() / tot) if tot > 0 else 0.0
        rows.append(row)

    out = pd.DataFrame(rows)

    # Compute region total
    tot = persons["w"].sum()
    total = {"code": "03ZGB", "level": "total", "n_unweighted": int(len(persons))}
    for p in PURPOSE:
        total[p] = float(persons.loc[persons["pid"].isin(havers[p]), "w"].sum() / tot)
    return pd.concat([out, pd.DataFrame([total])], ignore_index=True)


def load_kreis_by_hhnr(households_path: Path) -> pd.Series:
    """
    Load the SrV household file and derive the Kreis lookup by HHNR.

    The Kreis (5-digit ARS) is the first 5 digits of the zero-padded 8-digit
    household AGS -- NOT the SrV survey design stratum (ST_CODE), which
    partitions the sampled municipalities for design purposes and does not
    correspond 1:1 to a Kreis (mirrors the pattern used by
    scripts/extract_srv_kreis_tables.py and scripts/extract_srv_trip_classes_kreis.py).

    Parameters
    ----------
    households_path : Path
        Path to SrV2023_Haushalte.csv.

    Returns
    -------
    pd.Series
        Kreis (5-digit ARS str) indexed by HHNR.
    """
    households = pd.read_csv(
        households_path, sep=";", decimal=",", encoding="latin-1", low_memory=False,
        usecols=["HHNR", "AGS"],
    )
    households["kreis"] = households["AGS"].astype(str).str.zfill(8).str[:5]
    return households.set_index("HHNR")["kreis"]


def write_aggregate(df: pd.DataFrame, out_path: Path) -> None:
    """Write the participation aggregate to a CSV file with header."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rounded = df.copy()
    rounded[["work", "leisure", "education"]] = rounded[["work", "leisure", "education"]].round(6)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(HEADER)
        rounded.to_csv(f, index=False)
    log.info("wrote %s (%d rows)", out_path, len(df))


def main(argv=None) -> int:
    """Read raw SrV data, compute participation, and write committed aggregate."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA_DEFAULT)
    parser.add_argument("--out-dir", type=Path, default=DATA_DEFAULT / "srv")
    args = parser.parse_args(argv)

    srv_raw_dir = args.data / "srv" / "srv2023_raw"
    haushalte_path = srv_raw_dir / "SrV2023_Haushalte.csv"
    personen_path = srv_raw_dir / "SrV2023_Personen.csv"
    wege_path = srv_raw_dir / "SrV2023_Wege.csv"

    if not haushalte_path.exists():
        log.error("raw households file not found: %s", haushalte_path)
        return 1
    if not personen_path.exists():
        log.error("raw persons file not found: %s", personen_path)
        return 1
    if not wege_path.exists():
        log.error("raw trips file not found: %s", wege_path)
        return 1

    log.info("reading %s", haushalte_path)
    kreis_by_hhnr = load_kreis_by_hhnr(haushalte_path)
    log.info("derived Kreis for %d households", len(kreis_by_hhnr))

    log.info("reading %s", personen_path)
    persons = pd.read_csv(
        personen_path, sep=";", decimal=",", encoding="latin-1", low_memory=False
    )
    log.info("read %d persons", len(persons))

    # Attach the Kreis (real 5-digit ARS, derived from the household AGS) via
    # HHNR. ST_CODE is the SrV survey design stratum, not the Kreis, and must
    # not be used as the grouping key here.
    persons["kreis"] = persons["HHNR"].map(kreis_by_hhnr)
    n_unmatched = int(persons["kreis"].isna().sum())
    if n_unmatched:
        raise RuntimeError(
            f"{n_unmatched} persons in '{personen_path.name}' could not be matched to a "
            "household via HHNR; the person and household files are expected to be "
            "fully joinable and this indicates a data integrity problem."
        )

    log.info("reading %s", wege_path)
    wege = pd.read_csv(wege_path, sep=";", decimal=",", encoding="latin-1", low_memory=False)
    log.info("read %d trips", len(wege))

    # Filter to average weekday
    before = len(persons)
    persons = persons[persons["MITTL_WERKTAG"] == 1]
    after = len(persons)
    log.info("filtered to MITTL_WERKTAG == 1: %d -> %d persons (%.1f%%)", before, after, 100.0 * after / before)

    # Compute participation and write
    agg = compute_participation(persons, wege)
    out_path = args.out_dir / "srv2023_participation_by_kreis.csv"
    write_aggregate(agg, out_path)

    # Log region-total row
    total_row = agg[agg["code"] == "03ZGB"].iloc[0]
    log.info("region total (03ZGB): work=%.4f, leisure=%.4f, education=%.4f",
             total_row["work"], total_row["leisure"], total_row["education"])

    return 0


if __name__ == "__main__":
    sys.exit(main())
