"""Measure the passenger access key of the plan-source matchers on the raw MiD donor build (ADR-0162).

Runs ``braunschweig.popsim.completed_donor.build_completed_donor`` with the production flags (MiD passenger
availability on) once with ``donor_match_passenger_access`` OFF and once ON, and writes ONLY aggregate counts:

- ``n_foreign``: donor persons whose plan source is another MiD row (weekend match, diary match, fillers);
- ``n_crossed``: of those, the persons whose passenger access class (``P_VAUTO`` 3 "never" or not) differs
  from the class of the donor person whose diary they carry;
- ``n_never_with_foreign_lift``: persons of class "never" whose FOREIGN plan source reports a car-passenger
  trip (the defect of person 903464 of the 1 % rerun of the #465 fixes);
- ``never_foreign_lift_by_source``: ``n_never_with_foreign_lift`` split by the matching step that set the plan
  source last (weekend household alignment, weekend person match, diary re-match, member completion filler) and by
  whether the diary's respondent is of another passenger class (``crossed_class``) or is a "never" respondent
  who reports a lift themselves (``same_class_donor_lift``, the own-answer conflict of ADR-0121 carried over
  to another person);
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
import tempfile
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


#: Weekend-match resolutions grouped into the matching step reported in the residual split.
_WEEKEND_PATHS = {"hh_match": "weekend_hh_alignment", "person_fallback": "weekend_person_match",
                  "mixed_person_sweep": "weekend_person_match",
                  "member_completion_filler": "member_completion_filler"}
RESIDUAL_PATHS = ("weekend_hh_alignment", "weekend_person_match", "diary_rematch", "member_completion_filler",
                  "other")


def _require_row_aligned(persons: pd.DataFrame, trace: pd.DataFrame, what: str) -> None:
    if (len(trace) != len(persons) or not (trace["H_ID"].to_numpy() == persons["H_ID"].to_numpy()).all()
            or not (trace["P_ID"].to_numpy() == persons["P_ID"].to_numpy()).all()):
        raise ValueError(f"split_never_foreign_lift: the {what} trace is not row-aligned with the persons frame")


def split_never_foreign_lift(persons: pd.DataFrame, donor_never: pd.Series, weekend_trace: pd.DataFrame,
                             diary_trace: pd.DataFrame) -> dict:
    """Split the "never" persons with a lift in a FOREIGN plan source by the step that set the source last and
    by the passenger class of the source respondent.

    ``donor_never`` is the passenger class (True = ``P_VAUTO`` 3) of every real respondent, indexed by
    (H_ID, P_ID); both traces must be row-aligned with ``persons`` (the build keeps the row order). The diary
    re-match runs after the weekend match, so a person it re-matched is counted there. A plan source that does
    not resolve to a respondent raises: it would otherwise drop out of both classes unseen.
    """
    from braunschweig.popsim.diary_plan_match import REASONS_REMAP
    from braunschweig.popsim.weekend_plan_match import passenger_never_class

    _require_row_aligned(persons, weekend_trace, "weekend")
    _require_row_aligned(persons, diary_trace, "diary")
    foreign = ~(persons["source_H_ID"].eq(persons["H_ID"]) & persons["source_P_ID"].eq(persons["P_ID"]))
    lift = persons["src_has_car_passenger_trip"].fillna(False).astype(bool)
    defect = (passenger_never_class(persons["P_VAUTO"]) & foreign & lift).to_numpy()
    source_never = donor_never.reindex(pd.MultiIndex.from_arrays(
        [persons["source_H_ID"], persons["source_P_ID"]])).to_numpy()
    unresolved = defect & pd.isna(source_never)
    if unresolved.any():
        raise ValueError(f"split_never_foreign_lift: {int(unresolved.sum())} plan source(s) do not resolve to a "
                         "real respondent")
    path = weekend_trace["resolution"].map(_WEEKEND_PATHS).fillna("other").to_numpy(dtype=object)
    path[diary_trace["reason"].isin(REASONS_REMAP).to_numpy()] = "diary_rematch"
    split = {}
    for name in RESIDUAL_PATHS + ("total",):
        on_path = defect if name == "total" else defect & (path == name)
        same = on_path & (source_never == True)  # noqa: E712 -- object array of bools
        split[name] = {"crossed_class": int(on_path.sum() - same.sum()), "same_class_donor_lift": int(same.sum())}
    return split


def _arm(mid_dir: Path, random_seed: int, passenger_key: bool):
    from braunschweig.popsim import completed_donor as cd
    from braunschweig.popsim.stage import config_keys as keys
    from braunschweig.popsim.weekend_plan_match import passenger_never_class

    with tempfile.TemporaryDirectory() as tmp:
        weekend_trace_path, diary_trace_path = Path(tmp) / "weekend.parquet", Path(tmp) / "diary.parquet"
        result = _build(cd, keys, mid_dir, random_seed, passenger_key, weekend_trace_path, diary_trace_path)
        weekend_trace, diary_trace = pd.read_parquet(weekend_trace_path), pd.read_parquet(diary_trace_path)
    persons = result.persons
    real = persons[~persons["member_imputed"].astype(bool)]
    donor_never = passenger_never_class(real["P_VAUTO"]).set_axis(
        pd.MultiIndex.from_arrays([real["H_ID"], real["P_ID"]]))
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
        "never_foreign_lift_by_source": split_never_foreign_lift(persons, donor_never, weekend_trace, diary_trace),
        "weekend_report": repr(result.weekend_report),
        "diary_report": repr(result.diary_report),
    }
    return counts, persons[["H_ID", "P_ID", "source_H_ID", "source_P_ID"]]


def _build(cd, keys, mid_dir, random_seed, passenger_key, weekend_trace_path, diary_trace_path):
    return cd.build_completed_donor(
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
        trace_path=weekend_trace_path, diary_trace_path=diary_trace_path,
    )


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
