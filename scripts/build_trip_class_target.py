"""
Build the SrV 2023 per-Kreis trip-class control target
(target2026_trip_class_by_kreis.csv) from the committed SrV aggregate table
(Task 1 of docs/superpowers/plans/2026-07-08-trip-class-kreis-control.md).

Reads ONLY the committed SrV aggregate (no raw microdata):
    eqasim-data/data/braunschweig/srv/srv2023_trip_classes_by_kreis.csv

Classes (SrV E_ANZ_WEGE, matched by braunschweig.popsim.attributes.map_trip_class
on the MiD side): 0 / 1-2 / 3-4 / 5+ trips on the reporting day. The four
``trips_*`` share columns are renormalised to sum to 1 per row (``share_trips_invalid``
is dropped, mirroring how ``scripts/build_blended_kreis_targets.py`` drops
provenance-only columns).

Documented decisions (verbatim; also recorded in the written target CSV header --
see docs/superpowers/plans/2026-07-08-trip-class-kreis-control.md, Global Constraints):

1. ASSUMPTION (universe): target = SrV Di-Do mittlerer Werktag; seed universe =
   MiD kernwo (1,2,3) = Mo-Fr. Measured difference of MiD trip-class shares Mo-Fr
   vs Di-Do: <= 0.63pp per class (2026-07-08, P_GEW-weighted) -- immaterial, no
   correction applied.
2. DECISION (level anchoring): SrV and MiD measure mobility rates differently
   (uniform ~+5..+8pp immobile-share offset across ALL Kreise, method not region).
   This control DELIBERATELY anchors the synthetic trip-class distribution to the
   SrV level per user decision (regional survey = regional behaviour authority).
   The offset magnitude is recorded; consumers of MiD-anchored trip statistics
   must be aware totals shift accordingly.
3. ASSUMPTION (Wolfsburg): 03103 is not covered by SrV; its row uses the SrV
   region total (same convention as target2026_has_ebike).

Output (committed): eqasim-data/data/braunschweig/targets/target2026_trip_class_by_kreis.csv
with columns ars5,source,n_effective,trips_0,trips_1_2,trips_3_4,trips_5plus
(fractions, rows = 8 Kreise + Gesamt). This is a FINAL target: the
kreis_attribute_control registry must consume it with prior_n = 0.

Usage:
    python scripts/build_trip_class_target.py [--data <eqasim-data/data/braunschweig>] [--out-dir <targets dir>]
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
log = logging.getLogger("build_trip_class_target")

TRIP_CLASS_COLUMNS = ("trips_0", "trips_1_2", "trips_3_4", "trips_5plus")

# Wolfsburg (kreisfreie Stadt) is not covered by the SrV 2023 Braunschweig+RGB
# survey; per the documented ASSUMPTION above, its row uses the SrV region total
# (identical convention to target2026_has_ebike_by_kreis.csv).
WOLFSBURG_ARS5 = "03103"

HEADER = """\
# SrV 2023 trip-class per-Kreis control target, built by
# scripts/build_trip_class_target.py from the COMMITTED SrV aggregate
# (eqasim-data/data/braunschweig/srv/srv2023_trip_classes_by_kreis.csv; NO raw
# microdata). Classes: 0 / 1-2 / 3-4 / 5+ trips on the reporting day (SrV
# E_ANZ_WEGE; matched on the MiD side by attributes.map_trip_class on anzwege1).
# Shares are the SrV trips_* columns renormalised to sum to 1 over the four
# classes (share_trips_invalid dropped).
#
# ASSUMPTION (universe): target = SrV Di-Do mittlerer Werktag; seed universe =
# MiD kernwo (1,2,3) = Mo-Fr. Measured difference of MiD trip-class shares
# Mo-Fr vs Di-Do: <= 0.63pp per class (2026-07-08, P_GEW-weighted) --
# immaterial, no correction applied.
#
# DECISION (level anchoring): SrV and MiD measure mobility rates differently
# (uniform ~+5..+8pp immobile-share offset across ALL Kreise, method not
# region). This control DELIBERATELY anchors the synthetic trip-class
# distribution to the SrV level per user decision (regional survey = regional
# behaviour authority). The offset magnitude is recorded; consumers of
# MiD-anchored trip statistics must be aware totals shift accordingly.
#
# ASSUMPTION (Wolfsburg): 03103 is not covered by SrV; its row uses the SrV
# region total (same convention as target2026_has_ebike).
#
# CONSUMER NOTE: FINAL target - use with kreis_attribute_control prior_n = 0.
"""


def read_srv_source(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"build_trip_class_target: required committed input missing: {path}")
    return pd.read_csv(path, comment="#", dtype={"code": str})


def _renormalise(df: pd.DataFrame) -> pd.DataFrame:
    """Renormalise the four ``trips_*`` share columns to sum to 1 per row.

    ``share_trips_invalid`` is intentionally dropped: it is not a trip-class
    category, and the four remaining classes must be a complete partition of the
    "computable trip count" universe used by the popsim control.
    """
    out = df.copy()
    totals = out[list(TRIP_CLASS_COLUMNS)].sum(axis=1)
    if (totals <= 0).any():
        bad = out.loc[totals <= 0, "code"].tolist()
        raise ValueError(
            f"build_trip_class_target: non-positive trips_* row total for code(s) {bad}; cannot renormalise.")
    for col in TRIP_CLASS_COLUMNS:
        out[col] = out[col] / totals
    return out


def build_trip_class_target(data: Path) -> pd.DataFrame:
    """Build the trip_class target frame (ars5, source, n_effective, trips_*) from
    the committed SrV aggregate. Fails fast if the region-total row or any Kreis
    row is missing (no under-constrained control)."""
    src = read_srv_source(data / "srv" / "srv2023_trip_classes_by_kreis.csv")

    total_rows = src[src["level"] == "total"]
    if len(total_rows) != 1:
        raise ValueError(
            f"build_trip_class_target: expected exactly one region-total row (level == 'total'), "
            f"found {len(total_rows)}.")
    total_row = _renormalise(total_rows).iloc[0]

    kreis_rows = src[src["level"] == "kreis"].copy()
    if kreis_rows.empty:
        raise ValueError("build_trip_class_target: no Kreis rows (level == 'kreis') in the SrV source.")
    kreis_rows = _renormalise(kreis_rows)
    # The SrV code column carries 5-digit ARS codes with a leading zero (e.g. "03101");
    # zfill guards against upstream loss of the leading zero (e.g. int coercion).
    kreis_rows["code"] = kreis_rows["code"].str.zfill(5)

    rows = []
    for _, r in kreis_rows.iterrows():
        rows.append({
            "ars5": r["code"], "source": "srv", "n_effective": int(r["n_unweighted"]),
            **{c: float(r[c]) for c in TRIP_CLASS_COLUMNS},
        })
    rows.append({
        "ars5": WOLFSBURG_ARS5, "source": "srv_region_total_assumption",
        "n_effective": int(total_row["n_unweighted"]),
        **{c: float(total_row[c]) for c in TRIP_CLASS_COLUMNS},
    })
    rows.append({
        "ars5": "Gesamt", "source": "srv", "n_effective": int(total_row["n_unweighted"]),
        **{c: float(total_row[c]) for c in TRIP_CLASS_COLUMNS},
    })
    return pd.DataFrame(rows, columns=["ars5", "source", "n_effective", *TRIP_CLASS_COLUMNS])


def write_target(df: pd.DataFrame, out_path: Path) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    rounded = df.copy()
    rounded[list(TRIP_CLASS_COLUMNS)] = rounded[list(TRIP_CLASS_COLUMNS)].round(4)
    with open(out_path, "w", encoding="utf-8", newline="") as f:
        f.write(HEADER)
        rounded.to_csv(f, index=False)
    log.info("wrote %s (%d rows; sources: %s)", out_path, len(df), df["source"].value_counts().to_dict())


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DATA_DEFAULT)
    parser.add_argument("--out-dir", type=Path, default=DATA_DEFAULT / "targets")
    args = parser.parse_args(argv)
    target = build_trip_class_target(args.data)
    write_target(target, args.out_dir / "target2026_trip_class_by_kreis.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
