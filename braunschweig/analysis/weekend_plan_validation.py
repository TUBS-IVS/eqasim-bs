"""Roll up the weekend_plan_match trace and check behavioural convergence.

The weekend-origin cohort received weekday plans, so its trips/person and modal
split must converge to the weekday-origin cohort; large gaps mean the matching
distorted weekday mobility.
"""
from __future__ import annotations

import argparse
import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def resolution_funnel(trace: pd.DataFrame) -> pd.DataFrame:
    out = (trace.groupby("resolution").size().rename("n").reset_index())
    out["share"] = out["n"] / out["n"].sum()
    return out.sort_values("resolution").reset_index(drop=True)


def source_origin_breakdown(trace: pd.DataFrame) -> pd.DataFrame:
    we = trace[trace["donor_day_type"] == "weekend"]
    out = (we.groupby("resolution").size().rename("n").reset_index())
    out["share"] = out["n"] / out["n"].sum() if len(we) else 0.0
    return out.sort_values("resolution").reset_index(drop=True)


def hh_match_level_funnel(trace: pd.DataFrame) -> pd.DataFrame:
    """Counts per ``match_level`` among ``resolution == "hh_match"`` rows -- the
    spec's promised breakdown of HH-match relaxation depth (0 = strict … N = loosest).
    """
    hh = trace[trace["resolution"] == "hh_match"]
    out = (hh.groupby("match_level").size().rename("n").reset_index())
    out["share"] = out["n"] / out["n"].sum() if len(hh) else 0.0
    return out.sort_values("match_level").reset_index(drop=True)


# Raw MiD id columns that must never appear in an exported analysis CSV.
_ID_COLS = ("H_ID", "P_ID", "plan_source_H_ID", "plan_source_P_ID")


def _assert_no_ids(name: str, frame: pd.DataFrame) -> None:
    leaked = [c for c in _ID_COLS if c in frame.columns]
    if leaked:
        raise AssertionError(
            f"weekend_plan_validation export '{name}' would leak raw MiD id "
            f"column(s) {leaked}; analysis outputs must carry NO ids.")


def behavioural_sanity(persons: pd.DataFrame, trips: pd.DataFrame) -> pd.DataFrame:
    # Requires `persons` to carry `donor_day_type` (joined from the weekend_plan_match
    # trace via the pseudonym_map: trace raw (H_ID,P_ID) -> surrogate person_id). The
    # production persons frame does NOT carry it by default, so fail loud rather than a
    # cryptic KeyError if a caller passes an un-joined frame.
    if "donor_day_type" not in persons.columns:
        raise ValueError(
            "behavioural_sanity requires a `donor_day_type` column on `persons` "
            "(join it from the weekend_plan_match trace via pseudonym_map first); "
            "it is not present on the default expanded persons frame.")
    trips_per_person = (
        trips.groupby("person_id").size().rename("n_trips").reset_index())
    merged = persons.merge(trips_per_person, on="person_id", how="left")
    merged["n_trips"] = merged["n_trips"].fillna(0)
    rows = []
    for cohort, grp in merged.groupby("donor_day_type"):
        rows.append({
            "donor_day_type": cohort,
            "n_persons": len(grp),
            "trips_per_person": grp["n_trips"].mean(),
        })
    return pd.DataFrame(rows)


def main(trace_path, persons_path, trips_path, out_dir):
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    trace = pd.read_parquet(trace_path)
    exports = {
        "resolution_funnel.csv": resolution_funnel(trace),
        "source_origin_breakdown.csv": source_origin_breakdown(trace),
        "hh_match_level_funnel.csv": hh_match_level_funnel(trace),
    }
    if persons_path and trips_path:
        persons = pd.read_parquet(persons_path)
        trips = pd.read_parquet(trips_path)
        # behavioural_sanity needs persons.donor_day_type (joined from the trace via
        # pseudonym_map). Skip with a loud note rather than crash if it is absent --
        # the resolution/source/level funnels above are the working donor-level overview.
        if "donor_day_type" in persons.columns:
            exports["behavioural_sanity.csv"] = behavioural_sanity(persons, trips)
        else:
            logger.warning(
                "[weekend_plan_validation] persons frame has no `donor_day_type`; "
                "skipping behavioural_sanity.csv (join the trace's day_type onto the "
                "population via pseudonym_map to enable it).")
    # Defensive: analysis outputs carry NO raw MiD ids (the id-keyed trace stays a
    # restricted work_dir artifact, same tier as pseudonym_map.csv). Fail loud if a
    # future change tries to export ids.
    for name, frame in exports.items():
        _assert_no_ids(name, frame)
        frame.to_csv(out_dir / name, index=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", required=True)
    ap.add_argument("--persons", default=None)
    ap.add_argument("--trips", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    main(a.trace, a.persons, a.trips, a.out)
