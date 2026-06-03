"""
Seed CSVs for the MiD-2023-derived reference tables that used to be hard-
coded in Python modules.

Background
----------
Several Braunschweig stages historically embedded MiD reference percentages
as Python literals (CARS_BY_KREIS, BIKES_BY_KREIS, car/bike/PT subscription
constraints, the H4 income-by-size matrix, the income-class midpoint
lookup). All values were manually transcribed from the MiD 2023 regional
table volume (Tabellen P19, P22, P24.1, H4, H7, H12.3).

This script materialises those literals as authoritative CSV files under
``eqasim-data/data/braunschweig/mid/``. The downstream Python modules
(``braunschweig.data.mid.data``, ``braunschweig.data.census.household_income``,
``braunschweig.synthesis.population.enriched``) then load these CSVs at
pipeline runtime instead of relying on Python module-level globals.

Why a seed script and not a PDF parser
--------------------------------------
``scripts/extract_mid_tables.py`` already parses the same PDF for the
P9/P12.1/P13/P17.1 tables; extending it to the constraint tables would
introduce new parsing risk for percentages that have already been QC-ed
against the PDF by hand. The seed script is therefore the safest way to
preserve the *exact* values previously baked into the code while moving
them out of Python source.

Usage
-----
::

    python scripts/seed_mid_constraint_tables.py

The script is idempotent and prints which files were (over)written. By
default the output directory is ``eqasim-data/data/braunschweig/mid``.

Re-running the script after a manual PDF re-read is the supported way to
update the reference values; the diff in the CSVs then becomes part of
the data-provenance trail.
"""

from __future__ import annotations

import argparse
import math
import os
import sys
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO / "eqasim-data" / "data" / "braunschweig" / "mid"


# ---------------------------------------------------------------------------
# MiD 2023 regional sample — values transcribed from the MiD 2023 regional
# table volume. All percentages are stored as fractions in [0, 1].
# ---------------------------------------------------------------------------

# Tabelle A P19 'jederzeit' Anteil (Auto-Verfügbarkeit).
P19_CAR_AVAIL_ZONE = [
    ("braunschweig",  0.67),
    ("salzgitter",    0.80),
    ("wolfsburg",     0.68),
    ("gifhorn",       0.89),
    ("goslar",        0.71),
    ("helmstedt",     0.77),
    ("peine",         0.80),
    ("wolfenbuettel", 0.76),
    ("external",      0.76),  # fallback: Gesamtregion proxy
]
P19_CAR_AVAIL_SEX = [("male", 0.80), ("female", 0.72)]
P19_CAR_AVAIL_AGE = [
    (14, 17, 0.41),
    (18, 29, 0.61),
    (30, 39, 0.77),
    (40, 49, 0.84),
    (50, 59, 0.86),
    (60, 64, 0.82),
    (65, 74, 0.84),
    (75, 79, 0.79),
    (80, math.inf, 0.60),
]

# Tabelle A P22 'ja' (eigenes Fahrrad).
P22_BICYCLE_ZONE = [
    ("braunschweig",  0.73),
    ("salzgitter",    0.65),
    ("wolfsburg",     0.57),
    ("gifhorn",       0.78),
    ("goslar",        0.54),
    ("helmstedt",     0.64),
    ("peine",         0.76),
    ("wolfenbuettel", 0.63),
    ("external",      0.67),
]
P22_BICYCLE_SEX = [("male", 0.72), ("female", 0.63)]
P22_BICYCLE_AGE = [
    (-math.inf, 6, 0.52),
    (7, 10, 0.95),
    (11, 13, 0.86),
    (14, 17, 0.85),
    (18, 29, 0.74),
    (30, 39, 0.74),
    (40, 49, 0.78),
    (50, 59, 0.73),
    (60, 64, 0.64),
    (65, 74, 0.55),
    (75, 79, 0.48),
    (80, math.inf, 0.36),
]

# Tabelle A P24.1: Summe Deutschlandticket + Wochen-/Monatskarte (Abo) +
# Jobticket/Semesterticket.
P24_1_PT_ZONE = [
    ("braunschweig",  0.26),
    ("salzgitter",    0.20),
    ("wolfsburg",     0.18),
    ("gifhorn",       0.16),
    ("goslar",        0.19),
    ("helmstedt",     0.19),
    ("peine",         0.14),
    ("wolfenbuettel", 0.14),
    ("external",      0.19),
]
P24_1_PT_SEX = [("male", 0.20), ("female", 0.18)]
P24_1_PT_AGE = [
    (14, 17, 0.65),
    (18, 29, 0.43),
    (30, 39, 0.20),
    (40, 49, 0.21),
    (50, 59, 0.12),
    (60, 64, 0.09),
    (65, 74, 0.06),
    (75, 79, 0.07),
    (80, math.inf, 0.06),
]

# Tabelle H7 'Anzahl Autos im Haushalt' (Zeilen%, columns 0/1/2/3+).
H7_CARS_BY_KREIS = {
    "03101": (0.25, 0.53, 0.20, 0.02),  # Braunschweig
    "03102": (0.10, 0.62, 0.22, 0.06),  # Salzgitter
    "03103": (0.17, 0.57, 0.22, 0.04),  # Wolfsburg
    "03151": (0.06, 0.50, 0.35, 0.08),  # Gifhorn
    "03153": (0.22, 0.53, 0.21, 0.04),  # Goslar
    "03154": (0.14, 0.52, 0.27, 0.07),  # Helmstedt
    "03157": (0.07, 0.48, 0.37, 0.08),  # Peine
    "03158": (0.13, 0.56, 0.22, 0.09),  # Wolfenbuettel
}
H7_CARS_REGION = (0.15, 0.53, 0.26, 0.06)  # Gesamt row

# Tabelle H12.3 'Anzahl Fahrraeder/Pedelecs/E-Bikes im Haushalt'
# (Zeilen%, columns 0/1/2/3/4+).
H12_3_BIKES_BY_KREIS = {
    "03101": (0.17, 0.25, 0.26, 0.12, 0.21),
    "03102": (0.23, 0.24, 0.25, 0.11, 0.17),
    "03103": (0.36, 0.22, 0.21, 0.08, 0.14),
    "03151": (0.12, 0.22, 0.25, 0.14, 0.26),
    "03153": (0.36, 0.23, 0.17, 0.09, 0.15),
    "03154": (0.28, 0.16, 0.29, 0.13, 0.15),
    "03157": (0.18, 0.23, 0.22, 0.16, 0.22),
    "03158": (0.23, 0.27, 0.17, 0.13, 0.20),
}
H12_3_BIKES_REGION = (0.23, 0.23, 0.23, 0.12, 0.20)

# Tabelle H4 — Zeilengruppe Haushaltsgröße, Zeilen% in households.
# Columns: sehr_niedrig, niedrig, mittel, hoch, sehr_hoch (BMDV quintiles).
H4_INCOME_BY_SIZE = {
    "1":  (0.16, 0.14, 0.39, 0.26, 0.03),
    "2":  (0.04, 0.15, 0.28, 0.38, 0.15),
    "3":  (0.03, 0.03, 0.17, 0.55, 0.22),
    "4":  (0.02, 0.04, 0.23, 0.51, 0.19),
    "5":  (0.04, 0.08, 0.30, 0.44, 0.14),  # H4 'mind. 5'
    "6+": (0.04, 0.08, 0.30, 0.44, 0.14),  # MiD H4 lumps with 5P
}

# Class midpoints used to derive numeric €-income downstream.  '5000+' is
# open ended; 6000 € chosen as the conservative DESTATIS 2022 average for
# households above the BMDV 5000-threshold.
CLASS_MIDPOINT_EUR = {
    "0-500":     250.0,
    "1500-2000": 1750.0,
    "2600-3000": 2800.0,
    "3600-4500": 4050.0,
    "5000+":     6000.0,
}


# ---------------------------------------------------------------------------
# CSV writers
# ---------------------------------------------------------------------------

def _age_label(lo: float, hi: float) -> str:
    """Human-readable age band label, e.g. '14-17', '0-6', '80+'."""
    if math.isinf(lo) and lo < 0:
        return f"upto_{int(hi)}"
    if math.isinf(hi):
        return f"{int(lo)}+"
    return f"{int(lo)}-{int(hi)}"


def _constraint_rows(zone_rows, sex_rows, age_rows) -> list[dict[str, Any]]:
    """Flatten the three breakdowns into a long-form constraint table."""
    out: list[dict[str, Any]] = []
    for zone, target in zone_rows:
        out.append({"dimension": "zone", "key": zone,
                    "age_lo": "", "age_hi": "", "target": float(target)})
    for sex, target in sex_rows:
        out.append({"dimension": "sex", "key": sex,
                    "age_lo": "", "age_hi": "", "target": float(target)})
    for lo, hi, target in age_rows:
        out.append({"dimension": "age", "key": _age_label(lo, hi),
                    "age_lo": lo, "age_hi": hi, "target": float(target)})
    return out


def _kreis_share_rows(by_kreis: dict[str, tuple], region: tuple,
                      values: Iterable[int]) -> pd.DataFrame:
    """Long-form Kreis × count-bucket share table including a ``Gesamt`` row."""
    cols = [str(v) for v in values]
    rows = [{"ars5": k, **dict(zip(cols, shares))}
            for k, shares in by_kreis.items()]
    rows.append({"ars5": "Gesamt", **dict(zip(cols, region))})
    return pd.DataFrame(rows, columns=["ars5", *cols])


def _income_by_size_rows() -> pd.DataFrame:
    cols = ["sehr_niedrig", "niedrig", "mittel", "hoch", "sehr_hoch"]
    rows = [{"household_size": s, **dict(zip(cols, shares))}
            for s, shares in H4_INCOME_BY_SIZE.items()]
    return pd.DataFrame(rows, columns=["household_size", *cols])


def _midpoint_rows() -> pd.DataFrame:
    return pd.DataFrame(
        [{"income_class": k, "midpoint_eur": v}
         for k, v in CLASS_MIDPOINT_EUR.items()],
        columns=["income_class", "midpoint_eur"],
    )


def _write(df: pd.DataFrame, path: Path, header_comment: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        for line in header_comment.splitlines():
            fh.write(f"# {line}\n")
        df.to_csv(fh, index=False)
    try:
        display = path.relative_to(REPO)
    except ValueError:
        display = path
    print(f"  wrote {display}  ({len(df)} rows)")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT,
                   help="Output directory (default: %(default)s)")
    args = p.parse_args(argv)

    out = args.out_dir
    print(f"[seed_mid_constraint_tables] writing to {out}")

    _write(
        pd.DataFrame(_constraint_rows(P19_CAR_AVAIL_ZONE,
                                      P19_CAR_AVAIL_SEX,
                                      P19_CAR_AVAIL_AGE)),
        out / "mid2023_P19_car_constraints.csv",
        "MiD 2023 regional sample Tabelle A P19 'jederzeit' (car availability).\n"
        "Source: MiD 2023 Tabelle P19. Values are fractions in [0, 1].",
    )
    _write(
        pd.DataFrame(_constraint_rows(P22_BICYCLE_ZONE,
                                      P22_BICYCLE_SEX,
                                      P22_BICYCLE_AGE)),
        out / "mid2023_P22_bicycle_constraints.csv",
        "MiD 2023 regional sample Tabelle A P22 'ja' (own bicycle).\n"
        "Source: MiD 2023 Tabelle P22. Values are fractions in [0, 1].",
    )
    _write(
        pd.DataFrame(_constraint_rows(P24_1_PT_ZONE,
                                      P24_1_PT_SEX,
                                      P24_1_PT_AGE)),
        out / "mid2023_P24_1_pt_subscription_constraints.csv",
        "MiD 2023 regional sample Tabelle A P24.1 (PT subscription = sum of\n"
        "Deutschlandticket + Wochen-/Monatskarte Abo + Jobticket/Semesterticket).\n"
        "Source: MiD 2023 Tabelle P24.1. Values are fractions in [0, 1].",
    )
    _write(
        _kreis_share_rows(H7_CARS_BY_KREIS, H7_CARS_REGION, [0, 1, 2, 3]),
        out / "mid2023_H7_cars_by_kreis.csv",
        "MiD 2023 regional sample Tabelle H7 'Anzahl Autos im Haushalt'.\n"
        "ars5='Gesamt' is the region-wide row used as fallback.\n"
        "Columns 0/1/2/3 are the share with that vehicle count (3 == 3+).",
    )
    _write(
        _kreis_share_rows(H12_3_BIKES_BY_KREIS, H12_3_BIKES_REGION,
                          [0, 1, 2, 3, 4]),
        out / "mid2023_H12_3_bikes_by_kreis.csv",
        "MiD 2023 regional sample Tabelle H12.3 'Anzahl Fahrraeder/Pedelecs/\n"
        "E-Bikes im Haushalt'. ars5='Gesamt' is the region-wide fallback.\n"
        "Column 4 means 4+.",
    )
    _write(
        _income_by_size_rows(),
        out / "mid2023_H4_income_by_size.csv",
        "MiD 2023 regional sample Tabelle H4 'Ökonomischer Status des Haushalts'\n"
        "by household size (BMDV quintiles, Zeilen%). 5+ HH share is mirrored\n"
        "to keys '5' and '6+' to support the IPF household-size split.",
    )
    _write(
        _midpoint_rows(),
        out / "mid2023_class_midpoint_eur.csv",
        "Class-midpoint € per BMDV income bin. Used to compute numeric\n"
        "household_income_eur (post INKAR scaling).",
    )

    print("[seed_mid_constraint_tables] done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
