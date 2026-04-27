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


def main() -> int:
    parser = argparse.ArgumentParser(description="BS 10 % validation report")
    parser.add_argument("--out", type=Path, default=None,
                        help="Output directory (default: <output_bs_10pct>/validation)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("validate_bs_10pct")

    out_dir = args.out or (OUTPUT_DIR / "validation")
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("Writing report to %s", out_dir)

    log.info("Rendering plots …")
    plot_files = plots.render_all(out_dir)
    log.info("Rendered %d plots", len(plot_files))

    log.info("Building HTML …")
    html_path = report.build_report(plot_files, out_dir)
    log.info("HTML written: %s", html_path)

    log.info("Writing JSON summary …")
    json_path = report.write_json_summary(out_dir)
    log.info("JSON written: %s", json_path)

    print()
    print(f"  Open: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
