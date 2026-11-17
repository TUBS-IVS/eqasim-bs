"""Compare the ladder arms P, 0a and 0b of #138 (WP1) per batch.

Reads the diagnostics output directories written by
``python -m braunschweig.analysis.popsim_batch_diagnostics.run`` for each arm and the arms'
batch folders, and writes ``ladder_comparison.csv`` (long table: batch, arm, metric, value),
``ladder_verdict.json`` (the G3 and fallback verdicts of arm 0b against arm 0a) and prints a
summary.

Usage:
    python compare_ladder.py <repo_root> <out_dir> P=<diag_dir>:<work_dir> 0a=<diag_dir>:<work_dir> 0b=<diag_dir>:<work_dir>

G3 bounds (design section 2, fixed before this comparison): WAPE over the controls of a geography
at most the baseline + 0.02 (ZENSUS100m), + 0.02 (ZENSUS1km), + 0.01 (KREIS); baseline = arm 0a of
the same batch (the comparison asks whether 0b may serve as the fork base). WAPE of a geography =
sum over its controls and zones of |synthetic - target| / sum of targets, controls with a zero
target sum left out, from the batch rows of control_fit.csv.
"""
import json
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, sys.argv[1])
from braunschweig.analysis.popsim_batch_diagnostics.batch_folder import load_batch_folder  # noqa: E402
from braunschweig.analysis.popsim_batch_diagnostics.reuse import usage_counts, weight_class_shares  # noqa: E402

G3_BOUNDS = {"ZENSUS100m": 0.02, "ZENSUS1km": 0.02, "KREIS": 0.01}
BATCHES = ("batch_000", "batch_032")

out_dir = Path(sys.argv[2])
out_dir.mkdir(parents=True, exist_ok=True)
arms = {}
for spec in sys.argv[3:]:
    name, dirs = spec.split("=", 1)
    diag, work = dirs.split(":", 1)
    arms[name] = (Path(diag), Path(work))

rows = []


def add(batch, arm, metric, value):
    rows.append({"batch": batch, "arm": arm, "metric": metric, "value": float(value)})


for arm, (diag, work) in arms.items():
    fit = pd.read_csv(diag / "control_fit.csv")
    fit = fit[(fit["scope"] == "batch") & (fit["target_sum"] > 0)]
    solver = pd.read_csv(diag / "solver_paths.csv")
    reuse = pd.read_csv(diag / "reuse.csv")
    shift_path = diag / "rounding_shift.csv"
    shift = pd.read_csv(shift_path) if shift_path.is_file() else None
    for batch in BATCHES:
        f = fit[fit["batch"] == batch]
        for geo, g in f.groupby("geography"):
            add(batch, arm, f"wape_{geo}", g["abs_error"].sum() / g["target_sum"].sum())
        hh = f[f["control"].str.startswith("Insgesamt_Haushalte") & (f["geography"] == "ZENSUS100m")]
        add(batch, arm, "households_synthetic", hh["synthetic_sum"].sum())
        add(batch, arm, "households_target", hh["target_sum"].sum())
        s = solver[solver["batch"] == batch]
        for _, r in s.iterrows():
            add(batch, arm, f"n_parents_{r['level']}", r["n_parents"])
            add(batch, arm, f"non_simultaneous_{r['level']}", r["non_simultaneous"])
            add(batch, arm, f"not_solved_{r['level']}", r["first_not_solved"] + r["retry_not_solved"])
        rb = reuse[(reuse["scope"] == "batch") & (reuse["subject"] == batch)].iloc[0]
        for key in ("n_distinct_used", "kish_n_eff", "max_copies", "top1000_share"):
            add(batch, arm, key, rb[key])
        folder = load_batch_folder(work / batch)
        shares = weight_class_shares(folder.seed_households, usage_counts(folder.expanded))
        for _, r in shares.iterrows():
            add(batch, arm, f"weight_class_diff_pp_{r['weight_class']}", r["difference_pp"])
        if shift is not None:
            sb = shift[shift["batch"] == batch]
            add(batch, arm, "rounding_shift_max", sb["shift"].max())
            add(batch, arm, "rounding_shift_n_outside", (~sb["within_bound"].astype(bool)).sum())
            add(batch, arm, "rounding_shift_n_controls", len(sb))

table = pd.DataFrame(rows)
table.to_csv(out_dir / "ladder_comparison.csv", index=False)

verdict = {"g3_bounds": G3_BOUNDS, "batches": {}}
wide = table.pivot_table(index=["batch", "metric"], columns="arm", values="value")
for batch in BATCHES:
    v = {}
    for geo, bound in G3_BOUNDS.items():
        a, b = wide.loc[(batch, f"wape_{geo}"), "0a"], wide.loc[(batch, f"wape_{geo}"), "0b"]
        v[f"g3_{geo}"] = {"wape_0a": a, "wape_0b": b, "limit": a + bound, "met": bool(b <= a + bound)}
    for level in ("KREIS", "ZENSUS1km", "ZENSUS100m"):
        key = f"non_simultaneous_{level}"
        if (batch, key) in wide.index:
            a, b = wide.loc[(batch, key), "0a"], wide.loc[(batch, key), "0b"]
            v[f"fallback_{level}"] = {"non_simultaneous_0a": a, "non_simultaneous_0b": b,
                                      "more_in_0b": bool(b > a)}
    verdict["batches"][batch] = v
verdict["g3_met_all"] = all(x["met"] for b in verdict["batches"].values()
                            for k, x in b.items() if k.startswith("g3_"))
verdict["fallback_more_in_0b_any"] = any(x["more_in_0b"] for b in verdict["batches"].values()
                                         for k, x in b.items() if k.startswith("fallback_"))
(out_dir / "ladder_verdict.json").write_text(json.dumps(verdict, indent=2, default=float))
pd.set_option("display.width", 200)
print(wide.to_string())
print(json.dumps(verdict, indent=2, default=float))
