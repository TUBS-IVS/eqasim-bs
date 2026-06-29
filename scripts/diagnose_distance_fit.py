"""CLI: distance-fit diagnostic on a synpp cache (work / education / secondary).

Reads the actual assigned locations from a cache and reports realised-vs-MiD
distance fit per spatial stratum. Resolves the workflow-dependent stages via the
shared stage_io alias resolution, so it runs on popsim_mid and IPF caches alike.
Run on the server (caches are server-only).
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from braunschweig.calibration import stage_io  # noqa: E402
from braunschweig.calibration.distance_fit import run as run_mod  # noqa: E402
from braunschweig.calibration.distance_fit import report as report_mod  # noqa: E402


def build_parser():
    p = argparse.ArgumentParser(description="Distance-fit diagnostic on a synpp cache.")
    p.add_argument("--working-directory", required=True)
    p.add_argument("--config", default=None)
    p.add_argument("--mid-dir", default="eqasim-data/data/braunschweig/mid")
    p.add_argument("--activity", choices=["work", "education", "secondary", "all"], default="all")
    p.add_argument("--detour-factor", type=float, default=1.3)
    p.add_argument("--output-dir", default=None)
    return p


def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    args = build_parser().parse_args()
    aliases = stage_io.load_aliases(args.config) if args.config else {}
    wd = args.working_directory

    stages = {
        "activities": stage_io.load_cached_stage(wd, "synthesis.population.activities"),
        "locations": stage_io.load_cached_stage(wd, "synthesis.population.spatial.locations"),
        "regiostar": stage_io.load_cached_stage(wd, "braunschweig.data.bbsr.regiostar"),
        "employees": stage_io.load_cached_stage(wd, "braunschweig.data.census.employees"),
        "work_locations": stage_io.load_cached_stage(wd, "braunschweig.locations.work"),
        "municipalities": stage_io.load_cached_stage(wd, "data.spatial.municipalities"),
        "enriched": stage_io.load_cached_stage(
            wd, stage_io.resolve_stage(aliases, stage_io.ENRICHED_ALIAS_KEY,
                                       stage_io.DEFAULT_ENRICHED_PRODUCER)),
    }
    activities = ["work", "education", "secondary"] if args.activity == "all" else [args.activity]
    provenance = {
        "cache": wd, "config": args.config, "git_hash": report_mod.git_hash(),
        "mid_dir": args.mid_dir,
        "scope_boundaries": [
            "synthesis-level straight-line x detour, NOT network-routed",
            "residents only (cross-cordon in-commuters + freight excluded)",
            "deployed-model snapshot (commute friction not pinned unless config pins it)",
        ],
    }
    run_mod.run_distance_fit(stages, args.mid_dir, activities=activities,
                             detour_factor=args.detour_factor,
                             output_dir=args.output_dir, provenance=provenance)


if __name__ == "__main__":
    main()
