"""Compare ladder arm 1 of issue #645 (WP2) with arm 0a, per batch, against the pre-registered bounds.

Reads the diagnostics directories written by ``python -m braunschweig.analysis.popsim_batch_diagnostics.run``
and the arms' batch folders. Writes ``arm1_comparison.csv`` (long table: batch, arm, metric, value) and
``arm1_verdict.json`` (G1, G2, G3, G5, the method rule and the reproducibility check as pre-registered in
docs/runs/popsim-ladder-arm1-2026-11-17.yml) and prints both.

Usage:
    python compare_arm1.py <repo_root> <out_dir> 0a=<diag>:<work> 1p=<diag>:<work> 1g=<diag>:<work> \
        [1x-B=<diag>:<work>] [--repeat <work_A>:<work_repeat>:<batch>]

Bounds (fixed in the manifest before the runs): G2 |synthetic share - seed weight share| <= 5 pp in every
H_GEW class; G3 WAPE <= arm 0a + 0.02 (ZENSUS100m), + 0.02 (ZENSUS1km), + 0.01 (KREIS). WAPE of a geography
= sum of |synthetic - target| over its controls and zones / sum of targets, zero-target controls left out,
from the batch rows of control_fit.csv. Method rule: among the draw methods meeting G3 on both batches,
the one with the smaller maximum |G2 gap| over both batches; within 0.5 pp of each other -> Pareto.
"""
import hashlib
import json
import re
import sys
from pathlib import Path

import pandas as pd

sys.path.insert(0, sys.argv[1])
from braunschweig.analysis.popsim_batch_diagnostics.batch_folder import load_batch_folder  # noqa: E402
from braunschweig.analysis.popsim_batch_diagnostics.reuse import usage_counts, weight_class_shares  # noqa: E402

G2_BOUND_PP = 5.0
G3_BOUNDS = {"ZENSUS100m": 0.02, "ZENSUS1km": 0.02, "KREIS": 0.01}
METHOD_TIE_PP = 0.5
BATCHES = ("batch_000", "batch_032")
SIMULTANEOUS = {"simultaneous", "retry_simultaneous"}
FRACTIONAL_LINE = re.compile(r"fractional sample weights kept: (\d+) groups, (\d+) with 0 < weight < 1")

out_dir = Path(sys.argv[2])
out_dir.mkdir(parents=True, exist_ok=True)
arms, repeat = {}, None
args = sys.argv[3:]
while args:
    spec = args.pop(0)
    if spec == "--repeat":
        repeat = args.pop(0).split(":")
        continue
    name, dirs = spec.split("=", 1)
    diag, work = dirs.split(":", 1)
    arms[name] = (Path(diag), Path(work))

rows = []


def add(batch, arm, metric, value):
    rows.append({"batch": batch, "arm": arm, "metric": metric, "value": float(value)})


donors = {}
g5 = {}
g1 = {}
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
        rb = reuse[(reuse["scope"] == "batch") & (reuse["subject"] == batch)].iloc[0]
        for key in ("n_distinct_used", "kish_n_eff", "max_copies", "top1000_share"):
            add(batch, arm, key, rb[key])
        folder = load_batch_folder(work / batch)
        counts = usage_counts(folder.expanded)
        donors[(arm, batch)] = set(counts.index[counts > 0])
        shares = weight_class_shares(folder.seed_households, counts)
        for _, r in shares.iterrows():
            add(batch, arm, f"weight_class_diff_pp_{r['weight_class']}", r["difference_pp"])
        add(batch, arm, "weight_class_max_abs_diff_pp", shares["difference_pp"].abs().max())
        if shift is not None:
            sb = shift[shift["batch"] == batch]
            add(batch, arm, "rounding_shift_max", sb["shift"].max())
            add(batch, arm, "rounding_shift_n_outside", (~sb["within_bound"].astype(bool)).sum())
        record = work / batch / "output" / "integerizer_paths.csv"
        if record.is_file():
            rec = pd.read_csv(record, dtype=str, keep_default_na=False)
            out = rec[rec["row_type"] == "outcome"]
            out = out[out["outcome"] != "single_failed_smart_round"]
            check = {}
            for level, o in out.groupby("geography"):
                log_row = s[s["level"] == level]
                rec_parents = len(o)
                rec_non_sim = int((~o["outcome"].isin(SIMULTANEOUS)).sum())
                log_parents = int(log_row["n_parents"].iloc[0]) if len(log_row) else None
                log_non_sim = int(log_row["non_simultaneous"].iloc[0]) if len(log_row) else None
                check[level] = {"record_parents": rec_parents, "log_parents": log_parents,
                                "record_non_simultaneous": rec_non_sim, "log_non_simultaneous": log_non_sim,
                                "agree": rec_parents == log_parents and rec_non_sim == log_non_sim}
            g5[f"{arm}/{batch}"] = check
            log_text = (work / batch / "output" / "populationsim.log").read_text(errors="replace")
            m = FRACTIONAL_LINE.search(log_text)
            calls = rec[(rec["row_type"] == "call") & rec["trace_label"].str.endswith("STAAT_1")]
            groups = int(m.group(1)) if m else None
            seed_rows = sorted({int(x) for x in calls["rows"] if x})
            # G1 (pre-registered): no seed group with positive survey weight starts at weight 0. The fork logs
            # the grouped weights it kept fractional; do_integerizing drops groups whose weight is 0 before
            # the seed-level single integerizer, so that call receiving every group shows that none started
            # at 0. (The simultaneous STAAT_1 call row has fewer rows: it sees only groups with a positive
            # integer seed weight.)
            g1[f"{arm}/{batch}"] = {
                "groups": groups,
                "groups_below_one_kept": int(m.group(2)) if m else None,
                "seed_level_call_rows": seed_rows,
                "met": groups is not None and groups in seed_rows,
            }

for arm in arms:
    a, b = donors[(arm, "batch_000")], donors[(arm, "batch_032")]
    add("both", arm, "donor_overlap_share_of_batch_000", len(a & b) / len(a))
    add("both", arm, "distinct_donors_union", len(a | b))

table = pd.DataFrame(rows)
table.to_csv(out_dir / "arm1_comparison.csv", index=False)
wide = table.pivot_table(index=["batch", "metric"], columns="arm", values="value")

verdict = {"g2_bound_pp": G2_BOUND_PP, "g3_bounds": G3_BOUNDS, "arms": {}, "g5": g5, "g1": g1}
for arm in arms:
    if arm == "0a":
        continue
    v = {}
    for batch in BATCHES:
        gap = wide.loc[(batch, "weight_class_max_abs_diff_pp"), arm]
        v[f"g2_{batch}"] = {"max_abs_gap_pp": gap, "met": bool(gap <= G2_BOUND_PP)}
        for geo, bound in G3_BOUNDS.items():
            base, val = wide.loc[(batch, f"wape_{geo}"), "0a"], wide.loc[(batch, f"wape_{geo}"), arm]
            v[f"g3_{batch}_{geo}"] = {"wape_0a": base, "wape": val, "limit": base + bound,
                                      "met": bool(val <= base + bound)}
    g2_items = [x for k, x in v.items() if k.startswith("g2_batch")]
    g3_items = [x for k, x in v.items() if k.startswith("g3_batch")]
    v["g2_met"] = all(x["met"] for x in g2_items)
    v["g3_met"] = all(x["met"] for x in g3_items)
    v["max_gap_both_batches_pp"] = max(x["max_abs_gap_pp"] for x in g2_items)
    verdict["arms"][arm] = v
candidates = {a: v for a, v in verdict["arms"].items() if a in ("1p", "1g") and v["g3_met"]}
if candidates:
    best = min(candidates, key=lambda a: candidates[a]["max_gap_both_batches_pp"])
    if "1p" in candidates and "1g" in candidates and abs(
            candidates["1p"]["max_gap_both_batches_pp"] - candidates["1g"]["max_gap_both_batches_pp"]) <= METHOD_TIE_PP:
        best = "1p"
    verdict["method_rule"] = {"chosen": {"1p": "pareto", "1g": "gumbel"}[best], "candidates": sorted(candidates)}
else:
    verdict["method_rule"] = {"chosen": None, "candidates": []}
if repeat:
    work_a, work_r, batch = repeat
    digests = [hashlib.sha256((Path(w) / batch / "output" / "final_expanded_household_ids.csv").read_bytes()).hexdigest()
               for w in (work_a, work_r)]
    verdict["reproducibility"] = {"batch": batch, "sha256": digests, "identical": digests[0] == digests[1]}
(out_dir / "arm1_verdict.json").write_text(json.dumps(verdict, indent=2, default=float))
pd.set_option("display.width", 220)
print(wide.to_string())
print(json.dumps(verdict, indent=2, default=float))
