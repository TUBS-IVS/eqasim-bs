"""Build Kita (kindergarten) destination points for the education gravity model.

Each LSN Einheits-/Samtgemeinde's Plaetze (LSN K2300112) are distributed across the
unit's OSM kindergarten POIs by area. A POI's LSN unit is derived from its 12-digit
ARS commune_id; the kreisfreie Staedte fall back to the 3-digit Kreis code. Output:
GeoDataFrame[location_id, capacity, geometry] in EPSG:25832.
"""
from __future__ import annotations

import os

import geopandas as gpd
import pandas as pd

CRS_METRIC = "EPSG:25832"


def lsn_unit(commune_id):
    """LSN Einheits-/Samtgemeinde code from a 12-digit ARS: Kreis(3)+Verband(last 3)."""
    s = str(commune_id)
    return s[2:5] + s[6:9]


def build_kita_facilities(df_kitas, df_osm_kg):
    osm = df_osm_kg.to_crs(CRS_METRIC).copy()
    osm["lsn6"] = osm["commune_id"].map(lsn_unit)
    osm["kreis3"] = osm["commune_id"].astype(str).str[2:5]

    plaetze = dict(zip(df_kitas["lsn_code"].astype(str), df_kitas["plaetze"]))

    def unit_key(row):
        return row["lsn6"] if row["lsn6"] in plaetze else row["kreis3"]
    osm["unit"] = osm.apply(unit_key, axis=1)

    rows_id, rows_cap, rows_geom = [], [], []
    for unit, sub in osm.groupby("unit"):
        if unit not in plaetze:
            continue
        total_area = sub["weight"].sum()
        if total_area <= 0:
            continue
        for _, b in sub.iterrows():
            rows_id.append("kita_%d" % len(rows_id))
            rows_cap.append(float(plaetze[unit]) * float(b["weight"]) / total_area)
            rows_geom.append(b["geometry"])

    gdf = gpd.GeoDataFrame({"location_id": rows_id, "capacity": rows_cap},
                           geometry=rows_geom, crs=CRS_METRIC)
    return gdf[["location_id", "capacity", "geometry"]]


def configure(context):
    context.config("data_path")
    context.config("nds_kitas_path", "braunschweig/schools/nds_kitas_zgb.csv")
    context.stage("eqasim_common.locations.education")


def execute(context):
    path = os.path.join(context.config("data_path"), context.config("nds_kitas_path"))
    df_k = pd.read_csv(path, dtype={"lsn_code": str})
    df_osm = context.stage("eqasim_common.locations.education")
    df_osm = df_osm[df_osm["education_type"] == "kindergarten"].copy()
    gdf = build_kita_facilities(df_k, df_osm)
    placed = float(gdf["capacity"].sum())
    expected = float(df_k["plaetze"].sum())
    if placed < 0.9 * expected:
        print("[braunschweig.data.schools.kita_facilities] WARNING: placed %.0f < "
              "reference %.0f Plaetze -- some units lack OSM kindergarten POIs."
              % (placed, expected))
    print("[braunschweig.data.schools.kita_facilities] %d kita points; "
          "capacity sum %.0f of %.0f" % (len(gdf), placed, expected))
    return gdf


def validate(context):
    path = os.path.join(context.config("data_path"), context.config("nds_kitas_path"))
    if not os.path.exists(path):
        raise RuntimeError("nds_kitas_zgb.csv missing: run scripts/extract_nds_kitas.py")
    return os.path.getsize(path)
