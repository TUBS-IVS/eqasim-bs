"""
Build the SrV 2023 per-Kreis trip-participation control target
(target2026_<purpose>_participation_by_kreis.csv) from the committed SrV
aggregate table (Task 2 of feature #224, following Task 1's
srv2023_participation_by_kreis.csv aggregate).

Reads ONLY the committed SrV aggregate (no raw microdata):
    eqasim-data/data/braunschweig/srv/srv2023_participation_by_kreis.csv

Purposes: work, education, leisure (SrV E_ZWECK_9 groupings, see the aggregate's
own header). This module is parametric by purpose; Task 2 generates the `work`
target, Task 5 reuses the same script for `leisure` and `education`.

Documented decisions (verbatim; also recorded in the written target CSV header):

1. DECISION (level anchoring): shares are the SrV participation levels directly
   (regional survey = regional behaviour authority). This control anchors the
   synthetic <purpose>-participation distribution to the SrV level.
   ASSUMPTION (honest caveat): SrV and MiD measure participation differently
   (survey design, universe, and purpose taxonomy all differ); this is a
   DELIBERATE SrV-level anchoring, not a claim that SrV and MiD agree.
2. ASSUMPTION (Wolfsburg, SrV region-total convention): 03103 is not covered by
   SrV; its <purpose>_yes share is the SrV region total (03ZGB) directly --
   the SAME convention as target2026_has_ebike_by_kreis.csv. Unlike the
   trip_class target, NO MiD-P36 immobility pattern transfer is applied here:
   that transfer is specific to immobility, not to purpose-participation.

Output (committed): eqasim-data/data/braunschweig/targets/
target2026_<purpose>_participation_by_kreis.csv with columns
ars5,source,n_effective,<purpose>_yes,<purpose>_no (fractions summing to 1 per
row, rows = 7 SrV Kreise + Wolfsburg + Gesamt). This is a FINAL target: the
kreis_attribute_control registry must consume it with prior_n = 0.

Usage:
    python scripts/build_participation_target.py [--data <eqasim-data/data/braunschweig>]
        [--out-dir <targets dir>] [--purpose {work,leisure,education}]
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
log = logging.getLogger("build_participation_target")

PURPOSES = ("work", "leisure", "education")

# Wolfsburg (kreisfreie Stadt) is not covered by the SrV 2023 Braunschweig+RGB
# survey; per the documented ASSUMPTION above, its row uses the SrV region total
# directly (identical convention to target2026_has_ebike_by_kreis.csv; unlike
# the trip_class target, no MiD pattern transfer is applied for participation).
WOLFSBURG_ARS5 = "03103"

HEADER_TEMPLATE = """\
# SrV 2023 {purpose}-participation per-Kreis control target, built by
# scripts/build_participation_target.py from the COMMITTED SrV aggregate
# (eqasim-data/data/braunschweig/srv/srv2023_participation_by_kreis.csv; NO raw
# microdata). Shares are the SrV {purpose} participation levels (share of
# weighted persons with >= 1 trip of this purpose on the reporting day).
#
# DECISION (level anchoring): shares are the SrV participation levels directly
# (regional survey = regional behaviour authority) -- this control DELIBERATELY
# anchors the synthetic {purpose}-participation distribution to the SrV level.
# ASSUMPTION (honest caveat): SrV and MiD measure participation differently
# (survey design, universe, and purpose taxonomy all differ); this anchoring
# is a deliberate choice, not a claim that SrV and MiD levels agree.
#
# ASSUMPTION (Wolfsburg, SrV region-total convention): 03103 is not covered by
# SrV. Its {purpose}_yes share is the SrV region total (03ZGB) directly -- the
# SAME convention as target2026_has_ebike_by_kreis.csv. Unlike the trip_class
# target, NO MiD-P36 immobility pattern transfer is applied here: that transfer
# is specific to immobility, not to purpose-participation.
#
# CONSUMER NOTE: FINAL target - use with kreis_attribute_control prior_n = 0.
"""


def read_srv_source(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"build_participation_target: required committed input missing: {path}")
    return pd.read_csv(path, comment="#", dtype={"code": str})


def build_participation_target(data: Path, purpose: str) -> pd.DataFrame:
    """Build the <purpose>-participation target frame (ars5, source,
    n_effective, <purpose>_yes, <purpose>_no) from the committed SrV
    participation aggregate; the Wolfsburg row is the SrV region total used
    directly (no pattern transfer, see module header). Fails fast if the
    region-total row or any Kreis row is missing (no under-constrained
    control)."""
    if purpose not in PURPOSES:
        raise ValueError(f"build_participation_target: purpose must be one of {PURPOSES}, got {purpose!r}.")

    src = read_srv_source(data / "srv" / "srv2023_participation_by_kreis.csv")
    if purpose not in src.columns:
        raise ValueError(
            f"build_participation_target: source is missing column {purpose!r}; has {list(src.columns)}.")

    total_rows = src[src["level"] == "total"]
    if len(total_rows) != 1:
        raise ValueError(
            f"build_participation_target: expected exactly one region-total row (level == 'total'), "
            f"found {len(total_rows)}.")
    total_row = total_rows.iloc[0]

    kreis_rows = src[src["level"] == "kreis"].copy()
    if kreis_rows.empty:
        raise ValueError("build_participation_target: no Kreis rows (level == 'kreis') in the SrV source.")
    # The SrV code column carries 5-digit ARS codes with a leading zero (e.g. "03101");
    # zfill guards against upstream loss of the leading zero (e.g. int coercion).
    kreis_rows["code"] = kreis_rows["code"].str.zfill(5)

    yes_col, no_col = f"{purpose}_yes", f"{purpose}_no"

    rows = []
    for _, r in kreis_rows.iterrows():
        yes = float(r[purpose])
        rows.append({
            "ars5": r["code"], "source": "srv", "n_effective": int(r["n_unweighted"]),
            yes_col: yes, no_col: 1.0 - yes,
        })

    # Wolfsburg: SrV region-total share used directly (no MiD pattern transfer;
    # that transfer is specific to trip_class immobility, not participation).
    total_yes = float(total_row[purpose])
    total_n = int(total_row["n_unweighted"])
    rows.append({
        "ars5": WOLFSBURG_ARS5, "source": "srv_region_total", "n_effective": total_n,
        yes_col: total_yes, no_col: 1.0 - total_yes,
    })
    rows.append({
        "ars5": "Gesamt", "source": "srv", "n_effective": total_n,
        yes_col: total_yes, no_col: 1.0 - total_yes,
    })
    return pd.DataFrame(rows, columns=["ars5", "source", "n_effective", yes_col, no_col])


def write_target(df: pd.DataFrame, purpose: str, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    yes_col, no_col = f"{purpose}_yes", f"{purpose}_no"
    rounded = df.copy()
    rounded[[yes_col, no_col]] = rounded[[yes_col, no_col]].round(4)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(HEADER_TEMPLATE.format(purpose=purpose))
        rounded.to_csv(f, index=False)
    log.info("wrote %s (%d rows; sources: %s)", out_path, len(df), df["source"].value_counts().to_dict())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA_DEFAULT)
    parser.add_argument("--out-dir", type=Path, default=DATA_DEFAULT / "targets")
    parser.add_argument("--purpose", choices=PURPOSES, default="work")
    args = parser.parse_args(argv)
    target = build_participation_target(args.data, args.purpose)
    write_target(target, args.purpose, args.out_dir / f"target2026_{args.purpose}_participation_by_kreis.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
