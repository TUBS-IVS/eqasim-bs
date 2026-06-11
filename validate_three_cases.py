"""Detailed validation + comparability of the three population producers.

Loads each producer's synthesis.output (persons / households / trips / activities)
and reports head-line counts, schema coverage of the unified attributes, key
attribute distributions, and a side-by-side comparability table. Local-only
validation helper for the three-case smoke runs; not part of the pipeline.
"""
from __future__ import annotations

import glob
import os

import pandas as pd

CASES = {
    "simple_ipf_open": "eqasim-data/output_bs",
    "popsim_mid_mini": "eqasim-data/output_mini_popsim_mid",
    "popsim_open_mini": "eqasim-data/output_mini_popsim_open",
}

# Unified attributes that MUST be present + comparable across all three producers.
PERSON_KEYS = [
    "person_id", "household_id", "age", "sex", "employed",
    "has_driving_license", "has_pt_subscription", "socioprofessional_class",
    "is_urban_resident", "census_person_id", "hts_id",
]
HOUSEHOLD_KEYS = [
    "household_id", "household_income", "census_household_id", "hts_household_id",
]


def _find(output_path: str, kind: str) -> str | None:
    hits = glob.glob(os.path.join(output_path, f"*{kind}.csv"))
    return hits[0] if hits else None


def _load(output_path: str, kind: str) -> pd.DataFrame | None:
    path = _find(output_path, kind)
    if path is None:
        return None
    return pd.read_csv(path, sep=";", low_memory=False)


def _share(series: pd.Series) -> str:
    try:
        return f"{100.0 * series.astype(float).mean():.1f}%"
    except Exception:
        return "n/a"


def validate_case(name: str, output_path: str) -> dict:
    report: dict = {"case": name, "output_path": output_path}
    if not os.path.isdir(output_path):
        report["status"] = "OUTPUT DIR MISSING"
        return report
    persons = _load(output_path, "persons")
    households = _load(output_path, "households")
    trips = _load(output_path, "trips")
    activities = _load(output_path, "activities")
    if persons is None:
        report["status"] = "NO persons.csv (run incomplete?)"
        return report
    report["status"] = "ok"
    report["n_persons"] = len(persons)
    report["n_households"] = len(households) if households is not None else 0
    report["n_trips"] = len(trips) if trips is not None else 0
    report["n_activities"] = len(activities) if activities is not None else 0

    report["missing_person_cols"] = [c for c in PERSON_KEYS if c not in persons.columns]
    if households is not None:
        report["missing_household_cols"] = [
            c for c in HOUSEHOLD_KEYS if c not in households.columns
        ]

    # Key distributions (only for columns that exist).
    dist: dict = {}
    if "age" in persons:
        dist["mean_age"] = round(float(persons["age"].astype(float).mean()), 1)
    if "sex" in persons:
        dist["female_share"] = _share(persons["sex"].astype(str).str.lower().isin(["f", "female", "2"]))
    for col in ["employed", "has_driving_license", "has_pt_subscription", "is_urban_resident"]:
        if col in persons:
            dist[f"{col}_share"] = _share(persons[col])
    if "socioprofessional_class" in persons:
        dist["spc_nunique"] = int(persons["socioprofessional_class"].nunique())
        dist["spc_fallback_share"] = _share(
            persons["socioprofessional_class"].astype(str).isin(["5", "5.0", "other"])
        )
    if households is not None and "high_income" in households:
        dist["high_income_share_hh"] = _share(households["high_income"])
    if households is not None and "household_income" in households:
        try:
            dist["mean_hh_income_class"] = round(
                float(households["household_income"].astype(float).mean()), 2
            )
        except Exception:
            pass
    if trips is not None and "mode" in trips:
        modeshare = trips["mode"].value_counts(normalize=True).mul(100).round(1)
        dist["mode_share_pct"] = modeshare.to_dict()
    # Mobility quota (share of persons that leave home = have >=1 trip), the
    # headline KPI validated against MiD 2023 P36_1 (~80% mobile / 19% immobile
    # for the ZGB; see braunschweig.analysis.population_validation.trip_coherence
    # for the per-Kreis comparison). Here we surface the raw share for a quick
    # side-by-side smoke read; the committed reference comparison lives in the
    # population_validation tool.
    if trips is not None and len(persons):
        mobile_ids = set(trips["person_id"].unique())
        n_mobile = persons["person_id"].isin(mobile_ids).sum()
        dist["mobility_rate_share"] = _share(persons["person_id"].isin(mobile_ids))
        dist["immobile_n"] = int(len(persons) - n_mobile)
    report["distributions"] = dist

    # Activity-chain invariant: every person has activities = trips + 1 (per person).
    if activities is not None and trips is not None and len(persons):
        n_act = len(activities)
        n_trips = len(trips)
        # expected: activities == trips + n_persons_with_trips ... report raw for eyeball
        report["activities_minus_trips"] = n_act - n_trips
    return report


def main() -> None:
    reports = [validate_case(name, path) for name, path in CASES.items()]
    print("=" * 78)
    print("THREE-CASE POPULATION VALIDATION")
    print("=" * 78)
    for r in reports:
        print(f"\n### {r['case']}  [{r.get('status')}]")
        if r.get("status") != "ok":
            continue
        print(f"  persons={r['n_persons']:,}  households={r['n_households']:,}  "
              f"trips={r['n_trips']:,}  activities={r['n_activities']:,}")
        if r.get("missing_person_cols"):
            print(f"  !! MISSING person cols: {r['missing_person_cols']}")
        else:
            print("  person schema: all unified keys present")
        if r.get("missing_household_cols"):
            print(f"  !! MISSING household cols: {r['missing_household_cols']}")
        elif "missing_household_cols" in r:
            print("  household schema: all unified keys present")
        for k, v in r.get("distributions", {}).items():
            print(f"    {k}: {v}")
        if "activities_minus_trips" in r:
            print(f"    activities - trips = {r['activities_minus_trips']} "
                  f"(should equal #persons-with-trips)")

    # Side-by-side comparability of headline shares.
    ok = [r for r in reports if r.get("status") == "ok"]
    if len(ok) >= 2:
        print("\n" + "=" * 78)
        print("COMPARABILITY (headline)")
        print("=" * 78)
        rows = ["n_persons", "n_households", "n_trips"]
        share_keys = ["employed_share", "has_driving_license_share",
                      "has_pt_subscription_share", "is_urban_resident_share",
                      "high_income_share_hh", "mean_age",
                      "mobility_rate_share"]  # MiD P36_1 ZGB target ~80% mobile
        header = "metric".ljust(28) + "".join(r["case"].ljust(18) for r in ok)
        print(header)
        for key in rows:
            line = key.ljust(28) + "".join(f"{r.get(key, '-'):,}".ljust(18) for r in ok)
            print(line)
        for key in share_keys:
            line = key.ljust(28) + "".join(
                str(r.get("distributions", {}).get(key, "-")).ljust(18) for r in ok
            )
            print(line)


if __name__ == "__main__":
    main()
