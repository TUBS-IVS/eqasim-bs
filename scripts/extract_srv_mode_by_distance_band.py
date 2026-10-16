# scripts/extract_srv_mode_by_distance_band.py
"""Extract the committed SrV 2023 mode-share-by-distance-band table (eqasim-bs#453, ADR-0145).

Reads the LOCAL-ONLY SrV 2023 "Braunschweig und RGB" scientific-use trips (cp1252, semicolon, decimal
comma) and writes ONE small aggregate to ``--out-dir`` (default ``eqasim-data/data/braunschweig/srv``):

    srv2023_mode_by_distance_band.csv

Definitions live in ``braunschweig.calibration.srv_mode_by_distance_band`` (trip-level, GIS-routed length
only, GEWICHT_W_ZENSUS, E_HVM_5 modes, purpose groups work/education/other/all, bands pooled from the top
below MIN_TRIPS_PER_BAND). The table carries a provenance header.

Usage (eqasim env, from a worktree; point --raw at the main checkout's raw directory):
    python scripts/extract_srv_mode_by_distance_band.py \
        --raw C:/Users/bienzeisler/Documents/GitHub/eqasim-bs/eqasim-data/data/braunschweig/srv/srv2023_raw \
        --out-dir eqasim-data/data/braunschweig/srv
"""
from __future__ import annotations

import argparse
import datetime as dt
import logging
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from braunschweig.calibration import srv_mode_by_distance_band as M  # noqa: E402

RAW_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig" / "srv" / "srv2023_raw"
OUT_DEFAULT = REPO / "eqasim-data" / "data" / "braunschweig" / "srv"
CSV_READ_KWARGS = dict(sep=";", decimal=",", encoding="cp1252", low_memory=False)
TRIP_COLUMNS = ["HHNR", "PNR", "WNR", "V_ZWECK", "E_START_ZWECK", "E_HVM_5", "GIS_LAENGE",
                "GIS_LAENGE_GUELTIG", "GEWICHT_W_ZENSUS", "STICHTAG_WTAG"]

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger("extract_srv_mode_by_distance_band")


def _header(log: dict, detour_factor: float, min_trips: int, raw: Path) -> list[str]:
    lines = [
        f"# srv2023_mode_by_distance_band.csv -- generated {dt.date.today().isoformat()} by "
        "scripts/extract_srv_mode_by_distance_band.py (eqasim-bs#453, ADR-0145)",
        f"# Source: SrV 2023 Braunschweig + RGB SciUse trips ({raw.name}/SrV2023_Wege.csv, local only, Tue-Thu).",
        "# Unit: trip, weight GEWICHT_W_ZENSUS >= 0; distance GIS_LAENGE where GIS_LAENGE_GUELTIG > 0, straight-line",
        f"#   equivalent = routed / {detour_factor}; mode E_HVM_5 (1 walk, 2 bicycle, 3 car, 4 car_passenger, 5 pt).",
        "# Purpose group from V_ZWECK via srv_plan_structure.PURPOSE_BY_V_ZWECK (work 1-2 incl. business; education",
        "#   3-7; other 8-18, 70); trips to home (19) take the group of E_START_ZWECK; unknown purposes (-7, -10) and",
        "#   home -> home trips are excluded. Group all pools the three.",
        f"# Bands (straight-line km): {list(M.BAND_EDGES_KM)}; a band below {min_trips} unweighted trips merges into",
        "#   the band below, from the top downwards (pooled bands and thin bands kept are listed here).",
        "# Exclusions (trips): " + ", ".join(f"{key}={value}" for key, value in log.items()
                                             if key.startswith("n_")),
        "# Pooled bands: " + "; ".join(f"{group}: {bands}" for group, bands in log["pooled_bands"].items()),
        "# Thin bands kept: " + "; ".join(f"{group}: {bands}" for group, bands in log["thin_bands_kept"].items()),
        "# share = weighted mode share within (purpose_group, band); n_band / n_cell = unweighted trips.",
    ]
    return lines


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", default=str(RAW_DEFAULT), help="directory with SrV2023_Wege.csv (local only)")
    parser.add_argument("--out-dir", default=str(OUT_DEFAULT), help="directory of the committed aggregate")
    parser.add_argument("--detour-factor", type=float, default=M.DEFAULT_DETOUR_FACTOR)
    parser.add_argument("--min-trips-per-band", type=int, default=M.MIN_TRIPS_PER_BAND)
    args = parser.parse_args(argv)
    raw, out_dir = Path(args.raw), Path(args.out_dir)
    trips = pd.read_csv(raw / "SrV2023_Wege.csv", usecols=TRIP_COLUMNS, **CSV_READ_KWARGS)
    weekdays = sorted(trips["STICHTAG_WTAG"].dropna().unique().tolist())
    if not set(weekdays) <= {2, 3, 4}:
        raise ValueError(f"Expected Tue-Thu reporting days only, found STICHTAG_WTAG codes {weekdays}")
    origin_codes = sorted(pd.to_numeric(trips["E_START_ZWECK"], errors="coerce").dropna().unique().tolist())
    logger.info("E_START_ZWECK codes present: %s (the V_ZWECK code list plus -7 'war nicht zu Hause')", origin_codes)
    table, log = M.build_table(trips, detour_factor=args.detour_factor, min_trips_per_band=args.min_trips_per_band)
    for key, value in log.items():
        logger.info("%s = %s", key, value)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / M.TABLE_FILE_NAME
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write("\n".join(_header(log, args.detour_factor, args.min_trips_per_band, raw)) + "\n")
        table.to_csv(handle, index=False, float_format="%.6f")
    logger.info("wrote %s (%d rows)", path, len(table))
    return 0


if __name__ == "__main__":
    sys.exit(main())
