import pandas as pd
import numpy as np
import geopandas as gpd


def _filter_pool_by_zone(pool: pd.DataFrame, zone_value, zone_key: str = "commune_id") -> pd.DataFrame:
    """Return rows of *pool* whose *zone_key* column equals *zone_value*.

    This is the single filtering point for both the commune_id (education,
    OFF-path) and taz_id (WORK ON-path) code paths so the logic stays in
    one testable place.

    Parameters
    ----------
    pool:
        DataFrame of candidate locations; must contain *zone_key* as a column.
    zone_value:
        The zone identifier to select (commune_id string or taz_id string).
    zone_key:
        Column name to filter on; defaults to ``"commune_id"`` to preserve
        the existing education / OFF behaviour byte-identically.

    Raises
    ------
    KeyError
        If *zone_key* is not a column in *pool*.
    """
    if zone_key not in pool.columns:
        raise KeyError(
            "zone_key %r not found in pool columns %s" % (zone_key, list(pool.columns))
        )
    return pool[pool[zone_key] == zone_value]


def configure(context):
    context.stage("data.od.weighted")

    context.stage("synthesis.locations.education")
    context.stage("synthesis.locations.work")

    context.stage("synthesis.population.spatial.home.zones")
    context.stage("synthesis.population.enriched")
    context.stage("synthesis.population.trips")

    context.config("output_path")
    context.config("random_seed")
    context.config("education_location_source", "bpe")

    # When the TAZ work-location-choice feature is ON, this stage also needs
    # the home POINT (geometry) to PIP each household to its TAZ, and the TAZ
    # polygons themselves.  These deps are gated so the OFF path (all existing
    # configs) does not pull in any new stages.
    if context.config("taz_work_location_choice", False):
        context.stage("synthesis.population.spatial.home.locations")
        context.stage("braunschweig.data.spatial.taz")

EDUCATION_MAPPING = {
    "primary_school": ["C1"],
    "middle_school": ["C2"],
    "high_school": ["C3"],
    "higher_education": ["C4", "C5", "C6"]}

def sample_destination_municipalities(context, arguments):
    # Load data
    origin_id, count, random_seed = arguments
    df_od = context.data("df_od")

    # Prepare state
    random = np.random.RandomState(random_seed)
    df_od = df_od[df_od["origin_id"] == origin_id].copy()

    # Sample destinations
    df_od["count"] = random.multinomial(count, df_od["weight"].values)
    df_od = df_od[df_od["count"] > 0]

    context.progress.update()
    return df_od[["origin_id", "destination_id", "count"]]

def sample_locations(context, arguments):
    # Load data
    destination_id, random_seed = arguments
    df_locations, df_flow, zone_key = (
        context.data("df_locations"),
        context.data("df_flow"),
        context.data("zone_key"),
    )

    # Prepare state
    random = np.random.RandomState(random_seed)
    # Filter the candidate pool by the per-call zone key (commune_id for education
    # and the OFF path; taz_id for WORK when the TAZ flag is ON).
    df_locations = _filter_pool_by_zone(df_locations, destination_id, zone_key=zone_key)

    # Determine demand
    df_flow = df_flow[df_flow["destination_id"] == destination_id]
    count = df_flow["count"].sum()

    # Sample destinations
    weight = np.ones((len(df_locations),)) / len(df_locations)

    if "weight" in df_locations:
        weight = df_locations["weight"].values / df_locations["weight"].sum()
    
    location_counts = random.multinomial(count, weight)
    location_ids = df_locations["location_id"].values
    location_ids = np.repeat(location_ids, location_counts)

    # Shuffle, as otherwise it is likely that *all* copies 
    # of the first location id go to the first origin, and so on
    random.shuffle(location_ids)

    # Construct a data set for all commutes to this zone
    origin_id = np.repeat(df_flow["origin_id"].values, df_flow["count"].values)

    df_result = pd.DataFrame.from_records(dict(
        origin_id = origin_id,
        location_id = location_ids
    ))
    df_result["destination_id"] = destination_id

    return df_result

def process(context, purpose, random, df_persons, df_od, df_locations, step_name,
            origin_zone_col="commune_id", zone_key="commune_id"):
    """Sample work or education candidates via the gravity OD.

    Parameters
    ----------
    origin_zone_col:
        Column in *df_persons* used to group origin demand (``"commune_id"``
        for education / OFF path; ``"home_taz_id"`` for WORK ON path).
    zone_key:
        Column in *df_locations* used to filter the candidate pool per
        destination (``"commune_id"`` for education / OFF; ``"taz_id"`` for
        WORK ON).  Passed into ``sample_locations`` via the parallel data bag.
    """
    df_persons = df_persons[df_persons["has_%s_trip" % purpose]]

    # Sample commute flows based on population, grouped by the origin zone.
    df_demand = df_persons.groupby(origin_zone_col).size().reset_index(name="count")
    df_demand["random_seed"] = random.randint(0, int(1e6), len(df_demand))
    # Rename to generic "origin_zone" so sample_destination_municipalities
    # sees a stable column name regardless of whether it holds commune_id or taz_id.
    df_demand = df_demand.rename(columns={origin_zone_col: "origin_zone"})
    df_demand = df_demand[["origin_zone", "count", "random_seed"]]
    df_demand = df_demand[df_demand["count"] > 0]

    df_flow = []

    with context.progress(label="Sampling %s municipalities" % step_name, total=len(df_demand)) as progress:
        with context.parallel(dict(df_od=df_od)) as parallel:
            for df_partial in parallel.imap_unordered(
                    sample_destination_municipalities,
                    df_demand.itertuples(index=False, name=None)):
                df_flow.append(df_partial)

    df_flow = pd.concat(df_flow).sort_values(["origin_id", "destination_id"])

    # Sample destinations based on the obtained flows.
    unique_ids = df_flow["destination_id"].unique()
    random_seeds = random.randint(0, int(1e6), len(unique_ids))

    df_result = []

    with context.progress(label="Sampling %s destinations" % purpose, total=len(df_demand)) as progress:
        with context.parallel(dict(df_locations=df_locations, df_flow=df_flow,
                                   zone_key=zone_key)) as parallel:
            for df_partial in parallel.imap_unordered(sample_locations, zip(unique_ids, random_seeds)):
                df_result.append(df_partial)

    df_result = pd.concat(df_result).sort_values(["origin_id", "destination_id"])

    return df_result[["origin_id", "destination_id", "location_id"]]

def execute(context):
    # Read the flag (no default here; the default is declared in configure()).
    taz_on = context.config("taz_work_location_choice")

    # Prepare population data
    df_persons = context.stage("synthesis.population.enriched")[["person_id", "household_id", "age_range"]].copy()
    df_trips = context.stage("synthesis.population.trips")

    df_persons["has_work_trip"] = df_persons["person_id"].isin(df_trips[
        (df_trips["following_purpose"] == "work") | (df_trips["preceding_purpose"] == "work")
    ]["person_id"])

    df_persons["has_education_trip"] = df_persons["person_id"].isin(df_trips[
        (df_trips["following_purpose"] == "education") | (df_trips["preceding_purpose"] == "education")
    ]["person_id"])

    # Attach home commune (always needed for the education path and the OFF path).
    df_homes = context.stage("synthesis.population.spatial.home.zones")
    df_persons = pd.merge(df_persons, df_homes, on="household_id")

    # When TAZ is ON, PIP each household home POINT to its TAZ and attach
    # home_taz_id.  This resolves the origin-side zone for the WORK OD.
    # The education path continues to group on commune_id regardless.
    if taz_on:
        from braunschweig.gravity.taz_margins import assign_taz

        df_home_pts = context.stage("synthesis.population.spatial.home.locations")
        df_taz = context.stage("braunschweig.data.spatial.taz")

        # Build a per-household GeoDataFrame with the home POINT and its Kreis
        # (first 5 chars of AGS commune_id) for the Kreis-constrained fallback.
        hh_unique = df_persons[["household_id", "commune_id"]].drop_duplicates("household_id").copy()
        hh_unique["kreis"] = hh_unique["commune_id"].astype(str).str[:5]
        hh_pts = hh_unique.merge(
            df_home_pts[["household_id", "geometry"]], on="household_id", how="left")
        hh_pts = gpd.GeoDataFrame(hh_pts, geometry="geometry", crs=df_home_pts.crs)

        home_taz_result, n_primary, n_fallback = assign_taz(
            hh_pts, df_taz, id_column="household_id", kreis_column="kreis")
        total_hh = n_primary + n_fallback
        import logging
        logging.getLogger(__name__).info(
            "[candidates] home -> TAZ PIP: %d/%d households primary (%.1f%%), "
            "%d fallback (%.1f%%)",
            n_primary, total_hh,
            100.0 * n_primary / total_hh if total_hh else 0.0,
            n_fallback,
            100.0 * n_fallback / total_hh if total_hh else 0.0,
        )

        # Attach home_taz_id to all persons.
        df_persons = df_persons.merge(
            home_taz_result[["household_id", "taz_id"]].rename(columns={"taz_id": "home_taz_id"}),
            on="household_id", how="left",
        )

        # Guard: a household_id absent from home_taz_result (home-point coverage
        # gap) would leave home_taz_id NaN, causing that person to silently drop
        # from the WORK demand groupby.  Raise early with a clear count so the
        # gap is never invisible (CLAUDE.md: no silent fallbacks).
        n_nan_taz = int(df_persons["home_taz_id"].isna().sum())
        if n_nan_taz > 0:
            raise ValueError(
                "%d persons have no home_taz_id after the household home-point -> TAZ merge "
                "(home-point coverage gap: %d households not present in home_taz_result). "
                "Check that synthesis.population.spatial.home.locations covers all households."
                % (n_nan_taz, int(df_persons.loc[df_persons["home_taz_id"].isna(), "household_id"].nunique()))
            )

    # Prepare spatial data
    df_work_od, df_education_od = context.stage("data.od.weighted")

    # Sampling
    random = np.random.RandomState(context.config("random_seed"))

    # --- WORK candidates ---
    df_work_locations = context.stage("synthesis.locations.work")
    df_work_locations = df_work_locations.copy()
    df_work_locations["weight"] = df_work_locations["employees"]

    if taz_on:
        # WORK ON path: group origin demand by home_taz_id, filter the
        # candidate pool by taz_id.  The OD has already been produced with
        # taz-keyed origin/destination columns by the gravity model (Task 5-6).
        df_work = process(context, "work", random, df_persons,
                          df_work_od, df_work_locations, "work",
                          origin_zone_col="home_taz_id", zone_key="taz_id")
    else:
        # WORK OFF path: commune-keyed, byte-identical to the pre-TAZ behaviour.
        df_work = process(context, "work", random, df_persons,
                          df_work_od, df_work_locations, "work",
                          origin_zone_col="commune_id", zone_key="commune_id")

    # --- EDUCATION candidates (always commune_id regardless of TAZ flag) ---
    df_locations = context.stage("synthesis.locations.education")
    if context.config("education_location_source") == 'bpe':
        df_education = process(context, "education", random, df_persons, df_education_od,
                               df_locations, "education",
                               origin_zone_col="commune_id", zone_key="commune_id")
    else:
        df_education = []
        for prefix, education_type in EDUCATION_MAPPING.items():
            df_education.append(
                process(context, "education", random,
                        df_persons[df_persons["age_range"] == prefix],
                        df_education_od[df_education_od["age_range"] == prefix],
                        df_locations[df_locations["education_type"].isin(education_type)],
                        prefix,
                        origin_zone_col="commune_id", zone_key="commune_id")
            )
        df_education = pd.concat(df_education)

    # Build the persons return frame.  When TAZ is ON, home_taz_id is included
    # so that locations.py can group by it on the WORK side.  The column is
    # absent on the OFF path so downstream logic is unchanged.
    persons_cols = ["person_id", "household_id", "age_range", "commune_id",
                    "has_work_trip", "has_education_trip"]
    if taz_on:
        persons_cols.append("home_taz_id")

    return dict(
        work_candidates=df_work,
        education_candidates=df_education,
        persons=df_persons[df_persons["has_work_trip"] | df_persons["has_education_trip"]][persons_cols],
    )
