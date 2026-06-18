"""Roll up the weekend_plan_match trace and check behavioural convergence.

The weekend-origin cohort received weekday plans, so its trips/person and modal
split must converge to the weekday-origin cohort; large gaps mean the matching
distorted weekday mobility.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd


def resolution_funnel(trace: pd.DataFrame) -> pd.DataFrame:
    out = (trace.groupby("resolution").size().rename("n").reset_index())
    out["share"] = out["n"] / out["n"].sum()
    return out.sort_values("resolution").reset_index(drop=True)


def source_origin_breakdown(trace: pd.DataFrame) -> pd.DataFrame:
    we = trace[trace["donor_day_type"] == "weekend"]
    out = (we.groupby("resolution").size().rename("n").reset_index())
    out["share"] = out["n"] / out["n"].sum() if len(we) else 0.0
    return out.sort_values("resolution").reset_index(drop=True)


def behavioural_sanity(persons: pd.DataFrame, trips: pd.DataFrame) -> pd.DataFrame:
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
    resolution_funnel(trace).to_csv(out_dir / "resolution_funnel.csv", index=False)
    source_origin_breakdown(trace).to_csv(out_dir / "source_origin_breakdown.csv", index=False)
    if persons_path and trips_path:
        persons = pd.read_parquet(persons_path)
        trips = pd.read_parquet(trips_path)
        behavioural_sanity(persons, trips).to_csv(
            out_dir / "behavioural_sanity.csv", index=False)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--trace", required=True)
    ap.add_argument("--persons", default=None)
    ap.add_argument("--trips", default=None)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    main(a.trace, a.persons, a.trips, a.out)
