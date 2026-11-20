"""Compare ladder arm 3 of issue #646 (WP4, controlled rounding) with arm 2 (#647), per batch.

Reads the diagnostics directories written by
``python -m braunschweig.analysis.popsim_batch_diagnostics.run --misfit`` and the arms' batch folders,
applies the checks fixed in ``docs/runs/popsim-ladder-arm3-2026-11-18.yml`` before the runs, and writes
into ``<out_dir>``: ``arm3_comparison.csv`` (long table: batch, arm, metric, value),
``arm3_rare_groups.csv``, ``arm3_call_status_counts.csv`` and ``arm3_verdict.json``. It prints the wide
comparison table and the verdict.

Arms:

* ``2``  = arm 2 as run for ``popsim-ladder-arm2-2026-11-17`` (fork 0.10.0+bs.1, rounding ``round``);
* ``2r`` = optional: the arm-2 batch folders run again with the release candidate and rounding ``round``.
  Its expanded households must be byte-identical to arm 2 (neutral equivalence), and its checkpoint store
  gives the household conservation of the ``round`` mode, which the arm-2 diagnostics did not hold yet;
* ``3``  = arm 3: the arm-2 inputs with rounding ``controlled``.

Checks with a bound (``met`` in the verdict):

* G4: no ZENSUS100m control with a target sum of at least 40, household total left out, has a net
  rounding shift ``|I - F| / T`` above 0.10 in arm 3 (``rounding_shift.csv`` of the diagnostics, which
  applies ``fit.rounding_shift``). The arm-2 counts are reported next to it.
* G3: WAPE per geography <= arm 2 + 0.02 (ZENSUS100m), + 0.02 (ZENSUS1km), + 0.01 (KREIS), over the
  controls without ``building_type_*`` as in arm 2. WAPE of a geography = sum of |synthetic - target| over
  its controls and zones / sum of targets, zero-target controls left out, from the batch rows of
  ``control_fit.csv``.
* Solver path of arm 3: no non-simultaneous parent at any level; every simultaneous call recorded as
  controlled with the method ``search``, with status OPTIMAL or FEASIBLE and converged. Read with the
  pipeline's own gate (``braunschweig.popsim.stage.solver_path_gate.summarize_controlled_calls``).
* G1, G2, G5 as in ``compare_arm1.py`` of issue #645; G6: every file of ``configs/`` and ``data/`` of
  an arm-3 batch equals the arm-2 file except ``configs/settings.yaml``; G7: ``tool.json`` of arm 3 names
  the expected version and commit; reproducibility: a repeat of one batch gives the same sha256 of
  ``final_expanded_household_ids.csv``; neutral equivalence (with ``2r``): the same for arm 2 and ``2r``.

Reported without a bound: the WAPE over all controls, household totals, reuse (distinct seed households,
Kish effective size, largest number of copies), the H_GEW class gaps, the shares of the resolved seed
records, the rounding WAPE per level (``round_wape`` of ``misfit_split.csv`` as
``sum(round_wape * target_sum) / sum(target_sum)`` over the controls of the level without the household
total), the household conservation per level, the search totals of arm 3, the status of every integerizer
call, wall time and peak RSS, and the rare-group table (target ``T``, float sum ``F`` and integer sum ``I``
at ZENSUS100m for households of 5 and of 6 or more persons, single parents, and ages 18-19 by sex).

Usage:
    python compare_arm3.py <repo_root> <out_dir> --batches batch_000 batch_032 \
        --arm 2=<diag>:<work> [--arm 2r=<diag>:<work>] --arm 3=<diag>:<work> \
        --expected-version 0.10.0+bs.2 --expected-commit <sha> \
        [--repeat <work_A>:<work_repeat>:<batch>]
"""
import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

import pandas as pd

G2_BOUND_PP = 5.0
G3_BOUNDS = {"ZENSUS100m": 0.02, "ZENSUS1km": 0.02, "KREIS": 0.01}
G4_LEVEL = "ZENSUS100m"
SIMULTANEOUS = {"simultaneous", "retry_simultaneous"}
SINGLE_FAILED = "single_failed_smart_round"
HOUSEHOLD_TOTAL_PREFIX = "Insgesamt_Haushalte"
RARE_GROUP_CONTROLS = (
    "5_Personen_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m_target",
    "6_Personen_und_mehr_Groesse_des_privaten_Haushalts_100m_Gitter_ZENSUS100m_target",
    "Alleinerziehende_Typ_priv_HH_Familie_100m_Gitter_ZENSUS100m_target",
    "M_AGE_18_19_agg_ZENSUS100m_target",
    "F_AGE_18_19_agg_ZENSUS100m_target",
)
CONSERVATION_METRICS = ("abs_deviation_share", "conserved_pair_share", "distinct_rows_parent",
                        "distinct_rows_sub")
CONTROLLED_TOTALS = (
    "controlled_simul_calls", "search_calls", "lp_calls", "controlled_non_success_calls",
    "search_not_converged_calls", "swaps", "flow_wanted", "flow_achieved", "zone_shortfall",
    "row_shortfall", "distance_start", "distance_final", "hh_unreachable", "hh_outside_lp", "hh_excess",
    "conservation_loss_share", "not_converged_share")
FRACTIONAL_LINE = re.compile(r"fractional sample weights kept: (\d+) groups, (\d+) with 0 < weight < 1")
ELAPSED_LINE = re.compile(r"Elapsed \(wall clock\) time \(h:mm:ss or m:ss\): (\S+)")
RSS_LINE = re.compile(r"Maximum resident set size \(kbytes\): (\d+)")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def seconds_of(elapsed: str) -> float:
    """Seconds of a GNU time elapsed field (``h:mm:ss`` or ``m:ss.ss``)."""
    seconds = 0.0
    for part in elapsed.split(":"):
        seconds = seconds * 60.0 + float(part)
    return seconds


def read_record(work: Path, batch: str):
    """The integerizer path record of one batch as text columns, with a ``batch`` column; None if absent."""
    path = work / batch / "output" / "integerizer_paths.csv"
    if not path.is_file():
        return None
    record = pd.read_csv(path, dtype=str, keep_default_na=False)
    record["batch"] = batch
    return record


def differing_input_files(reference: Path, candidate: Path) -> list:
    """Relative paths under ``configs/`` and ``data/`` that differ or exist on one side only."""
    names = set()
    for root in (reference, candidate):
        for sub in ("configs", "data"):
            names.update(p.relative_to(root).as_posix() for p in (root / sub).rglob("*") if p.is_file())
    differing = []
    for name in sorted(names):
        a, b = reference / name, candidate / name
        if not a.is_file() or not b.is_file() or sha256(a) != sha256(b):
            differing.append(name)
    return differing


def main(argv=None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("repo_root", type=Path)
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--batches", nargs="+", required=True)
    parser.add_argument("--arm", action="append", required=True, help="<name>=<diag dir>:<work dir>")
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--expected-commit", required=True)
    parser.add_argument("--repeat", help="<work_A>:<work_repeat>:<batch>")
    args = parser.parse_args(argv)

    sys.path.insert(0, str(args.repo_root))
    from braunschweig.analysis.popsim_batch_diagnostics.batch_folder import load_batch_folder
    from braunschweig.analysis.popsim_batch_diagnostics.coverage import resolved_record_shares
    from braunschweig.analysis.popsim_batch_diagnostics.reuse import usage_counts, weight_class_shares
    from braunschweig.popsim.stage import solver_path_gate

    arms = {}
    for spec in args.arm:
        name, dirs = spec.split("=", 1)
        diag, work = dirs.split(":", 1)
        arms[name] = (Path(diag), Path(work))
    if not {"2", "3"} <= set(arms) or set(arms) - {"2", "2r", "3"}:
        raise SystemExit("give the arms 2 and 3, and optionally 2r")
    args.out_dir.mkdir(parents=True, exist_ok=True)

    rows, rare_rows, status_rows = [], [], []
    g1, g5, expanded_sha = {}, {}, {}

    def add(batch, arm, metric, value):
        rows.append({"batch": batch, "arm": arm, "metric": metric, "value": float(value)})

    for arm, (diag, work) in arms.items():
        fit = pd.read_csv(diag / "control_fit.csv")
        fit = fit[(fit["scope"] == "batch") & (fit["target_sum"] > 0)]
        solver = pd.read_csv(diag / "solver_paths.csv")
        reuse = pd.read_csv(diag / "reuse.csv")
        shift = pd.read_csv(diag / "rounding_shift.csv")
        split = pd.read_csv(diag / "misfit_split.csv")
        conservation_path = diag / "household_conservation.csv"
        conservation = pd.read_csv(conservation_path) if conservation_path.is_file() else None
        for batch in args.batches:
            f = fit[fit["batch"] == batch]
            is_building_type = f["control"].str.startswith("building_type_")
            for geo, g in f.groupby("geography"):
                add(batch, arm, f"wape_{geo}", g["abs_error"].sum() / g["target_sum"].sum())
                h = g[~is_building_type.loc[g.index]]
                add(batch, arm, f"wape_without_building_type_{geo}",
                    h["abs_error"].sum() / h["target_sum"].sum())
            hh = f[f["control"].str.startswith(HOUSEHOLD_TOTAL_PREFIX) & (f["geography"] == G4_LEVEL)]
            add(batch, arm, "households_synthetic", hh["synthetic_sum"].sum())
            add(batch, arm, "households_target", hh["target_sum"].sum())
            s = solver[solver["batch"] == batch]
            for _, r in s.iterrows():
                add(batch, arm, f"n_parents_{r['level']}", r["n_parents"])
                add(batch, arm, f"non_simultaneous_{r['level']}", r["non_simultaneous"])
            rb = reuse[(reuse["scope"] == "batch") & (reuse["subject"] == batch)].iloc[0]
            for key in ("n_distinct_used", "kish_n_eff", "max_copies", "top1000_share"):
                add(batch, arm, key, rb[key])
            sb = shift[shift["batch"] == batch]
            add(batch, arm, "rounding_shift_n_controls", len(sb))
            add(batch, arm, "rounding_shift_n_outside", (~sb["within_bound"].astype(bool)).sum())
            add(batch, arm, "rounding_shift_max", sb["shift"].max())
            sp = split[(split["batch"] == batch) & (split["target_sum"] > 0)
                       & ~split["control"].str.startswith(HOUSEHOLD_TOTAL_PREFIX)]
            for level, g in sp.groupby("level"):
                add(batch, arm, f"round_wape_{level}",
                    (g["round_wape"] * g["target_sum"]).sum() / g["target_sum"].sum())
            rare = split[(split["batch"] == batch) & (split["level"] == G4_LEVEL)
                         & split["control"].isin(RARE_GROUP_CONTROLS)]
            for _, r in rare.iterrows():
                rare_rows.append({"batch": batch, "arm": arm, "control": r["control"],
                                  "T": r["target_sum"], "F": r["float_sum"], "I": r["integer_sum"]})
            if conservation is not None:
                for _, r in conservation[conservation["batch"] == batch].iterrows():
                    for key in CONSERVATION_METRICS:
                        add(batch, arm, f"conservation_{key}_{r['level']}", r[key])

            folder = load_batch_folder(work / batch)
            counts = usage_counts(folder.expanded)
            shares = weight_class_shares(folder.seed_households, counts)
            for _, r in shares.iterrows():
                add(batch, arm, f"weight_class_diff_pp_{r['weight_class']}", r["difference_pp"])
            add(batch, arm, "weight_class_max_abs_diff_pp", shares["difference_pp"].abs().max())
            for _, r in resolved_record_shares(folder, counts).iterrows():
                key = f"{r['attribute']}[{r['raw_codes']}]"
                add(batch, arm, f"resolved_seed_share_{key}", r["seed_weight_share"])
                add(batch, arm, f"resolved_synthetic_share_{key}", r["synthetic_share"])
                add(batch, arm, f"resolved_ratio_{key}", r["ratio"])
            expanded_sha[(arm, batch)] = sha256(work / batch / "output" / "final_expanded_household_ids.csv")

            time_file = work / f"{batch}.time"
            if time_file.is_file():
                text = time_file.read_text(errors="replace")
                elapsed, rss = ELAPSED_LINE.search(text), RSS_LINE.search(text)
                if elapsed:
                    add(batch, arm, "wall_time_seconds", seconds_of(elapsed.group(1)))
                if rss:
                    # GNU time reports kibibytes; GiB is the unit of the pipeline's memory gate
                    add(batch, arm, "peak_rss_gib", int(rss.group(1)) / 2**20)

            record = read_record(work, batch)
            if record is None:
                continue
            calls = record[record["row_type"] == "call"]
            extra = [c for c in ("rounding", "method") if c in calls.columns]
            for key, n in calls.groupby(["kind", *extra, "status"]).size().items():
                key = key if isinstance(key, tuple) else (key,)
                status_rows.append({"batch": batch, "arm": arm, **dict(zip(["kind", *extra, "status"], key)),
                                    "n": int(n)})
            outcomes = record[(record["row_type"] == "outcome") & (record["outcome"] != SINGLE_FAILED)]
            check = {}
            for level, o in outcomes.groupby("geography"):
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
            seed_calls = calls[calls["trace_label"].str.endswith("STAAT_1")]
            groups = int(m.group(1)) if m else None
            seed_rows = sorted({int(x) for x in seed_calls["rows"] if x})
            # G1 as in compare_arm1.py: the seed-level single integerizer receives every seed group, so
            # none started at weight 0.
            g1[f"{arm}/{batch}"] = {"groups": groups,
                                    "groups_below_one_kept": int(m.group(2)) if m else None,
                                    "seed_level_call_rows": seed_rows,
                                    "met": groups is not None and groups in seed_rows}
            if arm == "3":
                try:
                    totals = solver_path_gate.summarize_controlled_calls(record, "controlled")
                except RuntimeError as error:
                    g5[f"{arm}/{batch}"]["controlled_proof_error"] = str(error)
                else:
                    for key in CONTROLLED_TOTALS:
                        add(batch, arm, f"controlled_{key}", totals[key])

    table = pd.DataFrame(rows)
    table.to_csv(args.out_dir / "arm3_comparison.csv", index=False)
    pd.DataFrame(rare_rows, columns=["batch", "arm", "control", "T", "F", "I"]).to_csv(
        args.out_dir / "arm3_rare_groups.csv", index=False)
    pd.DataFrame(status_rows, columns=["batch", "arm", "kind", "rounding", "method", "status", "n"]).to_csv(
        args.out_dir / "arm3_call_status_counts.csv", index=False)
    wide = table.pivot_table(index=["batch", "metric"], columns="arm", values="value")

    def value(batch, metric, arm):
        return wide.loc[(batch, metric), arm] if (batch, metric) in wide.index else float("nan")

    checks = {}
    for batch in args.batches:
        outside = value(batch, "rounding_shift_n_outside", "3")
        checks[f"g4_{batch}"] = {
            "n_controls": value(batch, "rounding_shift_n_controls", "3"),
            "n_outside_arm2": value(batch, "rounding_shift_n_outside", "2"),
            "n_outside_arm3": outside,
            "max_shift_arm2": value(batch, "rounding_shift_max", "2"),
            "max_shift_arm3": value(batch, "rounding_shift_max", "3"),
            "met": bool(outside == 0)}
        for geo, bound in G3_BOUNDS.items():
            key = f"wape_without_building_type_{geo}"
            base, val = value(batch, key, "2"), value(batch, key, "3")
            checks[f"g3_{batch}_{geo}"] = {"wape_arm2": base, "wape_arm3": val, "limit": base + bound,
                                           "met": bool(val <= base + bound)}
        non_simultaneous = {m[len("non_simultaneous_"):]: value(batch, m, "3")
                            for m in wide.loc[batch].index if m.startswith("non_simultaneous_")}
        proof_error = g5.get(f"3/{batch}", {}).get("controlled_proof_error")
        calls_3 = value(batch, "controlled_controlled_simul_calls", "3")
        checks[f"solver_paths_{batch}"] = {
            "non_simultaneous_parents": non_simultaneous,
            "controlled_simul_calls": calls_3,
            "search_calls": value(batch, "controlled_search_calls", "3"),
            "non_success_calls": value(batch, "controlled_controlled_non_success_calls", "3"),
            "not_converged_calls": value(batch, "controlled_search_not_converged_calls", "3"),
            "controlled_proof_error": proof_error,
            "met": bool(proof_error is None and non_simultaneous
                        and all(v == 0 for v in non_simultaneous.values())
                        and calls_3 > 0
                        and value(batch, "controlled_search_calls", "3") == calls_3
                        and value(batch, "controlled_controlled_non_success_calls", "3") == 0
                        and value(batch, "controlled_search_not_converged_calls", "3") == 0)}
        checks[f"g1_{batch}"] = g1.get(f"3/{batch}", {"met": False, "error": "no path record"})
        gap = value(batch, "weight_class_max_abs_diff_pp", "3")
        checks[f"g2_{batch}"] = {"max_abs_gap_pp_arm2": value(batch, "weight_class_max_abs_diff_pp", "2"),
                                 "max_abs_gap_pp_arm3": gap, "met": bool(gap <= G2_BOUND_PP)}
        levels = {k: v for k, v in g5.get(f"3/{batch}", {}).items() if isinstance(v, dict)}
        checks[f"g5_{batch}"] = {"levels": levels,
                                 "met": bool(levels) and all(v["agree"] for v in levels.values())}
        differing = differing_input_files(arms["2"][1] / batch, arms["3"][1] / batch)
        checks[f"g6_{batch}"] = {"differing_input_files": differing,
                                 "met": differing == ["configs/settings.yaml"]}
        if "2r" in arms:
            digests = [expanded_sha[("2", batch)], expanded_sha[("2r", batch)]]
            checks[f"neutral_equivalence_{batch}"] = {"sha256_arm2": digests[0], "sha256_arm2r": digests[1],
                                                      "met": digests[0] == digests[1]}
    tool = json.loads((arms["3"][1] / "tool.json").read_text(encoding="utf-8"))
    tool_text = json.dumps(tool)
    checks["g7"] = {"tool": tool, "expected_version": args.expected_version,
                    "expected_commit": args.expected_commit,
                    "met": tool.get("populationsim") == args.expected_version
                    and args.expected_commit in tool_text}
    if args.repeat:
        work_a, work_r, batch = args.repeat.split(":")
        digests = [sha256(Path(w) / batch / "output" / "final_expanded_household_ids.csv")
                   for w in (work_a, work_r)]
        checks["reproducibility"] = {"batch": batch, "sha256": digests, "met": digests[0] == digests[1]}

    verdict = {"g2_bound_pp": G2_BOUND_PP, "g3_bounds": G3_BOUNDS, "g4_bound": 0.10, "g4_min_target": 40.0,
               "expanded_sha256": {f"{arm}/{batch}": digest for (arm, batch), digest in expanded_sha.items()},
               "checks": checks, "all_met": all(c["met"] for c in checks.values())}
    (args.out_dir / "arm3_verdict.json").write_text(json.dumps(verdict, indent=2, default=float),
                                                    encoding="utf-8")
    pd.set_option("display.width", 220)
    pd.set_option("display.max_rows", 1000)
    print(wide.to_string())
    print(pd.DataFrame(rare_rows).to_string())
    print(json.dumps(verdict, indent=2, default=float))
    return 0


if __name__ == "__main__":
    sys.exit(main())
