"""Realised effect of the passive escort surrogate anchor (issue #409 option 1, ADR-0124):
ON vs OFF, per rescued child activity.

Reads THREE completed runs of the chainsolver stage over the SAME upstream population --
the OFF arm (``escort_passive_joint_surrogate: false``), the STRICT arm (flag on,
``escort_passive_joint_surrogate_require_same_purpose: true``) and the RELAXED arm (flag on,
that rule false) -- rebuilds each ON arm's surrogate link table from the shared persons and
trips frames with that arm's flag values, and measures for every rescued child activity:

* ``dist_child_adult_m``: distance between the child's placed location and the surrogate's
  placed location (ON: 0 by construction, the check that the anchor took; OFF: the distance
  the independent draw produced);
* ``dist_home_m``: distance between the child's household home and the child's placed
  location (the realised trip length the issue asks to compare).

No reference exists for either quantity; both are DESCRIPTIVE. Departure times and escort
participation are untouched by construction (the trips frame is one cached object shared by
all arms), which this script states rather than measures. Distances in metres in the
pipeline CRS (EPSG:25832); never computed in geographic coordinates.

Run on the server (paths are the three arms' stage pickles; see the run manifest):

    python scripts/measure_passive_joint_surrogate_effect.py \
        --cache ~/i409_runs/cache_strict \
        --off    ~/i409_runs/cache_off/braunschweig.synthesis.locations.secondary_chainsolvers__<hash>.p \
        --strict ~/i409_runs/cache_strict/braunschweig.synthesis.locations.secondary_chainsolvers__<hash>.p \
        --relaxed ~/i409_runs/cache_relaxed/braunschweig.synthesis.locations.secondary_chainsolvers__<hash>.p \
        --out ~/i409_effect

RESULT: filled in by Task 8 of the plan from the run's own output (never quoted from chat).
"""
from __future__ import annotations

import argparse
import logging
import os
import pickle
import sys

import geopandas as gpd
import pandas as pd

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from braunschweig.synthesis.locations.passive_joint_links import (   # noqa: E402
    DEFAULT_SURROGATE_MAX_GAP_MINUTES, DEFAULT_SURROGATE_MIN_AGE_YEARS,
    build_passive_joint_links, rescue_with_surrogates,
)
from scripts.measure_passive_joint_surrogate_adults import (         # noqa: E402
    PERSONS_STAGE, TRIPS_STAGE, load_stage,
)

logger = logging.getLogger(__name__)

#: synpp stage aliased to ``synthesis.population.spatial.home.locations`` in this model; its
#: output honours the upstream eqasim contract consumed by
#: ``synthesis/population/spatial/locations.py``: one row per household with ``household_id``
#: and a point ``geometry``.
HOME_STAGE = "braunschweig.synthesis.locations.home_cell"

EFFECT_COLUMNS = ["arm", "child_person_id", "child_activity_index", "child_purpose",
                  "adult_person_id", "adult_activity_index", "gap_minutes",
                  "dist_child_adult_m_arm", "dist_child_adult_m_off",
                  "dist_home_m_arm", "dist_home_m_off"]
_DISTANCE_COLUMNS = EFFECT_COLUMNS[7:]
_LOG_TAG = "[i409 effect]"


def _placed(locations: gpd.GeoDataFrame, person_ids, activity_indices, *, what: str
            ) -> gpd.GeoSeries:
    """The placed geometry of each (person, activity); raises naming the first miss."""
    index = pd.MultiIndex.from_arrays([locations["person_id"].astype("int64"),
                                       locations["activity_index"].astype("int64")])
    placed = gpd.GeoSeries(locations["geometry"].to_numpy(), index=index, crs=locations.crs)
    placed = placed[~placed.index.duplicated(keep="first")]
    wanted = pd.MultiIndex.from_arrays([pd.Index(person_ids).astype("int64"),
                                        pd.Index(activity_indices).astype("int64")])
    out = placed.reindex(wanted)
    if out.isna().any():
        first = wanted[out.isna().to_numpy()][0]
        raise ValueError(f"{_LOG_TAG} {what}: person {first[0]} activity {first[1]} has no "
                         "placed location in the stage output; the link table and the "
                         "locations frame must come from the same run.")
    return gpd.GeoSeries(out.to_numpy(), crs=locations.crs)


def surrogate_effect_rows(arm: str, surrogate_links: pd.DataFrame,
                          locations_arm: gpd.GeoDataFrame, locations_off: gpd.GeoDataFrame,
                          homes: gpd.GeoDataFrame, persons: pd.DataFrame,
                          child_purposes: pd.DataFrame) -> pd.DataFrame:
    """One row per surrogate link with the four realised distances (metres). Pure."""
    if len(surrogate_links) == 0:
        return pd.DataFrame(columns=EFFECT_COLUMNS)
    links = surrogate_links.copy()
    links["arm"] = arm
    purposes = child_purposes.assign(child_activity_index=child_purposes["trip_index"] + 1)[
        ["person_id", "child_activity_index", "following_purpose"]].rename(
        columns={"person_id": "child_person_id", "following_purpose": "child_purpose"})
    links = links.merge(purposes, on=["child_person_id", "child_activity_index"], how="left")

    child_arm = _placed(locations_arm, links["child_person_id"], links["child_activity_index"],
                        what=f"{arm} arm, child")
    adult_arm = _placed(locations_arm, links["adult_person_id"], links["adult_activity_index"],
                        what=f"{arm} arm, surrogate")
    child_off = _placed(locations_off, links["child_person_id"], links["child_activity_index"],
                        what="OFF arm, child")
    adult_off = _placed(locations_off, links["adult_person_id"], links["adult_activity_index"],
                        what="OFF arm, surrogate")

    households = persons[["person_id", "household_id"]].drop_duplicates("person_id").rename(
        columns={"person_id": "child_person_id"})
    links = links.merge(households, on="child_person_id", how="left")
    home_by_household = gpd.GeoSeries(homes["geometry"].to_numpy(),
                                      index=homes["household_id"].astype("int64"), crs=homes.crs)
    home = home_by_household.reindex(links["household_id"].astype("int64"))
    if home.isna().any():
        raise ValueError(f"{_LOG_TAG} a rescued child's household has no home location.")
    home = gpd.GeoSeries(home.to_numpy(), crs=homes.crs)

    links["dist_child_adult_m_arm"] = child_arm.distance(adult_arm, align=False).to_numpy()
    links["dist_child_adult_m_off"] = child_off.distance(adult_off, align=False).to_numpy()
    links["dist_home_m_arm"] = home.distance(child_arm, align=False).to_numpy()
    links["dist_home_m_off"] = home.distance(child_off, align=False).to_numpy()
    return links[EFFECT_COLUMNS].reset_index(drop=True)


def summarise_effect(rows: pd.DataFrame) -> pd.DataFrame:
    """Per arm and child purpose: n plus mean and median of every distance column."""
    if len(rows) == 0:
        return pd.DataFrame(columns=["arm", "child_purpose", "n"])
    grouped = rows.groupby(["arm", "child_purpose"], sort=True)
    summary = grouped.size().rename("n").reset_index()
    for column in _DISTANCE_COLUMNS:
        summary[f"{column}_mean"] = grouped[column].mean().to_numpy()
        summary[f"{column}_median"] = grouped[column].median().to_numpy()
    return summary


def _load_locations(path: str) -> gpd.GeoDataFrame:
    with open(path, "rb") as handle:
        payload = pickle.load(handle)
    locations = payload[0] if isinstance(payload, tuple) else payload
    if not isinstance(locations, gpd.GeoDataFrame):
        raise TypeError(f"{_LOG_TAG} {path} does not hold a GeoDataFrame stage output.")
    return locations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--cache", required=True,
                        help="a working directory holding the SHARED upstream stages "
                             "(persons, trips, home locations) of all three arms")
    parser.add_argument("--off", required=True, help="OFF arm chainsolver stage pickle")
    parser.add_argument("--strict", required=True, help="STRICT arm chainsolver stage pickle")
    parser.add_argument("--relaxed", required=True, help="RELAXED arm chainsolver stage pickle")
    parser.add_argument("--out", required=True)
    parser.add_argument("--max-gap-minutes", type=float, default=DEFAULT_SURROGATE_MAX_GAP_MINUTES)
    parser.add_argument("--min-age-years", type=int, default=DEFAULT_SURROGATE_MIN_AGE_YEARS)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    persons = load_stage(args.cache, PERSONS_STAGE)
    trips = load_stage(args.cache, TRIPS_STAGE)
    homes = load_stage(args.cache, HOME_STAGE)
    identity, identity_stats = build_passive_joint_links(persons, trips)
    print(f"{_LOG_TAG} identity links {identity_stats['n_linked']}/{identity_stats['n_passive_paired']}")

    locations_off = _load_locations(args.off)
    all_rows = []
    for arm, path, same_purpose in (("strict", args.strict, True), ("relaxed", args.relaxed, False)):
        surrogate_links, stats = rescue_with_surrogates(
            identity, persons, trips, max_gap_minutes=args.max_gap_minutes,
            min_age_years=args.min_age_years, require_same_purpose=same_purpose)
        print(f"{_LOG_TAG} {arm}: surrogate links {stats['n_surrogate_linked']}/"
              f"{stats['n_rescue_candidates']} (rate {100.0 * stats['rescue_rate']:.1f}%)")
        rows = surrogate_effect_rows(arm, surrogate_links, _load_locations(path), locations_off,
                                     homes, persons, trips[["person_id", "trip_index", "following_purpose"]])
        all_rows.append(rows)
    rows = pd.concat(all_rows, ignore_index=True)
    summary = summarise_effect(rows)
    print("\n--- realised effect per arm and child purpose (metres) ---")
    print(summary.to_string(index=False) if len(summary) else "(no surrogate links)")
    print("\nDeparture times and escort participation are untouched by construction: all arms "
          "read the same cached trips frame (asserted: one pickle per stage in --cache).")

    os.makedirs(args.out, exist_ok=True)
    rows.to_csv(os.path.join(args.out, "surrogate_effect_rows.csv"), index=False)
    summary.to_csv(os.path.join(args.out, "surrogate_effect_summary.csv"), index=False)
    print(f"\nwrote {args.out}/surrogate_effect_rows.csv and surrogate_effect_summary.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
