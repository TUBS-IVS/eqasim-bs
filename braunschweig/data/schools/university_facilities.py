"""Build university destination points for the education gravity model.

Inside the ZGB region the per-commune enrollment (LSN) is distributed across the
commune's OSM university buildings by the chosen weight column (default: footprint
area; with education_building_distribution=True: building potential from the
building-potentials stage). Each surrounding institution becomes a single curated
campus point (that branch is not affected by the weight_column parameter). Output:
GeoDataFrame[location_id, capacity, geometry] in EPSG:25832.
"""
from __future__ import annotations

import os

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

CRS_METRIC = "EPSG:25832"

# Ostfalia is one enrollment row but spans three ZGB campus communes; its students
# are distributed across these communes' OSM university buildings by area.
OSTFALIA_COMMUNES = ["03158", "03102", "03103"]


def build_university_facilities(df_hochschulen, df_osm_uni, weight_column="weight"):
    """Distribute local enrollment across OSM university buildings; add surrounding points.

    Parameters
    ----------
    df_hochschulen:
        Institution table with columns institution, scope, ars5, enrollment, lon, lat.
    df_osm_uni:
        OSM university buildings GeoDataFrame; must contain commune_id, geometry,
        and the column named by weight_column (for local-scope institutions only).
    weight_column:
        Column in df_osm_uni to use as the distribution weight for LOCAL institutions.
        Default "weight" (footprint area) reproduces the legacy behaviour
        byte-identically. Pass "potential" to distribute by building potential.
        The surrounding-institution branch (curated single points) is not affected.
    """
    rows_id, rows_cap, rows_geom = [], [], []

    # --- surrounding: one point per institution (UNCHANGED) -------------------
    sur = df_hochschulen[df_hochschulen["scope"] == "surrounding"]
    if len(sur):
        pts = gpd.GeoSeries(
            [Point(lon, lat) for lon, lat in zip(sur["lon"], sur["lat"])],
            crs="EPSG:4326").to_crs(CRS_METRIC)
        for inst, cap, geom in zip(sur["institution"], sur["enrollment"], pts):
            rows_id.append("uni_sur_" + str(inst).replace(" ", "_"))
            rows_cap.append(float(cap))
            rows_geom.append(geom)

    # --- local: pool enrollment per commune, split across OSM buildings -------
    local = df_hochschulen[df_hochschulen["scope"] == "local"].copy()
    osm = df_osm_uni.to_crs(CRS_METRIC).copy()
    osm["ars5"] = osm["commune_id"].astype(str).str[:5]

    def _distribute(enrollment, communes):
        sub = osm[osm["ars5"].isin(communes)]
        total_area = sub[weight_column].sum()
        if total_area <= 0 or sub.empty:
            return
        for _, b in sub.iterrows():
            rows_id.append("uni_loc_%d" % len(rows_id))
            rows_cap.append(float(enrollment) * float(b[weight_column]) / total_area)
            rows_geom.append(b["geometry"])

    pooled = {}
    for _, r in local.iterrows():
        if str(r["institution"]).startswith("Ostfalia"):
            continue
        pooled[r["ars5"]] = pooled.get(r["ars5"], 0.0) + float(r["enrollment"])
    for ars5, enr in pooled.items():
        _distribute(enr, [ars5])

    ost = local[local["institution"].astype(str).str.startswith("Ostfalia")]
    if len(ost):
        _distribute(float(ost["enrollment"].sum()), OSTFALIA_COMMUNES)

    gdf = gpd.GeoDataFrame(
        {"location_id": rows_id, "capacity": rows_cap},
        geometry=rows_geom, crs=CRS_METRIC)
    return gdf[["location_id", "capacity", "geometry"]]


def configure(context):
    context.config("data_path")
    context.config("nds_hochschulen_path", "braunschweig/schools/nds_hochschulen.csv")
    context.stage("eqasim_common.locations.education")
    enabled = context.config("education_building_distribution", True)
    if enabled:
        context.stage("braunschweig.data.building_potentials")


def execute(context):
    path = os.path.join(context.config("data_path"),
                        context.config("nds_hochschulen_path"))
    df_h = pd.read_csv(path, dtype={"ars5": str})
    df_osm = context.stage("eqasim_common.locations.education")
    df_osm = df_osm[df_osm["education_type"] == "university"].copy()

    enabled = context.config("education_building_distribution")
    weight_column = "weight"
    if enabled:
        from braunschweig.data.building_potential_attach import attach_potential
        df_b = context.stage("braunschweig.data.building_potentials")
        vals, _p, _f = attach_potential(
            df_osm, df_b, "potential_university",
            fallback=df_osm["weight"].to_numpy(float), label="university")
        df_osm = df_osm.copy()
        df_osm["potential"] = vals
        weight_column = "potential"

    gdf = build_university_facilities(df_h, df_osm, weight_column=weight_column)
    # Guard against silent enrollment loss: a local institution whose commune has
    # no OSM university building is dropped by the area-distribution (its students
    # would be redistributed to the nearest surviving campus). Name any such
    # institution explicitly so the calibration is not trusted on incomplete data.
    osm_ars5 = set(df_osm["commune_id"].astype(str).str[:5])
    dropped = []
    for _, r in df_h[df_h["scope"] == "local"].iterrows():
        communes = (OSTFALIA_COMMUNES
                    if str(r["institution"]).startswith("Ostfalia")
                    else [str(r["ars5"])])
        if not (set(communes) & osm_ars5):
            dropped.append("%s (commune(s) %s)" % (r["institution"], communes))
    if dropped:
        print("[braunschweig.data.schools.university_facilities] WARNING: no OSM "
              "university buildings for local institution(s): %s -- their students "
              "are redistributed to the nearest campus." % "; ".join(dropped))
    placed = float(gdf["capacity"].sum())
    expected = float(df_h["enrollment"].sum())
    print("[braunschweig.data.schools.university_facilities] %d university points; "
          "capacity sum %.0f of %.0f" % (len(gdf), placed, expected))
    return gdf


def validate(context):
    path = os.path.join(context.config("data_path"),
                        context.config("nds_hochschulen_path"))
    if not os.path.exists(path):
        raise RuntimeError("nds_hochschulen.csv missing: run scripts/seed_nds_hochschulen.py")
    return os.path.getsize(path)
