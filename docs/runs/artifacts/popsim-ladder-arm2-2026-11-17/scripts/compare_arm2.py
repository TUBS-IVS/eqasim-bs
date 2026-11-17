"""Compare ladder arm 2 of issue #647 (WP3) with arm 1 (#645), per batch, against the pre-registered bounds.

Reads the diagnostics directories written by ``python -m braunschweig.analysis.popsim_batch_diagnostics.run``
and the arms' batch folders. Writes ``arm2_comparison.csv`` (long table: batch, arm, metric, value) and
``arm2_verdict.json`` and prints both. Bounds as fixed in docs/runs/popsim-ladder-arm2-2026-11-17.yml before
the runs:

* G3: WAPE per geography <= arm 1 + 0.02 (ZENSUS100m), + 0.02 (ZENSUS1km), + 0.01 (KREIS), judged over the
  controls WITHOUT building_type_* (their targets change); the all-controls WAPE is reported too.
  WAPE of a geography = sum of |synthetic - target| over its controls and zones / sum of targets, zero-target
  controls left out, from the batch rows of control_fit.csv (as compare_arm1.py of #645).
* Use of the resolved records: synthetic share / seed weight share in [0.5, 2.0] for each raw-code group
  (coverage.resolved_record_shares). Arm 1 has no raw columns, so its shares are computed on the arm-2 seed
  tables (same records and H_ID, raw codes added) with arm 1's synthetic usage.
* G2: |synthetic share - seed weight share| <= 5 pp in every H_GEW class.

Usage:
    python compare_arm2.py <repo_root> <out_dir> 1=<diag>:<work> 2=<diag>:<work>
"""
import dataclasses
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, sys.argv[1])
from braunschweig.analysis.popsim_batch_diagnostics.batch_folder import load_batch_folder  # noqa: E402
from braunschweig.analysis.popsim_batch_diagnostics.coverage import resolved_record_shares  # noqa: E402
from braunschweig.analysis.popsim_batch_diagnostics.reuse import usage_counts, weight_class_shares  # noqa: E402

G2_BOUND_PP = 5.0
G3_BOUNDS = {"ZENSUS100m": 0.02, "ZENSUS1km": 0.02, "KREIS": 0.01}
RATIO_BOUNDS = (0.5, 2.0)
BATCHES = ("batch_000", "batch_032")

out_dir = Path(sys.argv[2])
out_dir.mkdir(parents=True, exist_ok=True)
arms = {}
for spec in sys.argv[3:]:
    name, dirs = spec.split("=", 1)
    diag, work = dirs.split(":", 1)
    arms[name] = (Path(diag), Path(work))
if set(arms) != {"1", "2"}:
    raise SystemExit("give exactly the arms 1 and 2")

rows = []


def add(batch, arm, metric, value):
    rows.append({"batch": batch, "arm": arm, "metric": metric, "value": float(value)})


seeds_with_raw = {batch: load_batch_folder(arms["2"][1] / batch) for batch in BATCHES}
for arm, (diag, work) in arms.items():
    fit = pd.read_csv(diag / "control_fit.csv")
    fit = fit[(fit["scope"] == "batch") & (fit["target_sum"] > 0)]
    solver = pd.read_csv(diag / "solver_paths.csv")
    reuse = pd.read_csv(diag / "reuse.csv")
    for batch in BATCHES:
        f = fit[fit["batch"] == batch]
        is_building_type = f["control"].str.startswith("building_type_")
        for geo, g in f.groupby("geography"):
            add(batch, arm, f"wape_{geo}", g["abs_error"].sum() / g["target_sum"].sum())
            h = g[~is_building_type.loc[g.index]]
            add(batch, arm, f"wape_without_building_type_{geo}", h["abs_error"].sum() / h["target_sum"].sum())
        bt = f[is_building_type]
        if len(bt):
            add(batch, arm, "wape_building_type", bt["abs_error"].sum() / bt["target_sum"].sum())
            add(batch, arm, "building_type_target_sum", bt["target_sum"].sum())
            add(batch, arm, "building_type_synthetic_sum", bt["synthetic_sum"].sum())
        hh = f[f["control"].str.startswith("Insgesamt_Haushalte") & (f["geography"] == "ZENSUS100m")]
        add(batch, arm, "households_synthetic", hh["synthetic_sum"].sum())
        add(batch, arm, "households_target", hh["target_sum"].sum())
        for _, r in solver[solver["batch"] == batch].iterrows():
            add(batch, arm, f"n_parents_{r['level']}", r["n_parents"])
            add(batch, arm, f"non_simultaneous_{r['level']}", r["non_simultaneous"])
        rb = reuse[(reuse["scope"] == "batch") & (reuse["subject"] == batch)].iloc[0]
        for key in ("n_distinct_used", "kish_n_eff", "max_copies", "top1000_share"):
            add(batch, arm, key, rb[key])
        folder = load_batch_folder(work / batch)
        counts = usage_counts(folder.expanded)
        shares = weight_class_shares(folder.seed_households, counts)
        for _, r in shares.iterrows():
            add(batch, arm, f"weight_class_diff_pp_{r['weight_class']}", r["difference_pp"])
        add(batch, arm, "weight_class_max_abs_diff_pp", shares["difference_pp"].abs().max())
        raw_seed = seeds_with_raw[batch]
        view = dataclasses.replace(folder, seed_households=raw_seed.seed_households,
                                   seed_persons=raw_seed.seed_persons)
        for _, r in resolved_record_shares(view, counts).iterrows():
            key = f"{r['attribute']}[{r['raw_codes']}]"
            add(batch, arm, f"resolved_seed_share_{key}", r["seed_weight_share"])
            add(batch, arm, f"resolved_synthetic_share_{key}", r["synthetic_share"])
            add(batch, arm, f"resolved_ratio_{key}", r["ratio"])

table = pd.DataFrame(rows)
table.to_csv(out_dir / "arm2_comparison.csv", index=False)
wide = table.pivot_table(index=["batch", "metric"], columns="arm", values="value")

verdict = {"g2_bound_pp": G2_BOUND_PP, "g3_bounds": G3_BOUNDS, "ratio_bounds": RATIO_BOUNDS}
checks = {}
for batch in BATCHES:
    for geo, bound in G3_BOUNDS.items():
        key = f"wape_without_building_type_{geo}"
        base, val = wide.loc[(batch, key), "1"], wide.loc[(batch, key), "2"]
        checks[f"g3_{batch}_{geo}"] = {"wape_arm1": base, "wape_arm2": val, "limit": base + bound,
                                       "met": bool(val <= base + bound)}
    gap = wide.loc[(batch, "weight_class_max_abs_diff_pp"), "2"]
    checks[f"g2_{batch}"] = {"max_abs_gap_pp": gap, "met": bool(gap <= G2_BOUND_PP)}
    ratio_rows = [m for m in wide.loc[batch].index if m.startswith("resolved_ratio_")]
    for metric in ratio_rows:
        val = wide.loc[(batch, metric), "2"]
        checks[f"{metric}_{batch}"] = {"ratio_arm1": wide.loc[(batch, metric), "1"], "ratio_arm2": val,
                                       "met": bool(RATIO_BOUNDS[0] <= val <= RATIO_BOUNDS[1])}
verdict["checks"] = checks
verdict["all_met"] = all(c["met"] for c in checks.values())
(out_dir / "arm2_verdict.json").write_text(json.dumps(verdict, indent=2, default=float))
pd.set_option("display.width", 220)
pd.set_option("display.max_rows", 500)
print(wide.to_string())
print(json.dumps(verdict, indent=2, default=float))
