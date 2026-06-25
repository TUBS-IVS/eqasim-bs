"""CLI: integerizer / smart-rounding error analysis for a finished popsim work_dir.

    python -m braunschweig.analysis.run_integerizer_quality \
        --work-dir eqasim-data/popsim_work_allfeat \
        --mid-dir  eqasim-data/data/braunschweig/popsim/mid2023_raw \
        --output-dir eqasim-data/output_bs_25pct_allfeat_popsim
"""
from __future__ import annotations

import argparse
import logging

from braunschweig.analysis.integerizer_quality import cell_error, report, zone_feasibility

logger = logging.getLogger(__name__)


def _parse(argv):
    p = argparse.ArgumentParser(description="Integerizer / smart-rounding error analysis")
    p.add_argument("--work-dir", required=True)
    p.add_argument("--mid-dir", required=True)
    p.add_argument("--output-dir", required=True)
    p.add_argument("--random-seed", type=int, default=1234)
    p.add_argument("--tiers", default="tier0,tier1,tier2,tier3")
    p.add_argument("--employment-grid", dest="employment_grid", action="store_true", default=True)
    p.add_argument("--no-employment-grid", dest="employment_grid", action="store_false")
    p.add_argument("--weekend", dest="weekend", action="store_true", default=True)
    p.add_argument("--no-weekend", dest="weekend", action="store_false")
    return p.parse_args(argv)


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO)
    args = _parse(argv)
    tiers = [t.strip() for t in args.tiers.split(",") if t.strip()]
    zones = zone_feasibility.classify_zones(args.work_dir)
    error_long = cell_error.cell_error_table(
        args.work_dir, args.mid_dir, random_seed=args.random_seed, tiers=tiers,
        employment_grid=args.employment_grid, weekend=args.weekend)
    outputs = report.build_outputs(error_long, zones)
    report.write_report(outputs, args.output_dir)
    logger.info("[integerizer_quality] wrote report to %s/integerizer_quality/", args.output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
