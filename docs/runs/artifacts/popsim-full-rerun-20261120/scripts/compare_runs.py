"""Side-by-side comparison of two popsim_batch_diagnostics outputs (issue #648).

Reads the output folders that ``python -m braunschweig.analysis.popsim_batch_diagnostics.run`` wrote for the
October production run (old) and the full rerun (new), both measured with the same diagnostics code, and writes
comparison tables. Nothing is re-measured here; every number comes from the two input folders.

Usage:
    python compare_runs.py OLD_DIAG_DIR NEW_DIAG_DIR OUT_DIR

Outputs (CSV, one row per key, columns suffixed ``_old`` / ``_new``):
    fit_by_control.csv          region rows of control_fit.csv per control and geography, with presence
                                (both / only_old / only_new) so a control present in one run only is never dropped
    fit_by_group_geography.csv  WAPE pooled per control group and geography (sum |error| / sum target)
    rare_groups.csv             region totals of the rare groups (6+ person households, persons 18-19,
                                single-adult households with minors); the old run controls Zensus family types,
                                the new run households with minors at 1 km, so their definitions differ (column)
    household_totals_per_batch.csv  household-total target against synthetic households per batch
    headline.csv                donor reuse, weight-class gap, solver paths, coverage ratios
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pandas as pd

# Ordered (pattern, group) pairs; the first match wins. Groups follow braunschweig/popsim/control_groups.py
# loosely: they only bundle controls for reporting and decide nothing in the model.
GROUP_PATTERNS: list[tuple[str, str]] = [
    (r"^Insgesamt_Haushalte", "household_total"),
    (r"Groesse_des_privaten_Haushalts", "household_size"),
    (r"^HH_MINOR_|Typ_priv_HH_Familie", "household_type"),
    (r"Tenure", "tenure"),
    (r"^building_type_", "building_type"),
    (r"^[FM]_AGE_", "age_sex"),
    (r"^EMPLOYED_[FM]_", "employed_grid"),
    (r"^OWN_CARS_", "own_cars"),
    (r"^OWN_BIKES_", "own_bikes"),
    (r"^(beruflabschluss|economic_status|education|employed|employment_status|escort_participation|has_ebike"
     r"|leisure_participation|number_of_bicycles|number_of_cars|pt_ticket_group4|schulabschluss|trip_class"
     r"|work_by_employment)(?:_.*)?_KREIS_target$", "kreis:{0}"),
]


def control_group(control: str) -> str:
    """Return the reporting group of a control column name (``other`` when no pattern matches)."""
    for pattern, group in GROUP_PATTERNS:
        match = re.search(pattern, control)
        if match:
            return group.format(*match.groups()) if "{0}" in group else group
    return "other"


def _region_fit(diag: Path) -> pd.DataFrame:
    fit = pd.read_csv(diag / "control_fit.csv")
    fit = fit[fit["scope"] == "region"].copy()
    fit["group"] = fit["control"].map(control_group)
    other = int((fit["group"] == "other").sum())
    print(f"[compare_runs] {diag.name}: controls with a named group {len(fit) - other}/{len(fit)}, "
          f"fallback group 'other' {other} ({100.0 * other / max(len(fit), 1):.1f}%)")
    if other:
        print(f"[compare_runs] WARNING: controls without a group pattern: {sorted(fit.loc[fit['group'] == 'other', 'control'])}")
    return fit[["control", "geography", "group", "target_sum", "synthetic_sum", "abs_error", "bias", "wape"]]


def fit_by_control(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    merged = old.merge(new, on=["control", "geography", "group"], how="outer", suffixes=("_old", "_new"),
                       indicator=True)
    merged["presence"] = merged.pop("_merge").map({"both": "both", "left_only": "only_old",
                                                    "right_only": "only_new"})
    return merged.sort_values(["group", "geography", "control"]).reset_index(drop=True)


def fit_by_group_geography(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    frames = []
    for label, fit in (("old", old), ("new", new)):
        pooled = fit[fit["target_sum"] > 0].groupby(["group", "geography"], as_index=False)[
            ["target_sum", "synthetic_sum", "abs_error"]].sum()
        pooled["wape"] = pooled["abs_error"] / pooled["target_sum"]
        pooled["n_controls"] = fit[fit["target_sum"] > 0].groupby(["group", "geography"]).size().values
        frames.append(pooled.set_index(["group", "geography"]).add_suffix(f"_{label}"))
    out = frames[0].join(frames[1], how="outer").reset_index()
    out["wape_change"] = out["wape_new"] - out["wape_old"]
    return out.sort_values(["geography", "group"]).reset_index(drop=True)


RARE_GROUPS: list[tuple[str, str, str]] = [
    ("households with 6+ persons", r"^6_Personen_und_mehr", "Zensus household size 6+ (same in both runs)"),
    ("persons aged 18-19", r"^[FM]_AGE_18_19_", "Zensus age x sex 18-19, F + M (same in both runs)"),
    ("single-adult households with minors", r"^HH_MINOR_ONE_ADULT_|^Alleinerziehende_",
     "old: Zensus family type single parent (children of any age, 100 m); new: one adult with minors (1 km)"),
]


def rare_groups(old: pd.DataFrame, new: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for name, pattern, definition in RARE_GROUPS:
        row = {"rare_group": name, "definition": definition}
        for label, fit in (("old", old), ("new", new)):
            sel = fit[fit["control"].str.contains(pattern, regex=True)]
            target, synthetic = sel["target_sum"].sum(), sel["synthetic_sum"].sum()
            row[f"target_{label}"] = target
            row[f"synthetic_{label}"] = synthetic
            row[f"relative_error_{label}"] = (synthetic - target) / target if target else float("nan")
            row[f"wape_{label}"] = sel["abs_error"].sum() / target if target else float("nan")
        rows.append(row)
    return pd.DataFrame(rows)


def household_totals_per_batch(old_dir: Path, new_dir: Path) -> pd.DataFrame:
    frames = []
    for label, diag in (("old", old_dir), ("new", new_dir)):
        fit = pd.read_csv(diag / "control_fit.csv")
        sel = fit[(fit["scope"] == "batch") & fit["control"].str.startswith("Insgesamt_Haushalte")
                  & (fit["geography"] == "ZENSUS1km")]
        sel = sel.set_index("batch")[["target_sum", "synthetic_sum"]]
        sel["difference"] = sel["synthetic_sum"] - sel["target_sum"]
        frames.append(sel.add_suffix(f"_{label}"))
    return frames[0].join(frames[1], how="outer").reset_index()


def headline(old_dir: Path, new_dir: Path) -> pd.DataFrame:
    rows: dict[str, dict[str, float]] = {}
    for label, diag in (("old", old_dir), ("new", new_dir)):
        reuse = pd.read_csv(diag / "reuse.csv")
        region = reuse[reuse["scope"] == "region"].iloc[0]
        for key in ("n_synthetic_households", "n_distinct_used", "distinct_share_of_seed", "kish_n_eff",
                    "max_copies", "top100_share", "top1000_share", "top5000_share"):
            rows.setdefault(f"reuse.{key}", {})[label] = float(region[key])
        weights = pd.read_csv(diag / "weight_class_shares.csv")
        rows.setdefault("weight_class.max_abs_gap_pp", {})[label] = float(weights["difference_pp"].abs().max())
        paths = pd.read_csv(diag / "solver_paths.csv")
        for level, grp in paths.groupby("level"):
            rate = grp["non_simultaneous"].sum() / grp["n_parents"].sum()
            rows.setdefault(f"solver.non_simultaneous_rate.{level}", {})[label] = float(rate)
        not_solved = paths[["first_not_solved", "retry_not_solved"]].sum().sum()
        rows.setdefault("solver.not_solved_calls", {})[label] = float(not_solved)
        coverage = pd.read_csv(diag / "control_coverage.csv")
        for _, cov in coverage[coverage["n_uncovered"] > 0].iterrows():
            rows.setdefault(f"coverage_ratio.{cov['group']}", {})[label] = float(cov["ratio"])
        resolved = diag / "resolved_record_shares.csv"
        if resolved.exists():
            for _, rec in pd.read_csv(resolved).iterrows():
                rows.setdefault(f"resolved_ratio.{rec['attribute']}", {})[label] = float(rec["ratio"])
    out = pd.DataFrame(rows).T.reindex(columns=["old", "new"])
    out.index.name = "metric"
    return out.reset_index()


def main(old_dir: Path, new_dir: Path, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    old, new = _region_fit(old_dir), _region_fit(new_dir)
    tables = {
        "fit_by_control": fit_by_control(old, new),
        "fit_by_group_geography": fit_by_group_geography(old, new),
        "rare_groups": rare_groups(old, new),
        "household_totals_per_batch": household_totals_per_batch(old_dir, new_dir),
        "headline": headline(old_dir, new_dir),
    }
    for name, table in tables.items():
        table.to_csv(out_dir / f"{name}.csv", index=False)
        print(f"[compare_runs] wrote {name}.csv ({len(table)} rows)")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3]))
