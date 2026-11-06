"""Recount the plan check of a finished run with the plan check of #576 (ADR-0188), read-only.

Loads ``plan_check.py`` of THIS branch from a file (``--plan-check``) under a private module name, so the run's
own code tree (``--code``) supplies only its imports (``braunschweig.popsim.plan_validation``). Streams the
prepared population that the run's written config names (``plans.inputPlansFile``), with the run's metric
thresholds read from the run's ``plan_check.json`` and the two bound thresholds of ADR-0188 from the module
defaults. Identity check: the totals the old check counted (``n_persons``, ``n_plans_late``,
``n_plans_non_monotonic``, ``n_pt_trips``, ``n_pt_as_walk`` of every block) must equal the run's
``plan_check.json``; the script exits non-zero otherwise. Writes ``plan_check_i576.json`` and ``summary.txt``
into ``--out`` (which must not exist). Nothing in the run or cache directory is written.

Usage on felix:
  PYTHONDONTWRITEBYTECODE=1 nice python i576_plan_check_recount.py --code ~/prod100_20261031/eqasim-bs \
      --plan-check ~/scratch_i576/plan_check.py \
      --stage-dir ~/prod100_20261031/cache/braunschweig.matsim.simulation.prepare__57fd011e45464705ad66d48134be0b63.cache \
      --config-name braunschweig_100pct_prod20261031_config.xml --out ~/scratch_i576/out
"""
import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path

IDENTITY_KEYS = ("n_persons", "n_plans_late", "n_plans_non_monotonic", "n_pt_trips", "n_pt_as_walk")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--code", required=True, help="the run's eqasim-bs checkout (for the module's imports)")
    parser.add_argument("--plan-check", required=True, help="plan_check.py of the #576 branch")
    parser.add_argument("--stage-dir", required=True, help="the prepare stage directory of the run")
    parser.add_argument("--config-name", required=True, help="the written MATSim config in the stage directory")
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    out = Path(args.out).expanduser()
    out.mkdir(parents=True, exist_ok=False)
    sys.path.insert(0, str(Path(args.code).expanduser()))
    spec = importlib.util.spec_from_file_location("plan_check_i576", Path(args.plan_check).expanduser())
    check = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = check   # dataclasses resolve the defining module through sys.modules
    spec.loader.exec_module(check)

    stage_dir = Path(args.stage_dir).expanduser()
    config_path = stage_dir / args.config_name
    run_report = json.loads((stage_dir / check.PLAN_CHECK_FILE_NAME).read_text())
    thresholds = run_report["thresholds"]
    population_path = check.resolve_population_path(config_path)
    print(f"population {population_path}", flush=True)
    started = time.time()
    summary = check.summarise(
        check.iter_person_plans(population_path), simulation_end_s=check.MAX_PLAN_TIME_SECONDS,
        walk_max_distance_m=float(thresholds["walk_max_distance_m"]),
        bicycle_max_distance_m=float(thresholds["bicycle_max_distance_m"]),
        leg_max_duration_s=float(thresholds["leg_max_duration_s"]),
        pt_as_walk_bound_min_distance_m=check.DEFAULT_PT_AS_WALK_BOUND_MIN_DISTANCE_M,
        non_monotonic_bound_min_step_s=check.DEFAULT_NON_MONOTONIC_BOUND_MIN_STEP_S)
    elapsed_s = time.time() - started
    max_defect = float(run_report["threshold"]["max_frozen_defect_share"])
    max_pt_as_walk = float(run_report["threshold"]["max_frozen_pt_as_walk_share"])
    summary["threshold"] = {"max_frozen_defect_share": max_defect, "max_frozen_pt_as_walk_share": max_pt_as_walk,
                            "exceeded": check.find_threshold_violations(summary, max_defect, max_pt_as_walk),
                            "exceeded_in_run": run_report["threshold"].get("exceeded")}

    mismatches = []
    for subpopulation, classes in run_report["subpopulations"].items():
        for name, old in classes.items():
            new = summary["subpopulations"].get(subpopulation, {}).get(name, {})
            mismatches += [f"{subpopulation}/{name}/{key}: run {old.get(key)} recount {new.get(key)}"
                           for key in IDENTITY_KEYS if key in old and old.get(key) != new.get(key)]
    summary["identity_with_run"] = {"keys": list(IDENTITY_KEYS), "mismatches": mismatches}
    (out / "plan_check_i576.json").write_text(json.dumps(summary, indent=2, sort_keys=True))

    lines = [f"population {population_path}", f"streamed in {elapsed_s:.0f} s",
             f"identity with the run's plan_check.json on {IDENTITY_KEYS}: "
             f"{'EXACT' if not mismatches else 'MISMATCH'}"] + mismatches
    for subpopulation, classes in summary["subpopulations"].items():
        for name, block in classes.items():
            lines.append(
                f"{subpopulation}/{name}: persons {block['n_persons']}, non-monotonic {block['n_plans_non_monotonic']} "
                f"({100 * block['share_plans_non_monotonic']:.2f}%), step > {check.DEFAULT_NON_MONOTONIC_BOUND_MIN_STEP_S:.0f} s "
                f"{block['n_plans_non_monotonic_large']} ({100 * block['share_plans_non_monotonic_large']:.2f}%), "
                f"by step {block['non_monotonic_by_step_band']}; pt-intended trips {block['n_pt_trips']}, pt as walk "
                f"{block['n_pt_as_walk']} ({100 * block['share_pt_as_walk']:.2f}%), over "
                f"{check.DEFAULT_PT_AS_WALK_BOUND_MIN_DISTANCE_M:.0f} m {block['n_pt_as_walk_long']} "
                f"({100 * block['share_pt_as_walk_long']:.2f}%), by distance {block['pt_as_walk_by_distance_band']}")
    lines.append(f"exceeded (recount) {summary['threshold']['exceeded']}; exceeded in the run "
                 f"{summary['threshold']['exceeded_in_run']}")
    (out / "summary.txt").write_text("\n".join(lines) + "\n")
    print("\n".join(lines), flush=True)
    return 1 if mismatches else 0


if __name__ == "__main__":
    sys.exit(main())
