"""Per-person education-location assignment for Braunschweig.

School-age pupils (6-19) are placed by the capacity-constrained distance-decay
gravity model on the real NDS school facilities; kindergarten (0-5) uses the
OSM radius sampler; university students (20+) are routed through the
singly-constrained ``assign_by_decay`` on real university facilities
(``braunschweig.data.schools.university_facilities``). Output schema matches
the legacy ``eqasim_common.locations.synthesis.education`` so it is a
drop-in replacement.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import geopandas as gpd

from braunschweig.synthesis.locations.education_gravity_model import (
    assign_by_capacity_gravity, assign_by_decay, assign_by_radius,
)
from braunschweig.data.bbsr.regiostar import ars_to_ags8

_SCHOOL_BANDS = [
    ("kindergarten", 0, 5),
    ("grundschule", 6, 9),
    ("sekundar_1", 10, 15),
    ("upper_secondary", 16, 19),
    ("university", 20, 200),
]
_NDS_LEVELS = ("grundschule", "sekundar_1", "oberstufe", "bbs")


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


def assign_education_locations(df_persons, df_nds, df_osm, df_universities, cfg, rng):
    """Assign one education location to every person in ``df_persons``.

    df_persons:     GeoDataFrame[person_id, age, home_rs7, geometry(home)]
                    (EPSG:25832).  ``home_rs7`` must be present: it is the
                    RegioStaR-7 code of the person's home Gemeinde (-1 for
                    unmatched), used to build a per-pupil slope when
                    ``cfg["slope_by_level_rs7"]`` is set.
    df_nds:         Facilities GeoDataFrame[school_id, level, capacity,
                    commune_id, geometry] -- NDS school levels only
                    (grundschule/sekundar_1/oberstufe/bbs).
    df_osm:         OSM education GeoDataFrame[location_id, education_type,
                    weight, commune_id, geometry].  Used for kindergarten only.
    df_universities: University facilities GeoDataFrame[location_id, capacity,
                    geometry] (EPSG:25832).  University students (age 20+) are
                    routed here via the singly-constrained ``assign_by_decay``
                    using ``cfg["university_slope"]`` and
                    ``cfg["university_max_radius_km"]``.  Institutions outside
                    the ZGB cordon carry no commune_id (filled as empty string).
    cfg:            dict with slope_by_level, slope_by_level_rs7 (None or
                    nested dict), max_radius_km_by_level, kindergarten_radius_m,
                    university_slope, university_max_radius_km,
                    max_iterations, tolerance, bbs_share.
    Returns DataFrame[person_id, commune_id, location_id, geometry].
    """
    df = df_persons.copy()
    df["level"] = df["age"].apply(age_to_level)

    # Resolve the synthetic "upper_secondary" band into oberstufe (academic)
    # or bbs (vocational) per pupil, drawn from the configured enrollment share.
    # bbs_share is the fraction going to vocational BBS (NDS default: 0.681).
    us = df["level"] == "upper_secondary"
    if us.any():
        draw = rng.random_sample(size=int(us.sum())) < cfg["bbs_share"]
        df.loc[us, "level"] = np.where(draw, "bbs", "oberstufe")

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

    # Kindergarten (age 0-5): OSM radius sampler, unchanged from previous behaviour.
    sel = df[df["level"] == "kindergarten"]
    if not sel.empty:
        locs = df_osm[df_osm["education_type"] == "kindergarten"]
        if locs.empty:
            raise RuntimeError(
                "[education_gravity] no OSM locations for 'kindergarten'")
        choice = assign_by_radius(
            _xy(sel), _xy(locs), locs["weight"].values,
            radius_m=cfg["kindergarten_radius_m"], rng=rng,
        )
        picked = locs.iloc[choice]
        parts.append(pd.DataFrame({
            "person_id": sel["person_id"].values,
            "location_id": picked["location_id"].values,
            "commune_id": picked["commune_id"].values,
            "geometry": picked["geometry"].values,
        }))

    # University (age 20+): singly-constrained decay on real university facilities.
    # Far institutions' large enrollment is largely non-resident; only the
    # distance decay governs how far the local commuter tail reaches.
    # commune_id is empty because many universities are outside the ZGB cordon.
    sel = df[df["level"] == "university"]
    if not sel.empty:
        if df_universities.empty:
            raise RuntimeError(
                "[education_gravity] no university facilities provided")
        choice = assign_by_decay(
            _xy(sel), _xy(df_universities), df_universities["capacity"].values,
            slope=cfg["university_slope"],
            max_radius_km=cfg["university_max_radius_km"], rng=rng,
        )
        picked = df_universities.iloc[choice]
        parts.append(pd.DataFrame({
            "person_id": sel["person_id"].values,
            "location_id": picked["location_id"].values,
            "commune_id": "",   # surrounding universities have no ZGB commune
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
                   {"grundschule": -0.3, "sekundar_1": -0.15,
                    "oberstufe": -0.08, "bbs": -0.05})
    context.config("education_gravity_max_radius_km_by_level",
                   {"grundschule": 15.0, "sekundar_1": 30.0,
                    "oberstufe": 60.0, "bbs": 100.0})
    # Share of upper-secondary (age 16-19) pupils assigned to berufsbildende
    # Schulen (BBS); the remainder go to academic Oberstufe.
    # Source: NDS Kultusministerium Schuljahresstatistik 2023/24:
    # BBS ~68 100 / (BBS 68 100 + gymnasiale Oberstufe 32 000) ~ 0.681.
    context.config("education_bbs_share", 0.681)
    context.stage("braunschweig.data.schools.university_facilities")
    context.config("education_gravity_kindergarten_radius_m", 2000.0)
    context.config("education_university_slope", -0.08)
    context.config("education_university_max_radius_km", 150.0)
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
    df_universities = context.stage("braunschweig.data.schools.university_facilities")

    # Attach home RegioStaR-7 code to each pupil via a spatial join with the
    # municipalities layer. The municipalities GeoDataFrame must share the same
    # CRS as df_persons (both EPSG:25832). Persons outside all municipality
    # polygons receive -1 (unmatched); slope_vector_for_level falls back to the
    # scalar slope for those, which is the safe conservative default.
    df_zones = context.stage("data.spatial.municipalities")[["commune_id", "geometry"]]
    if df_zones.crs is not None and df_persons.crs is not None:
        df_zones = df_zones.to_crs(df_persons.crs)
    df_rs7 = context.stage("braunschweig.data.bbsr.regiostar")[["commune_id", "regiostar7"]]
    joined = gpd.sjoin(df_persons[["person_id", "geometry"]], df_zones,
                       how="left", predicate="within").drop(columns="index_right")
    joined = joined.drop_duplicates("person_id")
    # municipalities carry the 12-digit ARS; regiostar keys on the 8-digit AGS.
    joined["commune_id"] = joined["commune_id"].map(ars_to_ags8)
    joined = joined.merge(df_rs7, on="commune_id", how="left")
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
        "university_slope": context.config("education_university_slope"),
        "university_max_radius_km":
            context.config("education_university_max_radius_km"),
        "max_iterations": context.config("education_gravity_max_iterations"),
        "tolerance": context.config("education_gravity_tolerance"),
        "bbs_share": context.config("education_bbs_share"),
    }
    out = assign_education_locations(
        df_persons, df_nds, df_osm, df_universities, cfg, rng)
    out = gpd.GeoDataFrame(out, geometry="geometry", crs=df_nds.crs)

    assert len(out) == len(df_persons), (
        f"education_gravity placed {len(out)} of {len(df_persons)} pupils")
    assert set(out["person_id"]) == set(df_persons["person_id"])
    return out[["person_id", "commune_id", "location_id", "geometry"]]
