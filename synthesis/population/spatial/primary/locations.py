import logging

import numpy as np
import pandas as pd
import geopandas as gpd
from .candidates import EDUCATION_MAPPING

logger = logging.getLogger(__name__)

#: eqasim-bs#465, ADR-0164: the sampled locations of an origin zone whose straight-line distance from the zone's
#: mean home exceeds FAR_CLASS_MIN_DISTANCE_KEY (metres) go, by rank, to the persons with the longest donor commute
#: distances; the other locations keep the upstream greedy on exact home distances. False restores the upstream
#: greedy over the whole zone (byte-identical).
FAR_CLASS_RANK_KEY = "primary_location_far_class_rank"
DEFAULT_FAR_CLASS_RANK = True
FAR_CLASS_MIN_DISTANCE_KEY = "primary_location_far_class_min_distance_m"
DEFAULT_FAR_CLASS_MIN_DISTANCE_M = 30000.0


def configure_assignment(context):
    """Declare the assignment keys; called from every configure() that runs this module's execute()."""
    context.config(FAR_CLASS_RANK_KEY, DEFAULT_FAR_CLASS_RANK)
    min_distance_m = float(context.config(FAR_CLASS_MIN_DISTANCE_KEY, DEFAULT_FAR_CLASS_MIN_DISTANCE_M))
    if not min_distance_m > 0.0:
        raise ValueError("%s must be > 0 m, got %r" % (FAR_CLASS_MIN_DISTANCE_KEY, min_distance_m))


def configure(context):
    context.stage("synthesis.population.spatial.primary.candidates")
    context.stage("synthesis.population.spatial.commute_distance")
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("synthesis.locations.work")
    context.stage("synthesis.locations.education")

    context.config("education_location_source", "bpe")
    # Declare the flag so execute() can read it without a default (synpp raises
    # if execute reads a config key that was never declared in configure).
    context.config("taz_work_location_choice", False)
    configure_assignment(context)


def define_distance_ordering(df_persons, df_candidates, progress):
    indices = []

    f_available = np.ones((len(df_candidates),), dtype = bool)
    costs = np.ones((len(df_candidates),)) * np.inf

    commute_coordinates = np.vstack([
        df_candidates["geometry"].x.values,
        df_candidates["geometry"].y.values
    ]).T

    for home_coordinate, commute_distance in zip(df_persons["home_location"], df_persons["commute_distance"]):
        home_coordinate = np.array([home_coordinate.x, home_coordinate.y])
        distances = np.sqrt(np.sum((commute_coordinates[f_available] - home_coordinate)**2, axis = 1))
        costs[f_available] = np.abs(distances - commute_distance)

        selected_index = np.argmin(costs)
        indices.append(selected_index)
        f_available[selected_index] = False
        costs[selected_index] = np.inf

        progress.update()

    assert len(set(indices)) == len(df_candidates)

    return indices

def define_random_ordering(df_persons, df_candidates, progress):
    progress.update(len(df_candidates))
    return np.arange(len(df_candidates))

define_ordering = define_distance_ordering


def define_far_class_rank_ordering(df_persons, df_candidates, progress, min_distance_m):
    """Candidate index per person (in ``df_persons`` order): far class by rank, near class by the upstream greedy.

    The far class is the set of candidates farther than ``min_distance_m`` (straight line) from the mean home of
    the zone's persons; its size is fixed by the sampled flows. The same number of persons with the LONGEST donor
    commute distances form the far class of persons, and the two are paired by rank (shortest wanted distance with
    the nearest far candidate). ASSUMPTION: beyond ``min_distance_m`` the spread of the homes inside one zone is
    small against the distances, so the mean home stands for every home (the measured choice of 30 km is in
    ADR-0164). All other persons take the remaining candidates through ``define_distance_ordering`` on their exact
    home distances, in the input order. A missing donor distance (NaN) takes the zone's median donor distance
    (ASSUMPTION, counted by ``log_assignment_quality``; 0 when the zone has no donor distance at all). Returns a
    list of indices into ``df_candidates`` like ``define_distance_ordering``.
    """
    home = np.array([[point.x, point.y] for point in df_persons["home_location"]], dtype=float)
    centre = home.mean(axis=0)
    candidate_distance = np.hypot(df_candidates["geometry"].x.values - centre[0],
                                  df_candidates["geometry"].y.values - centre[1])
    far_candidates = np.flatnonzero(candidate_distance > min_distance_m)

    desired = df_persons["commute_distance"].to_numpy(dtype=float)
    finite = np.isfinite(desired)
    desired = np.where(finite, desired, np.median(desired[finite]) if finite.any() else 0.0)
    far_persons = np.zeros(len(df_persons), dtype=bool)
    far_persons[np.argsort(-desired, kind="mergesort")[:len(far_candidates)]] = True

    indices = np.empty(len(df_persons), dtype=int)
    far_rows = np.flatnonzero(far_persons)
    by_wanted = far_rows[np.argsort(desired[far_rows], kind="mergesort")]
    indices[by_wanted] = far_candidates[np.argsort(candidate_distance[far_candidates], kind="mergesort")]
    progress.update(len(far_rows))

    near_rows = np.flatnonzero(~far_persons)
    if len(near_rows):
        near_candidates = np.flatnonzero(candidate_distance <= min_distance_m)
        near_persons = df_persons.iloc[near_rows].assign(commute_distance=desired[near_rows])
        local = define_distance_ordering(near_persons, df_candidates.iloc[near_candidates], progress)
        indices[near_rows] = near_candidates[local]
    return indices.tolist()

def process_municipality(context, origin_id):
    """Assign candidate work/education locations to persons in one origin zone.

    The origin zone is identified by *origin_id*, which may be a commune_id
    (education path, OFF path) or a taz_id (WORK path when TAZ flag is ON).
    ``origin_zone_col`` and ``zone_key`` are read from the parallel data bag so
    the function remains a single-argument parallel worker.
    """
    # Load data
    df_candidates = context.data("df_candidates")
    df_persons = context.data("df_persons")
    origin_zone_col = context.data("origin_zone_col")
    dest_zone_col = context.data("dest_zone_col")

    # Find relevant records
    # Filter persons by the origin zone column (commune_id or home_taz_id).
    df_persons = df_persons[df_persons[origin_zone_col] == origin_id][[
        "person_id", "home_location", "commute_distance"
    ]].copy()
    df_candidates = df_candidates[df_candidates["origin_id"] == origin_id]

    # From previous step, this should be equal!
    assert len(df_persons) == len(df_candidates)

    if context.data("far_class_rank"):
        indices = define_far_class_rank_ordering(df_persons, df_candidates, context.progress,
                                                 context.data("far_class_min_distance_m"))
    else:
        indices = define_ordering(df_persons, df_candidates, context.progress)
    df_candidates = df_candidates.iloc[indices]

    df_candidates["person_id"] = df_persons["person_id"].values
    # Rename destination_id to the DESTINATION zone name so the returned frame is
    # clearly named: "commune_id" for education/OFF (byte-identical), and
    # "work_taz_id" for WORK ON -- the column holds the assigned WORK destination
    # TAZ, so it must NOT reuse the home/origin name "home_taz_id".
    df_candidates = df_candidates.rename(columns={"destination_id": dest_zone_col})

    return df_candidates[["person_id", dest_zone_col, "location_id", "geometry"]]

def process(context, purpose, df_persons, df_candidates, origin_zone_col="commune_id", dest_zone_col=None,
            far_class_rank=False, far_class_min_distance_m=DEFAULT_FAR_CLASS_MIN_DISTANCE_M):
    """Assign candidate locations to persons by minimising commute-distance deviation.

    Parameters
    ----------
    origin_zone_col:
        Column in *df_persons* holding the origin zone key used to group
        persons (``"commune_id"`` for education / OFF; ``"home_taz_id"`` for
        WORK ON path).
    dest_zone_col:
        Name given to the assigned DESTINATION zone column on the returned frame
        (the renamed ``destination_id``). ``None`` -> reuse ``origin_zone_col``,
        which reproduces the legacy naming for the education/OFF path
        (byte-identical). The WORK ON path passes ``"work_taz_id"`` so the
        destination column is not mislabelled as the home/origin TAZ.
    far_class_rank, far_class_min_distance_m:
        ADR-0164: assign the far class of each zone by rank (see
        ``define_far_class_rank_ordering``); False is the upstream greedy.
    """
    if dest_zone_col is None:
        dest_zone_col = origin_zone_col
    unique_ids = df_candidates["origin_id"].unique()

    df_result = []

    with context.progress(label="Distributing %s destinations" % purpose, total=len(df_persons)) as progress:
        with context.parallel(dict(df_persons=df_persons, df_candidates=df_candidates,
                                   origin_zone_col=origin_zone_col,
                                   dest_zone_col=dest_zone_col, far_class_rank=far_class_rank,
                                   far_class_min_distance_m=far_class_min_distance_m)) as parallel:
            for df_partial in parallel.imap_unordered(process_municipality, unique_ids):
                df_result.append(df_partial)

    df_result = pd.concat(df_result).sort_index()
    log_assignment_quality(purpose, df_persons, df_result, far_class_rank, far_class_min_distance_m)
    return df_result


def log_assignment_quality(purpose, df_persons, df_result, far_class_rank, far_class_min_distance_m):
    """Log how far the assigned distances lie from the donor commute distances (ADR-0164 observability).

    Counts the persons without a donor distance (the zone median stands in on the far-class path, an arbitrary
    remaining candidate on the upstream path) and the persons whose assigned location lies beyond the far-class
    distance while their donor distance is below a tenth of it; reports the median and the 90th percentile of the
    absolute deviation in km. Returns these numbers as a dict.
    """
    merged = df_persons[["person_id", "home_location", "commute_distance"]].merge(
        df_result[["person_id", "geometry"]], on="person_id", how="inner")
    if len(merged) == 0:
        return {}
    # The merge onto the plain persons frame leaves plain object columns, so the coordinates are read per point.
    home = np.array([[point.x, point.y] for point in merged["home_location"]], dtype=float)
    location = np.array([[point.x, point.y] for point in merged["geometry"]], dtype=float)
    assigned = np.hypot(location[:, 0] - home[:, 0], location[:, 1] - home[:, 1])
    desired = merged["commute_distance"].to_numpy(dtype=float)
    finite = np.isfinite(desired)
    deviation_km = np.abs(assigned[finite] - desired[finite]) / 1000.0
    short_to_far = int((finite & (desired < 0.1 * far_class_min_distance_m)
                        & (assigned > far_class_min_distance_m)).sum())
    quality = {"n_persons": len(merged), "n_without_donor_distance": int((~finite).sum()),
               "median_abs_deviation_km": float(np.median(deviation_km)) if len(deviation_km) else float("nan"),
               "p90_abs_deviation_km": float(np.quantile(deviation_km, 0.9)) if len(deviation_km) else float("nan"),
               "n_short_donor_assigned_far": short_to_far}
    logger.info("[primary locations] %s (%s): %d persons, without donor distance %d (%.1f%%); |assigned - donor| "
                "median %.2f km, p90 %.2f km; donor below %.0f m but assigned beyond %.0f m: %d (%.2f%%)", purpose,
                "far-class rank" if far_class_rank else "upstream greedy", quality["n_persons"],
                quality["n_without_donor_distance"], 100.0 * quality["n_without_donor_distance"] / len(merged),
                quality["median_abs_deviation_km"], quality["p90_abs_deviation_km"], 0.1 * far_class_min_distance_m,
                far_class_min_distance_m, short_to_far, 100.0 * short_to_far / len(merged))
    return quality

def execute(context):
    # Read the flag (no default here; the default is declared in configure()).
    taz_on = context.config("taz_work_location_choice")
    assignment = dict(far_class_rank=bool(context.config(FAR_CLASS_RANK_KEY)),
                      far_class_min_distance_m=float(context.config(FAR_CLASS_MIN_DISTANCE_KEY)))

    data = context.stage("synthesis.population.spatial.primary.candidates")
    df_persons = data["persons"]

    # Separate data set
    df_work = df_persons[df_persons["has_work_trip"]]
    df_education = df_persons[df_persons["has_education_trip"]]

    # Attach home locations (already staged unconditionally in configure()).
    df_home = context.stage("synthesis.population.spatial.home.locations")

    df_work = pd.merge(df_work, df_home[["household_id", "geometry"]].rename(columns={
        "geometry": "home_location"
    }), how="left", on="household_id")

    df_education = pd.merge(df_education, df_home[["household_id", "geometry"]].rename(columns={
        "geometry": "home_location"
    }), how="left", on="household_id")

    # Attach commute distances
    df_commute_distance = context.stage("synthesis.population.spatial.commute_distance")

    df_work = pd.merge(df_work, df_commute_distance["work"], how="left", on="person_id")
    df_education = pd.merge(df_education, df_commute_distance["education"], how="left", on="person_id")

    # Attach geometry to work candidates
    df_locations = context.stage("synthesis.locations.work")[["location_id", "geometry"]]
    df_work_candidates = data["work_candidates"]
    df_work_candidates = pd.merge(df_work_candidates, df_locations, how="left", on="location_id")
    df_work_candidates = gpd.GeoDataFrame(df_work_candidates)

    # Attach geometry to education candidates
    df_locations = context.stage("synthesis.locations.education")[["education_type", "location_id", "geometry"]]
    df_education_candidates = data["education_candidates"]
    df_education_candidates = pd.merge(df_education_candidates, df_locations, how="left", on="location_id")
    df_education_candidates = gpd.GeoDataFrame(df_education_candidates)

    # Assign destinations.
    # WORK: when TAZ is ON, group persons by home_taz_id so the equal-count
    # assertion in process_municipality holds on the taz_id key (both persons and
    # candidates are keyed the same way).  Education is always commune_id.  The
    # returned DESTINATION column is named work_taz_id on the ON path so it is not
    # mislabelled as the home/origin TAZ; OFF keeps the legacy commune_id name.
    work_origin_col = "home_taz_id" if taz_on else "commune_id"
    work_dest_col = "work_taz_id" if taz_on else "commune_id"
    df_work = process(context, "work", df_work, df_work_candidates,
                      origin_zone_col=work_origin_col, dest_zone_col=work_dest_col, **assignment)

    if context.config("education_location_source") == 'bpe':
        df_education = process(context, "education", df_education, df_education_candidates,
                               origin_zone_col="commune_id", **assignment)
    else:
        education = []
        for prefix, education_type in EDUCATION_MAPPING.items():
            education.append(
                process(context, prefix,
                        df_education[df_education["age_range"] == prefix],
                        df_education_candidates[df_education_candidates["education_type"].isin(education_type)],
                        origin_zone_col="commune_id", **assignment))
        df_education = pd.concat(education).sort_index()

    return df_work, df_education
