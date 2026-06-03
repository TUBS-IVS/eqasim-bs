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


def age_to_level(age):
    for level, lo, hi in _SCHOOL_BANDS:
        if lo <= age <= hi:
            return level
    return "university"


def _xy(gdf):
    return np.column_stack([gdf.geometry.x.values, gdf.geometry.y.values])


def assign_education_locations(df_persons, df_nds, df_osm, cfg, rng):
    """Assign one education location to every person in ``df_persons``.

    df_persons: GeoDataFrame[person_id, age, geometry(home)] (EPSG:25832).
    df_nds: facilities GeoDataFrame[school_id, level, capacity, commune_id, geometry].
    df_osm: OSM education GeoDataFrame[location_id, education_type, weight,
            commune_id, geometry] (used for kindergarten + university).
    cfg: dict with slope_by_level, max_radius_km_by_level, kindergarten_radius_m,
         university_radius_m, max_iterations, tolerance.
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
        choice, _ = assign_by_capacity_gravity(
            _xy(sel), _xy(schools), schools["capacity"].values,
            slope=cfg["slope_by_level"][level],
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
    context.config("random_seed")
    context.config("education_gravity_slope_by_level",
                   {"grundschule": -0.3, "sekundar_1": -0.15, "sekundar_2": -0.08})
    context.config("education_gravity_max_radius_km_by_level",
                   {"grundschule": 15.0, "sekundar_1": 30.0, "sekundar_2": 60.0})
    context.config("education_gravity_kindergarten_radius_m", 2000.0)
    context.config("education_gravity_university_radius_m", 10000.0)
    context.config("education_gravity_max_iterations", 50)
    context.config("education_gravity_tolerance", 1e-3)


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

    cfg = {
        "slope_by_level": context.config("education_gravity_slope_by_level"),
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
