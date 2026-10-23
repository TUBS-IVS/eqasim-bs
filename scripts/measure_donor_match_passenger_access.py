"""Measure the passenger access key of the plan-source matchers on the raw MiD donor build (ADR-0161).

Runs ``braunschweig.popsim.completed_donor.build_completed_donor`` with the production flags (MiD passenger
availability on) once with ``donor_match_passenger_access`` OFF and once ON, and writes ONLY aggregate counts:

- ``n_foreign``: donor persons whose plan source is another MiD row (weekend match, diary match, fillers);
- ``n_crossed``: of those, the persons whose passenger access class (``P_VAUTO`` 3 "never" or not) differs
  from the class of the donor person whose diary they carry;
- ``n_never_with_foreign_lift``: persons of class "never" whose FOREIGN plan source reports a car-passenger
  trip (the defect of person 903464 of the 1 % rerun of the #465 fixes);
- the source columns compared between the arms (how many plan sources the key moved).

The counts are unweighted raw MiD donor counts, not synthetic-population estimates; the synthetic population
draws households from this donor with PopulationSim weights. No row-level data is written.

Usage: python scripts/measure_donor_match_passenger_access.py --mid-dir <MiD raw dir> --out <report.json>
       [--random-seed 1234] [--git-commit <sha>]
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _arm(mid_dir: Path, random_seed: int, passenger_key: bool):
    from braunschweig.popsim import completed_donor as cd
    from braunschweig.popsim.stage import config_keys as keys
    from braunschweig.popsim.weekend_plan_match import passenger_never_class

    result = cd.build_completed_donor(
        mid_dir, random_seed=random_seed, seed_day_filter=None,
        weekend_plan_match_on=keys.DEFAULT_WEEKEND_PLAN_MATCH,
        diary_plan_match_on=keys.DEFAULT_DIARY_PLAN_MATCH,
        exclude_holiday_plan_sources=keys.DEFAULT_EXCLUDE_HOLIDAY_PLAN_SOURCES,
        exclude_rbw_legs=keys.DEFAULT_EXCLUDE_RBW_LEGS,
        drop_leading_arrive_home_leg=keys.DEFAULT_DROP_LEADING_ARRIVE_HOME_LEG,
        diary_match_hard_employment=keys.DEFAULT_DIARY_MATCH_HARD_EMPLOYMENT,
        donor_match_fine_child_age_bands=keys.DEFAULT_DONOR_MATCH_FINE_CHILD_AGE_BANDS,
        passenger_availability_enabled=True,
        donor_match_passenger_access=passenger_key,
    )
    persons = result.persons
    n_foreign, n_crossed = cd.count_passenger_class_crossings(persons)
    foreign = ~(persons["source_H_ID"].eq(persons["H_ID"]) & persons["source_P_ID"].eq(persons["P_ID"]))
    never = passenger_never_class(persons["P_VAUTO"])
    lift = persons["src_has_car_passenger_trip"].fillna(False).astype(bool)
    counts = {
        "n_persons": int(len(persons)),
        "n_foreign": n_foreign,
        "n_crossed": n_crossed,
        "n_never": int(never.sum()),
        "n_never_with_foreign_lift": int((never & foreign & lift).sum()),
        "n_never_with_own_lift": int((never & ~foreign & lift).sum()),
        "weekend_report": repr(result.weekend_report),
        "diary_report": repr(result.diary_report),
    }
    return counts, persons[["H_ID", "P_ID", "source_H_ID", "source_P_ID"]]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--mid-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--random-seed", type=int, default=1234)
    parser.add_argument("--git-commit", help="commit of the code when run from an exported tree without .git")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")

    off, off_sources = _arm(args.mid_dir, args.random_seed, False)
    on, on_sources = _arm(args.mid_dir, args.random_seed, True)
    moved = int((off_sources[["source_H_ID", "source_P_ID"]].to_numpy()
                 != on_sources[["source_H_ID", "source_P_ID"]].to_numpy()).any(axis=1).sum())
    report = {
        "measurement": "donor_match_passenger_access_off_vs_on",
        "scope_note": "Unweighted raw MiD donor counts (completed donor build), not synthetic-population estimates.",
        "git_commit": args.git_commit or subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=REPO,
                                                                 text=True).strip(),
        "random_seed": args.random_seed,
        "off": off, "on": on, "plan_sources_moved_by_the_key": moved,
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("off", "on")} | {
        "off": {k: v for k, v in off.items() if not k.endswith("report")},
        "on": {k: v for k, v in on.items() if not k.endswith("report")}}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
