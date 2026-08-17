"""CLI entry point: build all metrics, plots, HTML, and JSON.

Usage:
    python -m scripts.validate_bs_10pct [--out PATH]
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

from . import plots, report
from .config import OUTPUT_DIR


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Build and run the CLI argument parser (split out for testability)."""
    parser = argparse.ArgumentParser(description="BS 10 % validation report")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output directory (default: <output_bs_10pct>/validation)")
    # Issue #256: the validation report is post-hoc -- it only reads output
    # CSV/XML files and cannot tell from those alone whether the synthesis
    # was built with escort_passive_education ON (model escort purpose =
    # active-only, passive leg folded into education) or OFF (escort purpose
    # covers both sides of Begleitung). The two modes need different MiD W1
    # scored baselines (report.ACTIVE_BASELINE_LABEL / section 5.3 vs 5.3b),
    # so the mode must be DECLARED by the operator instead of guessed.
    # Default False keeps every existing call site byte-identical.
    parser.add_argument("--escort-passive-education", dest="escort_passive_education",
                        action="store_true", default=False,
                        help=(
                            "Declare that the synthetic population was built with "
                            "escort_passive_education ON (issue #256): the report then "
                            "scores against the active-adjusted MiD W1 baseline "
                            "(section 5.3b) instead of the raw both-sides Begleitung "
                            "baseline (section 5.3). This cannot be inferred from the "
                            "output files and must be declared explicitly. Default: "
                            "False (raw baseline scored)."
                        ))
    return parser.parse_args(argv)


def main() -> int:
    args = _parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("validate_bs_10pct")

    out_dir = args.out or (OUTPUT_DIR / "validation")
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("Writing report to %s", out_dir)

    log.info("Rendering plots …")
    plot_files = plots.render_all(out_dir)
    log.info("Rendered %d plots", len(plot_files))

    log.info("Building HTML …")
    html_path = report.build_report(plot_files, out_dir,
                                     escort_passive_education=args.escort_passive_education)
    log.info("HTML written: %s", html_path)

    log.info("Writing JSON summary …")
    json_path = report.write_json_summary(out_dir,
                                          escort_passive_education=args.escort_passive_education)
    log.info("JSON written: %s", json_path)

    print()
    print(f"  Open: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
