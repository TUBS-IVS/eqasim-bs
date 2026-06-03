"""Build university destination points for the education gravity model.

Inside the ZGB region the per-commune enrollment (LSN) is distributed across the
commune's OSM university buildings by area (detailed intra-city spread). Each
surrounding institution becomes a single curated campus point. Output:
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


def build_university_facilities(df_hochschulen, df_osm_uni):
    rows_id, rows_cap, rows_geom = [], [], []

    # --- surrounding: one point per institution -------------------------------
    sur = df_hochschulen[df_hochschulen["scope"] == "surrounding"]
    if len(sur):
        pts = gpd.GeoSeries(
            [Point(lon, lat) for lon, lat in zip(sur["lon"], sur["lat"])],
            crs="EPSG:4326").to_crs(CRS_METRIC)
        for inst, cap, geom in zip(sur["institution"], sur["enrollment"], pts):
            rows_id.append("uni_sur_" + str(inst).replace(" ", "_"))
            rows_cap.append(float(cap))
            rows_geom.append(geom)

    # --- local: pool enrollment per commune, split across OSM buildings by area
    local = df_hochschulen[df_hochschulen["scope"] == "local"].copy()
    osm = df_osm_uni.to_crs(CRS_METRIC).copy()
    osm["ars5"] = osm["commune_id"].astype(str).str[:5]

    def _distribute(enrollment, communes):
        sub = osm[osm["ars5"].isin(communes)]
        total_area = sub["weight"].sum()
        if total_area <= 0 or sub.empty:
            return
        for _, b in sub.iterrows():
            rows_id.append("uni_loc_%d" % len(rows_id))
            rows_cap.append(float(enrollment) * float(b["weight"]) / total_area)
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


def execute(context):
    path = os.path.join(context.config("data_path"),
                        context.config("nds_hochschulen_path"))
    df_h = pd.read_csv(path, dtype={"ars5": str})
    df_osm = context.stage("eqasim_common.locations.education")
    df_osm = df_osm[df_osm["education_type"] == "university"].copy()
    gdf = build_university_facilities(df_h, df_osm)
    # Guard against silent enrollment loss: a local institution whose commune has
    # no OSM university building would be dropped by the area-distribution. Warn
    # if the placed capacity is materially below the reference enrollment.
    placed = float(gdf["capacity"].sum())
    expected = float(df_h["enrollment"].sum())
    if placed < 0.99 * expected:
        print("[braunschweig.data.schools.university_facilities] WARNING: placed "
              "capacity %.0f < reference enrollment %.0f -- a local institution "
              "may lack OSM university buildings in its commune." % (placed, expected))
    print("[braunschweig.data.schools.university_facilities] %d university points; "
          "capacity sum %.0f of %.0f" % (len(gdf), placed, expected))
    return gdf


def validate(context):
    path = os.path.join(context.config("data_path"),
                        context.config("nds_hochschulen_path"))
    if not os.path.exists(path):
        raise RuntimeError("nds_hochschulen.csv missing: run scripts/seed_nds_hochschulen.py")
    return os.path.getsize(path)
