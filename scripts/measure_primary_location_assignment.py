"""Measure the work-location assignment before/after the far-class rank of ADR-0164 on cached run inputs.

eqasim-bs#465: the upstream assignment (``synthesis/population/spatial/primary/locations.py``) hands the sampled
work locations of an origin zone to its persons greedily, so a far location left over at the end of the person
order can go to a person whose donor walked a few hundred metres (person 343377 of the 1 % rerun got 168 km).
This script re-runs ``process_municipality`` offline on the cached inputs of a finished run, once with the
upstream greedy (``far_class_rank`` False) and once with the far-class rank, and compares the assigned home ->
work straight-line distance with the donor commute distance. Work path only, TAZ work-location choice off (the
production configuration).

Two guards make the numbers describe the production code, not a re-implementation:

* the upstream run must reproduce the run's cached assignment for EVERY worker (otherwise the inputs are not the
  ones the run used, and the script fails);
* both runs must give each origin zone the same multiset of locations (the sampled flows are untouched).

Inputs (synpp cache pickles of the run, names as in the run's working directory):

    --candidates        synthesis.population.spatial.primary.candidates__<hash>.p
    --homes             <home locations stage>__<hash>.p (synthesis.population.spatial.home.locations)
    --commute-distance  <commute distance stage>__<hash>.p (synthesis.population.spatial.commute_distance)
    --work-locations    <work locations stage>__<hash>.p (synthesis.locations.work)
    --cached-assignment <primary locations stage>__<hash>.p (the (df_work, df_education) the run used)

Output: one JSON file with the before/after metrics (aggregates only) and the input file names.

Usage (felix, conda env eqasim, from the repository/worktree root):
    python scripts/measure_primary_location_assignment.py --cache-dir <working directory> \
        --candidates ... --homes ... --commute-distance ... --work-locations ... --cached-assignment ... \
        --out-json <scratch>/primary_location_assignment.json --source-commit <short sha of this checkout>
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import pickle
import sys
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import synthesis.population.spatial.primary.locations as locations  # noqa: E402

logger = logging.getLogger(__name__)

#: Straight-line metres of the two distance classes the metrics report: a donor commute at or below
#: SHORT_DONOR_M (the walk bound of ADR-0163) and an assignment beyond FAR_ASSIGNED_M (the portal threshold of
#: ADR-0141, i.e. an outside stay).
SHORT_DONOR_M = 5000.0
FAR_ASSIGNED_M = 45000.0


class _Progress:
    def update(self, n=1):
        pass


class _WorkerContext:
    """The parallel data bag that ``locations.process`` hands to ``process_municipality``."""

    def __init__(self, persons, candidates, far_class_rank, far_class_min_distance_m):
        self._data = dict(df_persons=persons, df_candidates=candidates, origin_zone_col="commune_id",
                          dest_zone_col="commune_id", far_class_rank=far_class_rank,
                          far_class_min_distance_m=far_class_min_distance_m)
        self.progress = _Progress()

    def data(self, name):
        return self._data[name]


def prepare_work_inputs(candidates_stage, homes, commute_distance, work_locations):
    """The persons and candidates frames of the work path, prepared exactly as ``locations.execute`` does."""
    persons = candidates_stage["persons"]
    persons = persons[persons["has_work_trip"]]
    persons = pd.merge(persons, homes[["household_id", "geometry"]].rename(columns={"geometry": "home_location"}),
                       how="left", on="household_id")
    persons = pd.merge(persons, commute_distance["work"], how="left", on="person_id")
    candidates = pd.merge(candidates_stage["work_candidates"], work_locations[["location_id", "geometry"]],
                          how="left", on="location_id")
    if candidates["geometry"].isna().any():
        raise ValueError("%d work candidates have no geometry: the work-locations pickle is not the run's"
                         % int(candidates["geometry"].isna().sum()))
    return persons, gpd.GeoDataFrame(candidates)


def assign(persons, candidates, far_class_rank, far_class_min_distance_m=locations.DEFAULT_FAR_CLASS_MIN_DISTANCE_M):
    """``process_municipality`` over every origin zone, sequentially (the production code, not a copy)."""
    context = _WorkerContext(persons, candidates, far_class_rank, far_class_min_distance_m)
    return pd.concat([locations.process_municipality(context, origin)
                      for origin in candidates["origin_id"].unique()]).sort_index()


def assignment_metrics(persons, result):
    """Assigned vs donor commute distance (km) over the persons with a donor distance; counts over all persons."""
    merged = persons[["person_id", "home_location", "commute_distance"]].merge(
        result[["person_id", "geometry"]], on="person_id", how="inner")
    home = np.array([[point.x, point.y] for point in merged["home_location"]], dtype=float)
    location = np.array([[point.x, point.y] for point in merged["geometry"]], dtype=float)
    assigned_m = np.hypot(location[:, 0] - home[:, 0], location[:, 1] - home[:, 1])
    donor_m = merged["commute_distance"].to_numpy(dtype=float)
    known = np.isfinite(donor_m)
    deviation_km = np.abs(assigned_m - donor_m)[known] / 1000.0
    far = assigned_m > FAR_ASSIGNED_M
    return {
        "n_workers": int(len(merged)),
        "n_without_donor_distance": int((~known).sum()),
        "abs_deviation_km_mean": round(float(deviation_km.mean()), 3),
        "abs_deviation_km_median": round(float(np.median(deviation_km)), 3),
        "abs_deviation_km_p90": round(float(np.quantile(deviation_km, 0.9)), 3),
        "n_donor_le_5km_assigned_gt_45km": int((known & (donor_m <= SHORT_DONOR_M) & far).sum()),
        "n_donor_le_2km_assigned_gt_10km": int((known & (donor_m <= 2000.0) & (assigned_m > 10000.0)).sum()),
        "n_donor_gt_45km": int((donor_m > FAR_ASSIGNED_M).sum()),
        "n_donor_gt_45km_assigned_gt_45km": int(((donor_m > FAR_ASSIGNED_M) & far).sum()),
        "n_assigned_gt_45km": int(far.sum()),
        "pearson_donor_vs_assigned": round(float(np.corrcoef(donor_m[known], assigned_m[known])[0, 1]), 3),
    }


def compare(persons, candidates, cached_work, far_class_min_distance_m=locations.DEFAULT_FAR_CLASS_MIN_DISTANCE_M):
    """Before/after metrics with the two guards of the module docstring (``ValueError`` when one fails)."""
    before = assign(persons, candidates, False)
    check = before.merge(cached_work[["person_id", "location_id"]], on="person_id", how="outer",
                         suffixes=("", "_cached"))
    n_differ = int((check["location_id"] != check["location_id_cached"]).sum())
    if n_differ:
        raise ValueError("the upstream greedy differs from the cached assignment for %d of %d workers: the inputs "
                         "are not the ones the run used" % (n_differ, len(check)))
    after = assign(persons, candidates, True, far_class_min_distance_m)
    # The result's own commune_id column is the DESTINATION zone; the origin comes from the persons.
    origin = persons[["person_id", "commune_id"]].rename(columns={"commune_id": "origin_zone"})
    flows = [result.merge(origin, on="person_id").groupby(["origin_zone", "location_id"]).size().sort_index()
             for result in (before, after)]
    if not flows[0].equals(flows[1]):
        raise ValueError("the far-class rank changed the locations of an origin zone; the flows must be untouched")
    return {"before_upstream_greedy": assignment_metrics(persons, before),
            "after_far_class_rank": assignment_metrics(persons, after),
            "far_class_min_distance_m": far_class_min_distance_m,
            "guards": {"upstream_reproduces_cache": True, "flows_per_origin_identical": True}}


def _load(cache_dir, name):
    with open(os.path.join(cache_dir, name), "rb") as handle:
        return pickle.load(handle)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--cache-dir", required=True)
    for name in ("candidates", "homes", "commute-distance", "work-locations", "cached-assignment"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--far-class-min-distance-m", type=float, default=locations.DEFAULT_FAR_CLASS_MIN_DISTANCE_M)
    parser.add_argument("--out-json", required=True)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    persons, candidates = prepare_work_inputs(
        _load(args.cache_dir, args.candidates), _load(args.cache_dir, args.homes),
        _load(args.cache_dir, args.commute_distance), _load(args.cache_dir, args.work_locations))
    cached_work, _ = _load(args.cache_dir, args.cached_assignment)
    result = compare(persons, candidates, cached_work, args.far_class_min_distance_m)
    result.update({"generated": dt.datetime.now().isoformat(timespec="seconds"), "source_commit": args.source_commit,
                   "cache_dir": args.cache_dir,
                   "inputs": {name: getattr(args, name.replace("-", "_")) for name in
                              ("candidates", "homes", "commute-distance", "work-locations", "cached-assignment")}})
    Path(args.out_json).parent.mkdir(parents=True, exist_ok=True)
    with open(args.out_json, "w", encoding="utf-8") as handle:
        json.dump(result, handle, indent=2)
    logger.info("[measure_primary_location_assignment] before %s", result["before_upstream_greedy"])
    logger.info("[measure_primary_location_assignment] after  %s", result["after_far_class_rank"])
    logger.info("[measure_primary_location_assignment] written to %s", args.out_json)


if __name__ == "__main__":
    main()
