"""Run the full Braunschweig analysis suite for one eqasim run.

Wrapper that chains the two analysis tools currently available in this
repo so a single command produces every artefact for a given run:

  1. ``braunschweig.analysis.dashboard.build_dashboard`` — rebuilds the
     interactive dashboard with the new run added (writes into
     ``braunschweig/analysis/dashboard/runs/<run_id>/``).
  2. ``braunschweig.analysis.run_mid_validation`` — writes per-table
     CSVs, PNGs, ``report.json`` and ``summary.md`` into
     ``<output-dir>/analysis/mid_validation/`` (or the path given via
     ``--analysis-out``).
  3. ``braunschweig.analysis.population_validation.run_population_validation``
     — runs the PopulationSim-style control validation and geo export
     (default: on; disable with ``--no-population-validation``).

Usage (PowerShell, conda env `eqasim` activated):

    python -m braunschweig.analysis.run_full_analysis `
        --output-dir eqasim-data/output_bs_25pct_parking `
        --sim-cache  eqasim-data/cache_bs_25pct_parking `
        --label      "25pct_parking"

Pass ``--skip-dashboard`` to only refresh the MiD-validation outputs,
or ``--skip-mid`` to only rebuild the dashboard.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from braunschweig.analysis import modestats_inside as _modestats_inside
from braunschweig.analysis import run_mid_validation as _mid
from braunschweig.analysis.dashboard import build_dashboard as _dash

LOGGER = logging.getLogger("braunschweig.analysis.full")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--sim-cache", required=False, default=None)
    ap.add_argument("--prefix", required=False, default=None)
    ap.add_argument("--analysis-out", required=False, default=None)
    ap.add_argument("--label", required=False, default=None)
    ap.add_argument("--sample-rate", required=False, type=float, default=None)
    ap.add_argument("--notes", required=False, default="")
    ap.add_argument("--skip-dashboard", action="store_true")
    ap.add_argument("--skip-mid", action="store_true")
    ap.add_argument(
        "--population-validation",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Run the PopulationSim-style control validation + geo export as part "
             "of the default analysis output (default: on).",
    )
    ns = ap.parse_args(argv)
    if not ns.skip_dashboard and ns.sim_cache is None:
        ap.error("--sim-cache is required unless --skip-dashboard is set")
    return ns


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    ns = _parse_args(argv)

    output_dir = Path(ns.output_dir).resolve()

    if not ns.skip_dashboard:
        LOGGER.info("Building dashboard for %s", output_dir)
        dash_argv = [
            "--output-dir",
            str(output_dir),
            "--sim-cache",
            str(Path(ns.sim_cache).resolve()),
        ]
        if ns.label:
            dash_argv += ["--label", ns.label]
        if ns.notes:
            dash_argv += ["--notes", ns.notes]
        if ns.sample_rate is not None:
            dash_argv += ["--sample-rate", str(ns.sample_rate)]
        # build_dashboard.main has no argv parameter; we patch sys.argv.
        old_argv = sys.argv[:]
        sys.argv = ["build_dashboard"] + dash_argv
        try:
            _dash.main()
        finally:
            sys.argv = old_argv

    # Outside-free modestats artefact (modestats_inside.csv + .png) next to the
    # native MATSim modestats. Best-effort: a missing modestats.csv (e.g. a
    # synthesis-only run) must not fail the analysis suite.
    if ns.sim_cache is not None:
        sim_output = Path(ns.sim_cache).resolve() / "simulation_output"
        try:
            csv_path, png_path = _modestats_inside.write_modestats_inside(sim_output)
            LOGGER.info("Wrote outside-free modestats: %s, %s", csv_path, png_path)
        except FileNotFoundError:
            LOGGER.info("No modestats.csv under %s; skipping outside-free modestats.", sim_output)

    if not ns.skip_mid:
        LOGGER.info("Running MiD validation for %s", output_dir)
        mid_argv = ["--output-dir", str(output_dir)]
        if ns.prefix:
            mid_argv += ["--prefix", ns.prefix]
        if ns.analysis_out:
            mid_argv += ["--analysis-out", str(Path(ns.analysis_out).resolve())]
        if ns.label:
            mid_argv += ["--label", ns.label]
        _mid.main(mid_argv)

    if ns.population_validation:
        # Local import so the (heavy) geopandas/control-validation dependency
        # is only loaded when the validation actually runs.
        from braunschweig.analysis.population_validation import (
            run_population_validation as _pop_validation,
        )

        LOGGER.info("Running population validation (default analysis output)")
        pop_argv = ["--run-output-dir", str(output_dir)]
        if ns.label:
            pop_argv += ["--label", ns.label]
        if ns.prefix:
            pop_argv += ["--prefix", ns.prefix]
        _pop_validation.run(_pop_validation._parse_args(pop_argv))

    LOGGER.info("Full analysis complete for %s", output_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
