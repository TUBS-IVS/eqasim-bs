"""
Build the SrV 2023 per-Kreis trip-participation control target
(target2026_<purpose>_participation_by_kreis.csv) from the committed SrV
aggregate table (Task 2 of feature #224, following Task 1's
srv2023_participation_by_kreis.csv aggregate).

Reads ONLY the committed SrV aggregate (no raw microdata):
    eqasim-data/data/braunschweig/srv/srv2023_participation_by_kreis.csv

Purposes: work, education, leisure, escort (SrV E_ZWECK_9 groupings, see the
aggregate's own header). This module is parametric by purpose; Task 2 generated the
`work` target, Task 5 reused the same script for `leisure` and `education`, and
issue #227 for `escort` (E_ZWECK_9 == 6, Holen/Bringen).

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
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.analysis import spatial  # noqa: E402
from braunschweig.calibration.srv_participation_universe import (  # noqa: E402
    require_unique_kreis_codes, validated_kreis_counts)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
log = logging.getLogger("build_participation_target")

PURPOSES = ("work", "leisure", "education", "escort")

# Wolfsburg (kreisfreie Stadt) is not covered by the SrV 2023 Braunschweig+RGB
# survey; per the documented ASSUMPTION above, its row uses the SrV region total
# directly (identical convention to target2026_has_ebike_by_kreis.csv; unlike
# the trip_class target, no MiD pattern transfer is applied for participation).
# The code is still named because the header text and the Gesamt row refer to it, but the
# builder no longer USES it to decide which Kreis needs the substitution -- that is read from
# the source's zero rows (issue #405).
WOLFSBURG_ARS5 = "03103"

# All EIGHT Kreis rows are expected in the SrV source since issue #405; derived from
# spatial.ZGB8 rather than re-listed, so the canonical set cannot drift out of sync.
_EXPECTED_SRV_KREIS_CODES = frozenset(spatial.ZGB8)

# Above this share of Kreise on the region-total fallback the substitution stops being a
# documented exception for one unsurveyed Kreis and starts being a broken input.
_REGION_TOTAL_FALLBACK_WARN_SHARE = 0.5

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

    # A set-based completeness check alone accepts a DUPLICATED Kreis row: it would be emitted
    # twice and reach the written target as an ambiguous ars5 key instead of failing the source
    # contract, so uniqueness is checked before the row set is split.
    require_unique_kreis_codes(kreis_rows, "build_participation_target")
    missing_codes = sorted(_EXPECTED_SRV_KREIS_CODES - set(kreis_rows["code"]))
    if missing_codes:
        raise ValueError(
            f"build_participation_target: SrV source is missing kreis row(s) for {missing_codes} "
            f"(expected all 8 ZGB Kreise, spatial.ZGB8 -- a Kreis SrV does not survey is a zero "
            f"row, not an absent one); present: {sorted(set(kreis_rows['code']))}.")
    unexpected_codes = sorted(set(kreis_rows["code"]) - _EXPECTED_SRV_KREIS_CODES)
    if unexpected_codes:
        raise ValueError(
            f"build_participation_target: SrV source has unexpected kreis row code(s) "
            f"{unexpected_codes}, not in the expected set {sorted(_EXPECTED_SRV_KREIS_CODES)}.")

    # Which Kreise have their own measurable share is a question about the DATA: a Kreis the
    # survey does not cover is a zero row with a NaN share (issue #405). Reading it here removes
    # the hardcoded "Wolfsburg is the exception" that the row convention exists to make
    # unnecessary; the substitution itself is unchanged.
    # Only an explicit zero means "not surveyed": a count that cannot be read is a broken source,
    # not an empty Kreis, and must never be coerced into one (it would silently route a corrupted
    # Kreis onto the region-total fallback below).
    kreis_rows["n_unweighted"] = validated_kreis_counts(kreis_rows, "build_participation_target")
    measurable = kreis_rows["n_unweighted"] > 0
    measured, empty_codes = kreis_rows[measurable], sorted(kreis_rows.loc[~measurable, "code"])
    n_kreis = len(kreis_rows)
    fallback_share = len(empty_codes) / n_kreis if n_kreis else 0.0
    message = ("build_participation_target[%s]: own SrV share for %d/%d Kreise (%.1f%%), "
               "region-total (03ZGB) fallback for %d (%.1f%%): %s")
    arguments = (purpose, len(measured), n_kreis, 100.0 * (1.0 - fallback_share),
                 len(empty_codes), 100.0 * fallback_share, empty_codes or "none")
    # CLAUDE.md fallback transparency: the substitution used to be invisible outside the written
    # header, so a source that lost several Kreise would have produced a target built mostly from
    # one pooled rate without saying so anywhere.
    if fallback_share > _REGION_TOTAL_FALLBACK_WARN_SHARE:
        log.warning(message + " -- most Kreise carry no own share; check the SrV source before "
                    "trusting this target", *arguments)
    else:
        log.info(message, *arguments)

    rows = []
    for _, r in measured.iterrows():
        yes = float(r[purpose])
        rows.append({
            "ars5": r["code"], "source": "srv", "n_effective": int(r["n_unweighted"]),
            yes_col: yes, no_col: 1.0 - yes,
        })

    # A Kreis without an own share (today: Wolfsburg) takes the SrV region total directly -- no
    # MiD pattern transfer; that transfer is specific to trip_class immobility, not participation.
    total_yes = float(total_row[purpose])
    total_n = int(total_row["n_unweighted"])
    for ars5 in empty_codes:
        rows.append({
            "ars5": ars5, "source": "srv_region_total", "n_effective": total_n,
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
