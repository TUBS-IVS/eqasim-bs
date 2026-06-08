"""Standalone trip-coherence measurement for one eqasim run output directory.

Loads <prefix>persons.csv + <prefix>trips.csv (+ households for the household_size
segment) and prints the trip-coherence report (purpose distribution vs MiD W1,
mobility rate vs MiD P36_1, segmented by employed / is_urban_resident /
household_size). Used to compare the legacy matching keys against the richer keys
of the HTS-matching optimization (step 2 measurement).

Usage:
    python scripts/measure_trip_coherence.py --output-dir eqasim-data/output_bs_25pct_allfeat
"""
from __future__ import annotations

import argparse
import glob
import os
import sys

import pandas as pd

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
DATA_PATH = os.path.join(REPO, "eqasim-data", "data")

from braunschweig.analysis.population_validation import trip_coherence as TC  # noqa: E402


def _detect_prefix(directory: str) -> str:
    matches = sorted(glob.glob(os.path.join(directory, "*persons.csv")))
    if not matches:
        raise FileNotFoundError(f"No *persons.csv in {directory}")
    return os.path.basename(matches[0])[: -len("persons.csv")]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--label", default=None)
    ns = ap.parse_args(argv)

    prefix = _detect_prefix(ns.output_dir)
    label = ns.label or prefix.rstrip("_")
    persons = pd.read_csv(os.path.join(ns.output_dir, f"{prefix}persons.csv"), sep=";")
    trips = pd.read_csv(os.path.join(ns.output_dir, f"{prefix}trips.csv"), sep=";")
    hh_path = os.path.join(ns.output_dir, f"{prefix}households.csv")
    if os.path.exists(hh_path) and "household_size" not in persons.columns:
        hh = pd.read_csv(hh_path, sep=";")
        if "household_size" in hh.columns:
            persons = persons.merge(
                hh[["household_id", "household_size"]], on="household_id", how="left")

    report = TC.build_trip_coherence_report(persons, trips, DATA_PATH)

    print(f"\n=== Trip coherence: {label} ===")
    print(f"persons={report['n_persons']:,}  trips={report['n_trips']:,}")
    m = report["mobility"]
    print(f"\nMobility rate: {100*m['overall_rate']:.1f}%  "
          f"(MiD P36_1 {100*m['target_rate']:.1f}%, |delta| {100*m['abs_delta']:.1f} pp)")
    pur = report["purpose"]
    print(f"\nPurpose distribution vs MiD W1 (SRMSE {pur['srmse']:.3f}):")
    print(f"  {'purpose':<12}{'realised':>10}{'W1':>8}{'|delta| pp':>12}")
    for p in pur["target"]:
        print(f"  {p:<12}{100*pur['realized'].get(p, float('nan')):>9.1f}%"
              f"{100*pur['target'][p]:>7.1f}%{pur['abs_delta_pp'][p]:>11.1f}")
    gap = report["differentiation"]["work_share_employed_gap_pp"]
    print(f"\n[KPI] work-trip participation gap employed - not-employed: {gap:.1f} pp "
          "(higher = matching gives employed persons commute diaries)")

    def _show_segment(df, value_col, fmt, only=None):
        if df.empty:
            return
        for _, r in df.iterrows():
            if only is not None and r["segment"] not in only:
                continue
            if int(r["n_persons"]) < 200:        # hide tiny noisy group-quarter cells
                continue
            print(f"  {r['segment']:<18}{str(r['segment_value']):<10}"
                  f"n={int(r['n_persons']):>9,}  {fmt}={value_col(r)}")

    keep = {"employed", "is_urban_resident"}
    print("\nWork-trip participation by segment:")
    _show_segment(report["work_participation_by_segment"],
                  lambda r: f"{100*r['participation_rate']:.1f}%", "work", only=keep)
    print("\nTrips per person by segment:")
    _show_segment(report["trips_per_person_by_segment"],
                  lambda r: f"{r['trips_per_person']:.2f}", "trips", only=keep)
    print("\nMobility rate by segment:")
    _show_segment(report["mobility_by_segment"],
                  lambda r: f"{100*r['mobility_rate']:.1f}%", "mobile", only=keep)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
