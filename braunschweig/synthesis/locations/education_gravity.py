"""Per-person education-location assignment for Braunschweig.

School-age pupils (6-19) are placed by the capacity-constrained distance-decay
gravity model on the real NDS school facilities; kindergarten (0-5) and university
(20+) keep the OSM radius sampler. Output schema matches the legacy
``eqasim_common.locations.synthesis.education`` so it is a drop-in replacement.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import geopandas as gpd

from braunschweig.synthesis.locations.education_gravity_model import (
    assign_by_capacity_gravity, assign_by_radius,
)

_SCHOOL_BANDS = [
    ("kindergarten", 0, 5),
    ("grundschule", 6, 9),
    ("sekundar_1", 10, 15),
    ("sekundar_2", 16, 19),
    ("university", 20, 200),
]
_NDS_LEVELS = ("grundschule", "sekundar_1", "sekundar_2")


def slope_vector_for_level(level, home_rs7, by_level_rs7, scalar_by_level):
    """Per-pupil slope for ``level``.

    ``home_rs7`` is a Series of each pupil's home RegioStaR-7 code.
    If ``by_level_rs7`` (nested dict ``{level: {rs7_code: slope}}``) is given and
    contains an entry for ``level``, use its per-RS7 value for each pupil,
    falling back to ``scalar_by_level[level]`` for RS7 codes without an override.
    If ``by_level_rs7`` is None or does not contain ``level``, every pupil
    receives the scalar, which reproduces the previous uniform-slope behaviour.
    Returns a float64 numpy array of length ``len(home_rs7)``.
    """
    scalar = scalar_by_level[level]
    if not by_level_rs7 or level not in by_level_rs7:
        return np.full(len(home_rs7), float(scalar))
    overrides = {int(k): float(v) for k, v in by_level_rs7[level].items()}
    return home_rs7.map(lambda c: overrides.get(int(c), scalar)).to_numpy(dtype=float)


def age_to_level(age):
    for level, lo, hi in _SCHOOL_BANDS:
        if lo <= age <= hi:
            return level
    return "university"


def _xy(gdf):
    return np.column_stack([gdf.geometry.x.values, gdf.geometry.y.values])


def assign_education_locations(df_persons, df_nds, df_osm, cfg, rng):
    """Assign one education location to every person in ``df_persons``.

    df_persons: GeoDataFrame[person_id, age, home_rs7, geometry(home)] (EPSG:25832).
                ``home_rs7`` must be present: it is the RegioStaR-7 code of the
                person's home Gemeinde (-1 for unmatched), used to build a
                per-pupil slope when ``cfg["slope_by_level_rs7"]`` is set.
    df_nds: facilities GeoDataFrame[school_id, level, capacity, commune_id, geometry].
    df_osm: OSM education GeoDataFrame[location_id, education_type, weight,
            commune_id, geometry] (used for kindergarten + university).
    cfg: dict with slope_by_level, slope_by_level_rs7 (None or nested dict),
         max_radius_km_by_level, kindergarten_radius_m, university_radius_m,
         max_iterations, tolerance.
    Returns DataFrame[person_id, commune_id, location_id, geometry].
    """
    df = df_persons.copy()
    df["level"] = df["age"].apply(age_to_level)
    parts = []

    for level in _NDS_LEVELS:
        sel = df[df["level"] == level]
        if sel.empty:
            continue
        schools = df_nds[df_nds["level"] == level]
        if schools.empty:
            raise RuntimeError(
                f"[education_gravity] no NDS schools for level '{level}'")
        slope = slope_vector_for_level(
            level, sel["home_rs7"], cfg.get("slope_by_level_rs7"),
            cfg["slope_by_level"])
        choice, _ = assign_by_capacity_gravity(
            _xy(sel), _xy(schools), schools["capacity"].values,
            slope=slope,
            max_radius_km=cfg["max_radius_km_by_level"][level],
            max_iterations=cfg["max_iterations"], tolerance=cfg["tolerance"],
            rng=rng,
        )
        picked = schools.iloc[choice]
        parts.append(pd.DataFrame({
            "person_id": sel["person_id"].values,
            "location_id": picked["school_id"].values,
            "commune_id": picked["commune_id"].values,
            "geometry": picked["geometry"].values,
        }))

    for level, radius_key in (("kindergarten", "kindergarten_radius_m"),
                              ("university", "university_radius_m")):
        sel = df[df["level"] == level]
        if sel.empty:
            continue
        locs = df_osm[df_osm["education_type"] == level]
        if locs.empty:
            raise RuntimeError(
                f"[education_gravity] no OSM locations for '{level}'")
        choice = assign_by_radius(
            _xy(sel), _xy(locs), locs["weight"].values,
            radius_m=cfg[radius_key], rng=rng,
        )
        picked = locs.iloc[choice]
        parts.append(pd.DataFrame({
            "person_id": sel["person_id"].values,
            "location_id": picked["location_id"].values,
            "commune_id": picked["commune_id"].values,
            "geometry": picked["geometry"].values,
        }))

    out = pd.concat(parts, ignore_index=True)
    return out[["person_id", "commune_id", "location_id", "geometry"]]


def configure(context):
    context.stage("synthesis.population.spatial.primary.candidates")
    context.stage("synthesis.population.enriched")
    context.stage("synthesis.population.spatial.home.locations")
    context.stage("braunschweig.data.schools.facilities")
    context.stage("eqasim_common.locations.education")
    context.stage("data.spatial.municipalities")
    context.stage("braunschweig.data.bbsr.regiostar")
    context.config("random_seed")
    context.config("education_gravity_slope_by_level",
                   {"grundschule": -0.3, "sekundar_1": -0.15, "sekundar_2": -0.08})
    context.config("education_gravity_max_radius_km_by_level",
                   {"grundschule": 15.0, "sekundar_1": 30.0, "sekundar_2": 60.0})
    context.config("education_gravity_kindergarten_radius_m", 2000.0)
    context.config("education_gravity_university_radius_m", 10000.0)
    context.config("education_gravity_max_iterations", 50)
    context.config("education_gravity_tolerance", 1e-3)
    # None (not {}) so synpp flatten() does not drop this key; per-RS7 dict is
    # set by the calibration script and mirrors gravity_slope_by_regiostar7.
    context.config("education_gravity_slope_by_level_rs7", None)


def execute(context):
    rng = np.random.RandomState(context.config("random_seed"))

    df_persons = context.stage(
        "synthesis.population.spatial.primary.candidates")["persons"]
    df_persons = df_persons[df_persons["has_education_trip"]].copy()
    households = set(df_persons["household_id"].unique())

    df_age = context.stage("synthesis.population.enriched")[["person_id", "age"]]
    df_persons = pd.merge(df_persons, df_age)

    df_homes = context.stage("synthesis.population.spatial.home.locations")
    df_homes = df_homes[df_homes["household_id"].isin(households)][
        ["household_id", "geometry"]]
    df_persons = pd.merge(df_persons, df_homes)
    df_persons = gpd.GeoDataFrame(df_persons, geometry="geometry",
                                  crs=df_homes.crs)

    df_nds = context.stage("braunschweig.data.schools.facilities")
    df_osm = context.stage("eqasim_common.locations.education")
    df_osm = df_osm[~df_osm["fake"]].copy()

    # Attach home RegioStaR-7 code to each pupil via a spatial join with the
    # municipalities layer. The municipalities GeoDataFrame must share the same
    # CRS as df_persons (both EPSG:25832). Persons outside all municipality
    # polygons receive -1 (unmatched); slope_vector_for_level falls back to the
    # scalar slope for those, which is the safe conservative default.
    df_zones = context.stage("data.spatial.municipalities")[["commune_id", "geometry"]]
    df_rs7 = context.stage("braunschweig.data.bbsr.regiostar")[["commune_id", "regiostar7"]]
    joined = gpd.sjoin(df_persons[["person_id", "geometry"]], df_zones,
                       how="left", predicate="within").drop(columns="index_right")
    joined = joined.drop_duplicates("person_id").merge(df_rs7, on="commune_id", how="left")
    rs7_by_person = joined.set_index("person_id")["regiostar7"]
    df_persons["home_rs7"] = (df_persons["person_id"].map(rs7_by_person)
                              .fillna(-1).astype(int))

    cfg = {
        "slope_by_level": context.config("education_gravity_slope_by_level"),
        "slope_by_level_rs7": context.config("education_gravity_slope_by_level_rs7"),
        "max_radius_km_by_level":
            context.config("education_gravity_max_radius_km_by_level"),
        "kindergarten_radius_m":
            context.config("education_gravity_kindergarten_radius_m"),
        "university_radius_m":
            context.config("education_gravity_university_radius_m"),
        "max_iterations": context.config("education_gravity_max_iterations"),
        "tolerance": context.config("education_gravity_tolerance"),
    }
    out = assign_education_locations(df_persons, df_nds, df_osm, cfg, rng)
    out = gpd.GeoDataFrame(out, geometry="geometry", crs=df_nds.crs)

    assert len(out) == len(df_persons), (
        f"education_gravity placed {len(out)} of {len(df_persons)} pupils")
    assert set(out["person_id"]) == set(df_persons["person_id"])
    return out[["person_id", "commune_id", "location_id", "geometry"]]
