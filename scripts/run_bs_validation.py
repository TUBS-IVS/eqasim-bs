"""Drive the validation harness against any Braunschweig sampling rate.

Reuses the package ``scripts.validate_bs_10pct`` (originally hard-coded to
10 %) by patching its ``config`` module BEFORE the submodules
(``io``, ``metrics``, ``plots``, ``report``, ``diagnostics``,
``references``) are imported. The submodules read ``OUTPUT_DIR``,
``PREFIX``, ``SAMPLING_RATE``, and ``CACHE_DIR`` at first import — see
``scripts/validate_bs_10pct/config.py`` — so this driver only works
when called as a fresh Python process (no prior import of the
submodules).

Usage:
    python -m scripts.run_bs_validation --rate {1|10|25} [--out PATH]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


def _patch_config(rate_pct: int) -> Path:
    # Import the config module first; submodules MUST NOT yet be imported.
    from scripts.validate_bs_10pct import config

    rate = rate_pct / 100.0
    repo = config.REPO

    if rate_pct == 1:
        # Default 1 % output directory matches `eqasim-data/output_bs/`
        # and `eqasim-data/cache_bs/`; the file prefix has no rate suffix.
        out_dir = repo / "eqasim-data" / "output_bs"
        cache_dir = repo / "eqasim-data" / "cache_bs"
        prefix = "braunschweig_"
    else:
        out_dir = repo / "eqasim-data" / f"output_bs_{rate_pct}pct"
        cache_dir = repo / "eqasim-data" / f"cache_bs_{rate_pct}pct"
        prefix = f"braunschweig_{rate_pct}pct_"

    if not out_dir.exists():
        raise FileNotFoundError(
            f"output directory for rate {rate_pct}% not found: {out_dir}"
        )

    config.SAMPLING_RATE = rate
    config.OUTPUT_DIR = out_dir
    config.CACHE_DIR = cache_dir
    config.PREFIX = prefix
    return out_dir


def main() -> int:
    parser = argparse.ArgumentParser(description="Run BS validation for any rate")
    parser.add_argument("--rate", type=int, choices=[1, 10, 25], required=True)
    parser.add_argument("--out", type=Path, default=None,
                        help="Output directory (default: <output>/validation)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(levelname)s] %(message)s")
    log = logging.getLogger("run_bs_validation")

    # Refuse if any of the harness submodules has already been imported in
    # this process (would have captured stale OUTPUT_DIR / PREFIX values).
    leaked = [
        name for name in (
            "scripts.validate_bs_10pct.io",
            "scripts.validate_bs_10pct.metrics",
            "scripts.validate_bs_10pct.plots",
            "scripts.validate_bs_10pct.report",
            "scripts.validate_bs_10pct.diagnostics",
            "scripts.validate_bs_10pct.references",
        ) if name in sys.modules
    ]
    if leaked:
        raise RuntimeError(
            "Run me as a fresh process; these modules were already imported "
            "and have captured the default 10 % paths: " + ", ".join(leaked)
        )

    out = _patch_config(args.rate)
    log.info("Running validation for rate=%d%% on %s", args.rate, out)

    # Now safe to import the submodules — they capture the patched config.
    from scripts.validate_bs_10pct import plots, report
    from scripts.validate_bs_10pct.config import OUTPUT_DIR

    out_dir = args.out or (OUTPUT_DIR / "validation")
    out_dir.mkdir(parents=True, exist_ok=True)
    log.info("Writing report to %s", out_dir)

    log.info("Rendering plots ...")
    plot_files = plots.render_all(out_dir)
    log.info("Rendered %d plots", len(plot_files))

    log.info("Building HTML ...")
    html_path = report.build_report(plot_files, out_dir)
    log.info("HTML written: %s", html_path)

    log.info("Writing JSON summary ...")
    json_path = report.write_json_summary(out_dir)
    log.info("JSON written: %s", json_path)

    print()
    print(f"  Open: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
