"""Report the MiD-implied reporting-day commute-distance bands, beside the model's own.

Maintainer decision of 2026-09-06 on ADR-0104 check 2 (issue #244): the pre-registered
``100_plus < 3 %`` bound on the reporting-day inter-Gemeinde work-distance band is WITHDRAWN,
because every ``100_plus`` column of the committed SrV reference
(``srv2023_commute_distance_by_kreis.csv``) is exactly 0.0 -- the survey records no work trip
at or above 100 km at all, a structural blind spot recorded in ADR-0102 Assumption 2. With no
SrV reference for the band, check 2's long band is instead judged by CONSISTENCY with MiD: this
script converts the register-anchored ASSIGNED commute-distance distribution
(``off/commute_by_kreis.csv``, flag OFF, before any re-draw) into what MiD's own reporting-day
"at workplace" probabilities, applied class by class, would imply for the reporting day, and
prints it beside the model's own realised ON reporting-day distribution
(``on/commute_by_kreis.csv``).

Formula, for each of the seven work-distance bands ``b`` of ``commute_by_kreis.csv``
(``0_5, 5_10, 10_20, 20_30, 30_50, 50_100, 100_plus``):

    unnormalised(b) = assigned_share_off(b) * mid_share_at_workplace(mid_class(b))
    mid_implied_reporting_day_share(b) = unnormalised(b) / sum_b' unnormalised(b')

``mid_share_at_workplace`` is read BY COLUMN NAME from the committed MiD reporting-day table
(never typed from a report), the same column ADR-0104's own keep-probability rule reads. The
division renormalises over the seven bands so the result is again a probability distribution:
without it the result would already be biased low, because MiD's "at workplace" probability is
itself less than one in every class (some MiD respondents in every class stayed home or did not
work that day) -- the renormalisation asks "of the workers whose day survives an MiD-shaped
at-workplace filter, how are they distributed across distance, versus how the model actually
distributes its own reporting-day travellers".

Band -> MiD class mapping (upper edge of the band decides the class): ``0_5``, ``5_10`` ->
``lt10``; ``10_20`` -> ``10_25``; ``20_30``, ``30_50`` -> ``25_50``; ``50_100`` -> ``50_100``;
``100_plus`` -> ``100_200``. MiD HAS NO CLASS BEYOND ``100_200`` (``P_ARB_ENTF`` top-codes at
200 km, stated in the table's own header) -- the model's ``100_plus`` band is therefore compared
against MiD's ``100_200`` class as the best available reference, not an exact match; this is why
the comparison is reported as a READING, not a precise reference figure.

Outputs ``mid_implied_reporting_day_bands.csv`` (columns ``band, assigned_share_off,
mid_share_at_workplace, mid_implied_reporting_day_share, model_on_reporting_day_share``) with a
provenance header into ``--out-dir``, and prints the same table to stdout.

Usage (from the repository/worktree root, conda env eqasim):
    python scripts/report_mid_implied_reporting_day_bands.py \\
        --off-csv eqasim-data/data/braunschweig/calibration/commute_day_state_phase_b_proof_100pct_2026-09-06_rerun/off/commute_by_kreis.csv \\
        --on-csv eqasim-data/data/braunschweig/calibration/commute_day_state_phase_b_proof_100pct_2026-09-06_rerun/on/commute_by_kreis.csv \\
        --mid-csv eqasim-data/data/braunschweig/mid/mid2023_workday_location_by_commute_distance.csv \\
        --out-dir eqasim-data/data/braunschweig/calibration/commute_day_state_phase_b_proof_100pct_2026-09-06_rerun \\
        --source-commit <short sha of this checkout>

This script only reads committed aggregates and writes one committed aggregate; it decides
nothing on its own -- the withdrawal of the 3 % bound and the adoption of MiD consistency as the
reference are recorded in ADR-0104 ("Decision 2026-09-06 on check 2 (maintainer)"), not here.
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path
from typing import Mapping

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("report_mid_implied_reporting_day_bands")

#: The seven work-distance bands carried by commute_by_kreis.csv's model_share_* columns, in
#: increasing-distance order.
BAND_ORDER = ("0_5", "5_10", "10_20", "20_30", "30_50", "50_100", "100_plus")

#: Maps each band (named by its upper edge in km) to the MiD reporting-day distance class whose
#: share_at_workplace value is applied to it. MiD has no class beyond 100_200 (P_ARB_ENTF
#: top-codes at 200 km -- see the table's own header): the 100_plus band, which the model itself
#: does NOT top-code, is therefore read against MiD's 100_200 class as the best available
#: reference, not an exact match.
BAND_TO_MID_CLASS: Mapping[str, str] = {
    "0_5": "lt10",
    "5_10": "lt10",
    "10_20": "10_25",
    "20_30": "25_50",
    "30_50": "25_50",
    "50_100": "50_100",
    "100_plus": "100_200",
}

#: Every MiD distance class the mapping above reads; the input table must carry all of them.
REQUIRED_MID_CLASSES = ("lt10", "10_25", "25_50", "50_100", "100_200")

OUTPUT_FILE = "mid_implied_reporting_day_bands.csv"

#: A renormalised distribution over the seven bands must sum to (approximately) one; anything
#: further off than this points at a corrupt or partial input row rather than floating-point noise.
SUM_TOLERANCE = 1e-6


def compute_mid_implied_shares(assigned_share_off: Mapping[str, float],
                               mid_share_at_workplace: Mapping[str, float]) -> dict[str, float]:
    """Convert an ASSIGNED (register-anchored) band distribution into a MiD-implied reporting-day one.

    ``assigned_share_off`` and ``mid_share_at_workplace`` must carry a value for every band in
    :data:`BAND_ORDER` and every MiD class in :data:`REQUIRED_MID_CLASSES` respectively (the
    caller validates completeness before calling this). Returns one share per band in
    :data:`BAND_ORDER`, renormalised so the seven values sum to 1.0 -- see the module docstring
    for the formula and why the renormalisation is needed.
    """
    unnormalised = {
        band: float(assigned_share_off[band]) * float(mid_share_at_workplace[BAND_TO_MID_CLASS[band]])
        for band in BAND_ORDER
    }
    total = sum(unnormalised.values())
    if total <= 0.0:
        raise ValueError(
            "compute_mid_implied_shares: the unnormalised MiD-implied shares sum to "
            f"{total} (<= 0); check that assigned_share_off is not all zero")
    return {band: value / total for band, value in unnormalised.items()}


def _read_commute_by_kreis(path: Path, code: str, scope: str) -> dict[str, float]:
    """Read one (code, scope) row of a commute_by_kreis.csv and return its seven band shares."""
    if not path.exists():
        raise FileNotFoundError(f"Input file missing: {path}")
    frame = pd.read_csv(path)
    required_columns = ["code", "scope"] + [f"model_share_{band}" for band in BAND_ORDER]
    missing_columns = [column for column in required_columns if column not in frame.columns]
    if missing_columns:
        raise ValueError(f"{path} is missing required column(s) {missing_columns}; available: "
                         f"{list(frame.columns)}")
    matches = frame[(frame["code"] == code) & (frame["scope"] == scope)]
    if len(matches) != 1:
        raise ValueError(f"{path}: expected exactly one row with code={code!r} scope={scope!r}, "
                         f"found {len(matches)}")
    row = matches.iloc[0]
    shares = {band: float(row[f"model_share_{band}"]) for band in BAND_ORDER}
    total = sum(shares.values())
    if abs(total - 1.0) > SUM_TOLERANCE:
        raise ValueError(f"{path}: model_share_* columns for code={code!r} scope={scope!r} sum to "
                         f"{total}, expected 1.0 (+/- {SUM_TOLERANCE}) -- the row may be corrupt or "
                         "partial")
    return shares


def _read_mid_share_at_workplace(path: Path) -> dict[str, float]:
    """Read the share_at_workplace column of the committed MiD reporting-day table, by class."""
    if not path.exists():
        raise FileNotFoundError(f"Input file missing: {path}")
    frame = pd.read_csv(path, comment="#")
    required_columns = ["distance_class", "share_at_workplace"]
    missing_columns = [column for column in required_columns if column not in frame.columns]
    if missing_columns:
        raise ValueError(f"{path} is missing required column(s) {missing_columns}; available: "
                         f"{list(frame.columns)}")
    by_class = frame.set_index("distance_class")["share_at_workplace"]
    missing_classes = [mid_class for mid_class in REQUIRED_MID_CLASSES if mid_class not in by_class.index]
    if missing_classes:
        raise ValueError(f"{path} is missing required distance_class row(s) {missing_classes}")
    values = {mid_class: float(by_class[mid_class]) for mid_class in REQUIRED_MID_CLASSES}
    invalid = {mid_class: value for mid_class, value in values.items()
              if not (0.0 <= value <= 1.0)}
    if invalid:
        raise ValueError(f"{path}: share_at_workplace out of the valid [0, 1] range: {invalid}")
    return values


def _header_lines(args: argparse.Namespace) -> list[str]:
    return [
        f"# Generated by scripts/report_mid_implied_reporting_day_bands.py on "
        f"{dt.date.today().isoformat()}.",
        f"# Code state: eqasim-bs {args.source_commit}.",
        "# Purpose: maintainer decision of 2026-09-06 on ADR-0104 check 2 (issue #244) -- the",
        "#   pre-registered 100_plus < 3% bound is WITHDRAWN because every 100_plus column of the",
        "#   committed SrV reference is exactly 0.0 (a structural survey blind spot, ADR-0102",
        "#   Assumption 2); the long band is instead judged by CONSISTENCY with MiD. This table is",
        "#   the evidence for that judgement, not a validation against observed behaviour.",
        f"# Inputs: assigned-workplace view (flag OFF, code={args.code!r} scope={args.scope!r} row) "
        f"{args.off_csv}",
        f"#   reporting-day model view (flag ON, same row) {args.on_csv}",
        f"#   MiD reporting-day work-location shares by commute-distance class {args.mid_csv}",
        "# Formula: unnormalised(b) = assigned_share_off(b) * mid_share_at_workplace(mid_class(b));",
        "#   mid_implied_reporting_day_share(b) = unnormalised(b) / sum_b' unnormalised(b'),",
        "#   renormalised over the seven bands below. mid_share_at_workplace is read BY COLUMN NAME",
        "#   from the MiD table, the same column ADR-0104's keep-probability rule reads.",
        "# Band -> MiD class mapping (by upper edge, km): 0_5, 5_10 -> lt10; 10_20 -> 10_25; 20_30,",
        "#   30_50 -> 25_50; 50_100 -> 50_100; 100_plus -> 100_200. MiD HAS NO CLASS BEYOND 100_200",
        "#   (P_ARB_ENTF top-codes at 200 km); the 100_plus band is therefore a READING against",
        "#   MiD's 100_200 class, not an exact reference.",
        "# Columns: band, assigned_share_off (the register-anchored ASSIGNED distribution before",
        "#   any commute-day-state re-draw), mid_share_at_workplace (the MiD class this band reads),",
        "#   mid_implied_reporting_day_share (the formula above), model_on_reporting_day_share (the",
        "#   model's own realised reporting-day share for this band, flag ON, read directly, no",
        "#   computation).",
    ]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--off-csv", required=True,
                        help="commute_by_kreis.csv of the flag-OFF (assigned-workplace) run")
    parser.add_argument("--on-csv", required=True,
                        help="commute_by_kreis.csv of the flag-ON (reporting-day) run")
    parser.add_argument("--mid-csv", required=True,
                        help="mid2023_workday_location_by_commute_distance.csv (committed MiD "
                             "reporting-day reference)")
    parser.add_argument("--out-dir", required=True,
                        help=f"directory {OUTPUT_FILE} is written to")
    parser.add_argument("--source-commit", required=True,
                        help="short git SHA of the eqasim-bs checkout this report corresponds to; "
                             "recorded verbatim in the output header")
    parser.add_argument("--code", default="zgb",
                        help="commute_by_kreis.csv 'code' value to read (default 'zgb', the ZGB "
                             "aggregate row)")
    parser.add_argument("--scope", default="inter",
                        help="commute_by_kreis.csv 'scope' value to read (default 'inter', the "
                             "inter-Gemeinde scope ADR-0104 check 2 is defined over)")
    args = parser.parse_args(argv)

    off_csv, on_csv, mid_csv = Path(args.off_csv), Path(args.on_csv), Path(args.mid_csv)
    assigned_share_off = _read_commute_by_kreis(off_csv, args.code, args.scope)
    model_on = _read_commute_by_kreis(on_csv, args.code, args.scope)
    mid_share_at_workplace = _read_mid_share_at_workplace(mid_csv)
    logger.info("read %s (code=%s scope=%s), %s (code=%s scope=%s) and %s",
               off_csv, args.code, args.scope, on_csv, args.code, args.scope, mid_csv)

    mid_implied = compute_mid_implied_shares(assigned_share_off, mid_share_at_workplace)

    table = pd.DataFrame({
        "band": list(BAND_ORDER),
        "assigned_share_off": [assigned_share_off[band] for band in BAND_ORDER],
        "mid_share_at_workplace": [mid_share_at_workplace[BAND_TO_MID_CLASS[band]] for band in BAND_ORDER],
        "mid_implied_reporting_day_share": [mid_implied[band] for band in BAND_ORDER],
        "model_on_reporting_day_share": [model_on[band] for band in BAND_ORDER],
    })

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / OUTPUT_FILE
    with open(out_path, "w", encoding="utf-8", newline="") as handle:
        for line in _header_lines(args):
            handle.write(line + "\n")
        table.to_csv(handle, index=False, lineterminator="\n", float_format="%.10g")
    logger.info("wrote %s", out_path)

    print(table.to_string(index=False))

    hundred_plus = table[table["band"] == "100_plus"].iloc[0]
    logger.info("HEADLINE 100_plus: assigned_share_off=%.4f, mid_implied_reporting_day_share=%.4f, "
               "model_on_reporting_day_share=%.4f", hundred_plus["assigned_share_off"],
               hundred_plus["mid_implied_reporting_day_share"],
               hundred_plus["model_on_reporting_day_share"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
