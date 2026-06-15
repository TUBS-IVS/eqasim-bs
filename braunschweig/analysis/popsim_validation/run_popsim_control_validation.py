"""CLI entry point: popsim_mid control-fit validation against census marginals.

Compares the synthesized popsim_mid population (expanded synthetic households/persons)
against the census marginals that PopulationSim was fitted to, using the same
evaluate / summarize / assess / family_scores machinery as the baseline population
validation.

Usage (PowerShell, conda env eqasim):
    $env:PYTHONUTF8=1; python -m braunschweig.analysis.popsim_validation.run_popsim_control_validation `
        --run-output-dir eqasim-data/output_popsim_25pct `
        --label popsim_25pct

Output files (in --analysis-out or <source>/analysis/popsim_validation/):
    controls_long.csv       -- per-(control, geo, category) deviations
    controls_summary.csv    -- per-(control, category) summary statistics
    quality_summary.csv     -- per-control fit measures + grade
    quality_by_family.csv   -- headline roll-up by family (backbone/hh/reference)
    summary.md              -- human-readable markdown report
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

import pandas as pd

from braunschweig.analysis import spatial
from braunschweig.analysis.population_validation import (
    control_validation as CV,
    quality_assessment as QA,
    population_source as PS,
)
from braunschweig.analysis.popsim_validation import controls as C

LOGGER = logging.getLogger("braunschweig.analysis.popsim_validation")
REPO_ROOT = spatial.REPO_ROOT
DATA_PATH = str(REPO_ROOT / "eqasim-data" / "data")


def _parse_args(argv):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--run-output-dir", default=None)
    ap.add_argument("--sim-cache", default=None)
    ap.add_argument("--prefix", default=None)
    ap.add_argument("--analysis-out", default=None)
    ap.add_argument("--label", default=None)
    ns = ap.parse_args(argv)
    if (ns.run_output_dir is None) == (ns.sim_cache is None):
        ap.error("pass exactly ONE of --run-output-dir / --sim-cache")
    return ns


def _git_commit() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=str(REPO_ROOT)
        ).decode().strip()
    except Exception:
        return "unknown"


def _interpretation_markdown(quality: pd.DataFrame) -> str:
    if quality.empty:
        return "_No controls with a target were evaluated._"
    good = quality[quality["grade"].isin(["very good", "good"])].head(10)
    bad = (quality[quality["grade"].isin(["acceptable", "needs improvement"])]
           .sort_values("mean_abs_delta_pp", ascending=False))
    lines = ["### Where are we good", ""]
    for r in good.itertuples():
        lines.append(
            f"- **{r.control}** ({r.grade}, mean |delta| "
            f"{r.mean_abs_delta_pp:.2f} pp, SRMSE {r.srmse:.3f})"
        )
    lines += ["", "### What can be improved", ""]
    for r in bad.itertuples():
        lines.append(
            f"- **{r.control}** ({r.grade}, mean |delta| "
            f"{r.mean_abs_delta_pp:.2f} pp, SRMSE {r.srmse:.3f})"
        )
    lines += ["", "### Possible causes", ""]
    for r in bad.itertuples():
        if getattr(r, "cause_hint", ""):
            lines.append(f"- **{r.control}**: {r.cause_hint}")
    return "\n".join(lines)


def run(ns) -> dict:
    frames = PS.load_population(
        run_output_dir=ns.run_output_dir, sim_cache=ns.sim_cache, prefix=ns.prefix
    )
    source_path = Path(frames.source_path)
    out = (
        Path(ns.analysis_out)
        if ns.analysis_out
        else source_path / "analysis" / "popsim_validation"
    )
    out.mkdir(parents=True, exist_ok=True)
    label = ns.label or frames.prefix.rstrip("_")

    # Build geo mapping: household_id -> ars5 / commune_id.
    kreise = spatial.load_kreise(frames.homes.crs)
    homes_geo = spatial.assign_geographies(frames.homes, kreise=kreise)
    geo = homes_geo[["household_id", "ars5", "commune_id"]].drop_duplicates("household_id")

    registry = C.build_registry(DATA_PATH)
    long = CV.evaluate_all(registry, frames, geo, DATA_PATH)
    summary = CV.summarize(long)
    quality = QA.assess(long)
    fam = QA.family_scores(quality)

    long.to_csv(out / "controls_long.csv", index=False)
    summary.to_csv(out / "controls_summary.csv", index=False)
    quality.to_csv(out / "quality_summary.csv", index=False)
    fam.to_csv(out / "quality_by_family.csv", index=False)

    report = {
        "label": label,
        "source_kind": frames.source_kind,
        "source_path": frames.source_path,
        "prefix": frames.prefix,
        "git_commit": _git_commit(),
        "n_persons": int(len(frames.persons)),
        "n_households": int(len(frames.households)),
        "family_scores": fam.to_dict(orient="records"),
        "quality": quality.to_dict(orient="records"),
    }
    (out / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    md = [
        f"# Popsim control-fit validation - {label}", "",
        f"- Source: `{frames.source_path}` ({frames.source_kind})",
        f"- Git commit: `{report['git_commit']}`",
        f"- Persons: {report['n_persons']:,} | Households: {report['n_households']:,}", "",
        "## Headline quality by family", "",
        fam.to_string(index=False) if not fam.empty else "_no targets_", "",
        "## Interpretation", "",
        _interpretation_markdown(quality), "",
    ]
    (out / "summary.md").write_text("\n".join(md), encoding="utf-8")
    LOGGER.info("Wrote popsim control-fit validation to %s", out)
    return report


def main(argv=None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
    )
    run(_parse_args(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
