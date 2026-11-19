"""Summary tables and the pre-registered recommendation of the method arms (issue #660, Part C, Task C2).

Reads the diagnostics written by ``run_method_arms.sh`` under ``--root`` and the verdicts written by
``compare_input_arms.py`` (Part A, ``popsim-ladder-inputs-a-2026-11-18/scripts``) under ``--comparisons``,
and writes into ``--out``:

* ``wape_matrix.csv``: pooled zone-level WAPE (sum ``abs_error`` / sum ``target_sum`` over the controls
  with ``target_sum > 0``, the estimator of ``compare_input_arms.eval_wape_vs_base``) per arm, target view,
  geography and control group, over all batches (``batch`` = ``ALL``) and per batch. Rows come from
  ``diag_<arm>/control_fit.csv`` (view ``as_run``) and ``diag_views/<arm>@<view>/control_fit.csv``.
  Groups (control-name patterns): ``age_sex`` (``^[MF]_AGE_``), ``household_size``
  (``^[0-9]_Person``), ``tenure`` (``^(EigentuemerHH|MieterHH)_``), ``building_type``
  (``^building_type_``), ``household_level`` (the three household groups together), ``all``.
* ``key_figures.csv``: per arm (as run) and batch: synthetic households, distinct seed households used,
  Kish effective n, parents not solved simultaneously (all levels), controls outside the rounding-shift
  bound (G4) and the largest shift, household conservation (largest ``abs_deviation_share``), household
  composition (share with a minor, one-adult share and the census counterparts), PopulationSim wall time
  and peak RSS (``<arm>/<batch>.time``).
* ``recommendation.json``: per tested arm, ``recommended`` = every N check met AND the household-level
  check met AND the 1 km age check met (the pre-registered rule of the record; the decision stays with
  the owner), with the three verdicts it rests on.

Usage:
  python summarize_method_arms.py --root <server root> --comparisons <dir> --out <dir> \
      --arms arm_8 arm_9 arm_10 --tested arm_9 arm_10
  ``--comparisons`` holds ``<check>_<arm>/verdict.json`` for check in ``n``, ``r_household_100m``,
  ``r_age_1km``.
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from pathlib import Path

import pandas as pd

GROUPS = {
    "age_sex": r"^[MF]_AGE_",
    "household_size": r"^[0-9]_Person",
    "tenure": r"^(EigentuemerHH|MieterHH)_",
    "building_type": r"^building_type_",
    "household_level": r"^([0-9]_Person|EigentuemerHH_|MieterHH_|building_type_)",
    "all": r".",
}
RULE_CHECKS = ("n", "r_household_100m", "r_age_1km")


def pooled_wape(rows: pd.DataFrame) -> tuple:
    """``(wape, target_sum, n_controls)`` over the rows with ``target_sum > 0``."""
    rows = rows[rows["target_sum"] > 0]
    target = float(rows["target_sum"].sum())
    wape = float(rows["abs_error"].sum()) / target if target > 0 else math.nan
    return wape, target, int(rows["control"].nunique())


def wape_rows(fit: pd.DataFrame, arm: str, view: str) -> list:
    fit = fit[fit["scope"] == "batch"]
    out = []
    for geography, at_geo in fit.groupby("geography", sort=False):
        for group, pattern in GROUPS.items():
            rows = at_geo[at_geo["control"].astype(str).str.contains(pattern, regex=True)]
            if rows.empty:
                continue
            for batch, part in [("ALL", rows), *rows.groupby("batch", sort=True)]:
                wape, target, n = pooled_wape(part)
                out.append({"arm": arm, "view": view, "geography": geography, "group": group,
                            "batch": batch, "wape": wape, "target_sum": target, "n_controls": n})
    return out


def wape_matrix(root: Path, arms) -> pd.DataFrame:
    rows = []
    for arm in arms:
        fit = root / f"diag_{arm}" / "control_fit.csv"
        if fit.is_file():
            rows += wape_rows(pd.read_csv(fit), arm, "as_run")
    views = root / "diag_views"
    for diag in sorted(views.glob("*@*")) if views.is_dir() else []:
        arm, view = diag.name.split("@", 1)
        if arm in arms and (diag / "control_fit.csv").is_file():
            rows += wape_rows(pd.read_csv(diag / "control_fit.csv"), arm, view)
    return pd.DataFrame(rows)


def _time_file(path: Path) -> dict:
    """Wall time (minutes) and peak RSS (GB) of a GNU ``time -v`` file; NaN when absent."""
    if not path.is_file():
        return {"wall_minutes": math.nan, "peak_rss_gb": math.nan}
    text = path.read_text(encoding="utf-8", errors="replace")
    wall = re.search(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): ([0-9:.]+)", text)
    rss = re.search(r"Maximum resident set size \(kbytes\): (\d+)", text)
    minutes = math.nan
    if wall:
        parts = [float(p) for p in wall.group(1).split(":")]
        seconds = sum(p * 60 ** i for i, p in enumerate(reversed(parts)))
        minutes = seconds / 60.0
    return {"wall_minutes": minutes, "peak_rss_gb": int(rss.group(1)) / 1024 ** 2 if rss else math.nan}


def key_figures(root: Path, arms) -> pd.DataFrame:
    rows = []
    for arm in arms:
        diag = root / f"diag_{arm}"
        if not (diag / "reuse.csv").is_file():
            continue
        reuse = pd.read_csv(diag / "reuse.csv")
        reuse = reuse[reuse["scope"] == "batch"].set_index("subject")
        solver = pd.read_csv(diag / "solver_paths.csv").groupby("batch")["non_simultaneous"].sum()
        shift = pd.read_csv(diag / "rounding_shift.csv")
        conservation = pd.read_csv(diag / "household_conservation.csv")
        composition = pd.read_csv(diag / "household_composition.csv").set_index("batch")
        for batch in reuse.index:
            s = shift[shift["batch"] == batch]
            c = conservation[conservation["batch"] == batch]
            comp = composition.loc[batch] if batch in composition.index else None
            rows.append({
                "arm": arm, "batch": batch,
                "synthetic_households": int(reuse.at[batch, "n_synthetic_households"]),
                "distinct_seed_households": int(reuse.at[batch, "n_distinct_used"]),
                "kish_n_eff": float(reuse.at[batch, "kish_n_eff"]),
                "non_simultaneous_parents": int(solver.get(batch, 0)),
                "g4_controls_outside": int((~s["within_bound"].astype(bool)).sum()),
                "g4_max_shift": float(s["shift"].max()) if len(s) else math.nan,
                "conservation_max_abs_deviation_share": float(c["abs_deviation_share"].max()) if len(c) else math.nan,
                "share_households_with_minor": float(comp["share_households_with_minor"]) if comp is not None else math.nan,
                "census_share_families_with_minor": float(comp["census_share_families_with_minor"]) if comp is not None else math.nan,
                "one_adult_share_among_minor_households": float(comp["one_adult_share_among_minor_households"]) if comp is not None else math.nan,
                "census_lone_parent_share": float(comp["census_lone_parent_share_among_families_with_minor"]) if comp is not None else math.nan,
                **_time_file(root / arm / f"{batch}.time"),
            })
    return pd.DataFrame(rows)


def recommendation(comparisons: Path, tested) -> dict:
    out = {}
    for arm in tested:
        verdicts, missing = {}, []
        for check in RULE_CHECKS:
            path = comparisons / f"{check}_{arm}" / "verdict.json"
            if not path.is_file():
                missing.append(str(path))
                continue
            verdict = json.loads(path.read_text(encoding="utf-8"))
            verdicts[check] = {"all_met": bool(verdict["all_met"]),
                               "checks": {k: bool(v["met"]) for k, v in verdict["checks"].items()}}
        out[arm] = {"verdicts": verdicts, "missing": missing,
                    "recommended": not missing and all(v["all_met"] for v in verdicts.values())}
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--comparisons", required=True, type=Path)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--arms", nargs="+", required=True)
    parser.add_argument("--tested", nargs="+", required=True)
    args = parser.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    matrix = wape_matrix(args.root, args.arms)
    matrix.to_csv(args.out / "wape_matrix.csv", index=False)
    figures = key_figures(args.root, args.arms)
    figures.to_csv(args.out / "key_figures.csv", index=False)
    rec = recommendation(args.comparisons, args.tested)
    (args.out / "recommendation.json").write_text(json.dumps(rec, indent=2), encoding="utf-8")
    pd.set_option("display.width", 220)
    pooled = matrix[matrix["batch"] == "ALL"].pivot_table(
        index=["geography", "group"], columns=["arm", "view"], values="wape") if len(matrix) else matrix
    print(pooled.to_string())
    print(json.dumps({a: r["recommended"] for a, r in rec.items()}, indent=2))
    return 0 if all(not r["missing"] for r in rec.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
