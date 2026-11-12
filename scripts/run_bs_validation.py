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
    python -m scripts.run_bs_validation --rate {1|10|25|100} [--layout {auto|canonical|legacy}] [--out PATH]
"""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path


# Rates with a canonical overlay in configs/overlays/ (test_1pct.yml, test_25pct.yml,
# test_100pct.yml): those write output_bs_<rate>pct_allfeat_popsim,
# cache_bs_<rate>pct_allfeat_popsim and the file prefix
# braunschweig_<rate>pct_allfeat_popsim_. The 10 % rate has no overlay; it keeps the
# legacy layout that scripts.validate_bs_10pct was written for.
CANONICAL_RATES = (1, 25, 100)
# "auto" picks the canonical layout for CANONICAL_RATES and the legacy one otherwise;
# "legacy" reads a run of the legacy fixtures (configs/fixtures/config_local_braunschweig_*pct.yml,
# output_bs_<rate>pct); "canonical" requires an overlay rate.
LAYOUTS = ("auto", "canonical", "legacy")


def rate_layout(rate_pct: int, repo: Path, layout: str = "auto") -> tuple[Path, Path, str]:
    """Return (output dir, cache dir, file prefix) the run of ``rate_pct`` % writes."""
    if layout not in LAYOUTS:
        raise ValueError(f"unknown layout {layout!r}; expected one of {LAYOUTS}")
    if layout == "canonical" and rate_pct not in CANONICAL_RATES:
        raise ValueError(
            f"rate {rate_pct}% has no canonical overlay in configs/overlays/; "
            f"canonical rates are {CANONICAL_RATES}, use --layout legacy"
        )
    if layout != "legacy" and rate_pct in CANONICAL_RATES:
        suffix = f"{rate_pct}pct_allfeat_popsim"
        return (repo / "eqasim-data" / f"output_bs_{suffix}",
                repo / "eqasim-data" / f"cache_bs_{suffix}",
                f"braunschweig_{suffix}_")
    return (repo / "eqasim-data" / f"output_bs_{rate_pct}pct",
            repo / "eqasim-data" / f"cache_bs_{rate_pct}pct",
            f"braunschweig_{rate_pct}pct_")


def _patch_config(rate_pct: int, layout: str = "auto") -> Path:
    # Import the config module first; submodules MUST NOT yet be imported.
    from scripts.validate_bs_10pct import config

    rate = rate_pct / 100.0
    out_dir, cache_dir, prefix = rate_layout(rate_pct, config.REPO, layout)

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
    parser.add_argument("--rate", type=int, choices=[1, 10, 25, 100], required=True)
    parser.add_argument("--out", type=Path, default=None,
                        help="Output directory (default: <output>/validation)")
    parser.add_argument("--layout", choices=LAYOUTS, default="auto",
                        help="Run layout to read: 'auto' (canonical overlay layout for "
                             "1/25/100 %%, legacy for 10 %%), 'canonical' or 'legacy' "
                             "(a run of configs/fixtures/config_local_braunschweig_*pct.yml)")
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

    out = _patch_config(args.rate, args.layout)
    log.info("Running validation for rate=%d%% (layout %s) on %s", args.rate, args.layout, out)

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
