"""Ad-hoc commute-distance breakdown by category.

Categorises each synthetic worker's commute as intra-Kreis, cross-Kreis
inside ZGB, or external (outside ZGB-8) and reports mean distances plus
per-Kreis shares.  Used to diagnose why the overall mean commute diverges
from the MiD P13 reference.
"""

from __future__ import annotations

import pickle
from pathlib import Path

import numpy as np
import pandas as pd

CACHE_ROOT = Path("eqasim-data/cache_bs")
SCOPE = [
    "03101", "03102", "03103", "03151",
    "03153", "03154", "03157", "03158",
]


def _latest(pattern: str) -> Path:
    """Return the newest cache file matching ``pattern`` (by mtime)."""
    return sorted(
        CACHE_ROOT.glob(pattern),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )[0]


def _load(pattern: str):
    """Load a pickled synpp cache, unwrapping the (payload, meta) tuple."""
    with open(_latest(pattern), "rb") as fh:
        obj = pickle.load(fh)
    return obj[0] if isinstance(obj, tuple) else obj


def main() -> None:
    samp = _load("synthesis.population.sampled__*.p")
    homes = _load("synthesis.population.spatial.home.locations__*.p")
    act = pd.DataFrame(_load("synthesis.population.activities__*.p"))
    loc = pd.DataFrame(_load("synthesis.population.spatial.locations__*.p"))

    # Attach home geometry + commune to each person.
    p2home = samp[["person_id", "household_id"]].merge(
        homes[["household_id", "geometry", "commune_id"]]
            .rename(columns={
                "geometry": "home_geom",
                "commune_id": "home_commune",
            }),
        on="household_id", how="left",
    )

    # Join the person-activity table with spatial locations to pull the
    # work activity for each person.
    activities = act[["person_id", "activity_index", "purpose"]]
    locations = loc[["person_id", "activity_index",
                     "commune_id", "geometry"]]
    merged = activities.merge(locations, on=["person_id", "activity_index"])
    work = (
        merged[merged["purpose"] == "work"]
        .drop_duplicates("person_id")[[
            "person_id", "geometry", "commune_id",
        ]]
        .rename(columns={
            "geometry": "work_geom",
            "commune_id": "work_commune",
        })
    )

    df = p2home.merge(work, on="person_id", how="inner")
    df["home_x"] = df["home_geom"].apply(lambda g: g.x)
    df["home_y"] = df["home_geom"].apply(lambda g: g.y)
    df["work_x"] = df["work_geom"].apply(lambda g: g.x)
    df["work_y"] = df["work_geom"].apply(lambda g: g.y)
    df["dist_km"] = np.sqrt(
        (df["home_x"] - df["work_x"]) ** 2
        + (df["home_y"] - df["work_y"]) ** 2
    ) / 1000.0

    df["home_k"] = df["home_commune"].astype(str).str[:5]
    df["work_k"] = df["work_commune"].astype(str).str[:5]
    df["is_ext"] = ~df["work_k"].isin(SCOPE)
    df["cross_k"] = df["home_k"] != df["work_k"]

    print(
        f"n workers: {len(df)}  "
        f"external share: {df['is_ext'].mean():.1%}"
    )

    print("Mean distance by category:")
    categories = [
        ("intra-Kreis", ~df["cross_k"]),
        ("cross-Kreis in-ZGB", df["cross_k"] & ~df["is_ext"]),
        ("external", df["is_ext"]),
    ]
    for name, mask in categories:
        if mask.any():
            mean_km = df.loc[mask, "dist_km"].mean()
            print(f"  {name:20s} n={int(mask.sum()):5d}  mean={mean_km:6.2f} km")

    print("\nPer-Kreis mean / external share:")
    for kreis in SCOPE:
        sub = df[df["home_k"] == kreis]
        if sub.empty:
            continue
        print(
            f"  {kreis}: n={len(sub):4d}  "
            f"mean={sub['dist_km'].mean():5.2f} km  "
            f"ext={sub['is_ext'].mean():5.1%}  "
            f"cross={sub['cross_k'].mean():5.1%}"
        )


if __name__ == "__main__":
    main()
