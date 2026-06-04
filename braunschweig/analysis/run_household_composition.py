"""Household-composition realism report (#3b).

Quantifies the age-plausibility of the synthesised households and (optionally)
compares a random-chunk run against an age-aware (optimised) run. Reads the
eqasim ``*_persons.csv`` and ``*_households.csv`` outputs and writes a CSV, a
``report.json`` entry and figures:

  - within-couple age-difference distribution (optimised should be far tighter);
  - parent-child age-gap distribution (optimised peaks near the target ~31 y);
  - share of "implausible" households (all-children, child-older-than-parent,
    couple gap > threshold) -- should drop to ~0 with the age-aware path;
  - household-type share (should still match the Zensus marginal).

Usage (PowerShell, conda env ``eqasim`` activated)::

    python -m braunschweig.analysis.run_household_composition `
        --output-dir eqasim-data/output_bs_25pct `
        --compare-dir eqasim-data/output_bs_25pct_ageaware `
        --analysis-out eqasim-data/output_bs_25pct/analysis/household_composition

The pure diagnostics (``couple_age_gaps`` etc.) are unit-tested; the full
``run`` is exercised manually after a synthesis run.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

LOGGER = logging.getLogger("braunschweig.analysis.household_composition")

MIN_ADULT_AGE = 18
COUPLE_GAP_THRESHOLD = 20  # a childless 2-adult household with a larger gap is flagged


# ---------------------------------------------------------------------------
# Pure diagnostics
# ---------------------------------------------------------------------------

def couple_age_gaps(persons: pd.DataFrame, min_adult_age: int = MIN_ADULT_AGE) -> np.ndarray:
    """Within-couple absolute age gap for every 2-adult household."""
    gaps: list[int] = []
    for _, g in persons.groupby("household_id"):
        ages = g["age"].to_numpy()
        if len(ages) == 2 and (ages >= min_adult_age).all():
            gaps.append(abs(int(ages[0]) - int(ages[1])))
    return np.array(gaps, dtype=float)


def parent_child_gaps(persons: pd.DataFrame, min_adult_age: int = MIN_ADULT_AGE) -> np.ndarray:
    """Per child, ``youngest_adult_age - child_age`` for households with at least
    one adult and one child."""
    gaps: list[int] = []
    for _, g in persons.groupby("household_id"):
        ages = g["age"].to_numpy()
        adults = ages[ages >= min_adult_age]
        children = ages[ages < min_adult_age]
        if len(adults) >= 1 and len(children) >= 1:
            gaps.extend((adults.min() - children).tolist())
    return np.array(gaps, dtype=float)


def implausible_share(persons: pd.DataFrame, min_adult_age: int = MIN_ADULT_AGE,
                      couple_gap_threshold: int = COUPLE_GAP_THRESHOLD) -> float:
    """Share of households that are demographically implausible: no adult present
    (all-children), a child older than the youngest adult, or a childless
    2-adult household whose age gap exceeds ``couple_gap_threshold``."""
    n = 0
    bad = 0
    for _, g in persons.groupby("household_id"):
        ages = g["age"].to_numpy()
        n += 1
        adults = ages[ages >= min_adult_age]
        children = ages[ages < min_adult_age]
        is_bad = False
        if len(adults) == 0:
            is_bad = True
        elif len(children) >= 1 and children.max() > adults.min():
            is_bad = True
        elif len(ages) == 2 and len(adults) == 2 and \
                abs(int(ages[0]) - int(ages[1])) > couple_gap_threshold:
            is_bad = True
        if is_bad:
            bad += 1
    return bad / max(n, 1)


def hh_type_share(households: pd.DataFrame) -> pd.Series:
    """Normalised household-type share."""
    return households["hh_type"].value_counts(normalize=True).sort_index()


# ---------------------------------------------------------------------------
# Run pipeline
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class _Args:
    output_dir: Path
    compare_dir: Path | None
    prefix: str
    compare_prefix: str | None
    analysis_out: Path


def _detect_prefix(output_dir: Path) -> str:
    hits = list(output_dir.glob("*_persons.csv"))
    if not hits:
        raise SystemExit(f"No *_persons.csv in {output_dir}")
    return hits[0].name[: -len("persons.csv")]


def _load_persons(output_dir: Path, prefix: str) -> pd.DataFrame:
    persons = pd.read_csv(output_dir / f"{prefix}persons.csv", sep=";")
    households = pd.read_csv(output_dir / f"{prefix}households.csv", sep=";")
    cols = ["household_id"]
    if "hh_type" in households.columns:
        cols.append("hh_type")
    merged = persons[["person_id", "household_id", "age"]].merge(
        households[cols].drop_duplicates("household_id"),
        on="household_id", how="left",
    )
    return merged, households


def _diagnostics(persons: pd.DataFrame, households: pd.DataFrame) -> dict:
    cg = couple_age_gaps(persons)
    pcg = parent_child_gaps(persons)
    return {
        "n_households": int(persons["household_id"].nunique()),
        "couple_gap_mean": float(np.mean(cg)) if len(cg) else float("nan"),
        "couple_gap_median": float(np.median(cg)) if len(cg) else float("nan"),
        "parent_child_gap_mean": float(np.mean(pcg)) if len(pcg) else float("nan"),
        "parent_child_gap_median": float(np.median(pcg)) if len(pcg) else float("nan"),
        "implausible_share": implausible_share(persons),
        "hh_type_share": (hh_type_share(households).to_dict()
                          if "hh_type" in households.columns else {}),
        "_couple_gaps": cg, "_parent_child_gaps": pcg,
    }


def run(args: _Args) -> dict:
    out = args.analysis_out
    out.mkdir(parents=True, exist_ok=True)

    base_persons, base_hh = _load_persons(args.output_dir, args.prefix)
    base = _diagnostics(base_persons, base_hh)
    LOGGER.info("base run: implausible share %.3f, couple-gap mean %.1f",
                base["implausible_share"], base["couple_gap_mean"])

    runs = {"base": base}
    if args.compare_dir is not None:
        cmp_prefix = args.compare_prefix or _detect_prefix(args.compare_dir)
        cmp_persons, cmp_hh = _load_persons(args.compare_dir, cmp_prefix)
        runs["compare"] = _diagnostics(cmp_persons, cmp_hh)

    # CSV (scalar diagnostics per run).
    rows = []
    for name, d in runs.items():
        rows.append({k: v for k, v in d.items() if not k.startswith("_")
                     and k != "hh_type_share"})
        rows[-1]["run"] = name
    pd.DataFrame(rows).to_csv(out / "household_composition.csv", index=False)

    # Figures.
    _plot_hist(runs, "_couple_gaps", "within-couple age gap (years)",
               out / "01_couple_age_gap.png")
    _plot_hist(runs, "_parent_child_gaps", "parent-child age gap (years)",
               out / "02_parent_child_gap.png")
    _plot_implausible(runs, out / "03_implausible_share.png")

    report = {name: {k: v for k, v in d.items() if not k.startswith("_")}
              for name, d in runs.items()}
    (out / "report.json").write_text(
        json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    LOGGER.info("Wrote household-composition report to %s", out)
    return report


def _plot_hist(runs: dict, key: str, xlabel: str, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(7, 4))
    for name, d in runs.items():
        vals = d[key]
        if len(vals):
            ax.hist(vals, bins=30, alpha=0.5, density=True, label=name)
    ax.set_xlabel(xlabel)
    ax.set_ylabel("density")
    ax.legend()
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _plot_implausible(runs: dict, path: Path) -> None:
    fig, ax = plt.subplots(figsize=(5, 4))
    names = list(runs.keys())
    ax.bar(names, [runs[n]["implausible_share"] * 100 for n in names])
    ax.set_ylabel("implausible households (%)")
    fig.tight_layout()
    fig.savefig(path, dpi=120)
    plt.close(fig)


def _parse_args(argv: list[str] | None) -> _Args:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--compare-dir", default=None)
    ap.add_argument("--prefix", default=None)
    ap.add_argument("--compare-prefix", default=None)
    ap.add_argument("--analysis-out", default=None)
    ns = ap.parse_args(argv)

    output_dir = Path(ns.output_dir).resolve()
    if not output_dir.is_dir():
        ap.error(f"--output-dir does not exist: {output_dir}")
    prefix = ns.prefix or _detect_prefix(output_dir)
    compare_dir = Path(ns.compare_dir).resolve() if ns.compare_dir else None
    analysis_out = (Path(ns.analysis_out).resolve() if ns.analysis_out
                    else output_dir / "analysis" / "household_composition")
    return _Args(output_dir=output_dir, compare_dir=compare_dir, prefix=prefix,
                 compare_prefix=ns.compare_prefix, analysis_out=analysis_out)


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s")
    run(_parse_args(argv))
    return 0


if __name__ == "__main__":
    sys.exit(main())
