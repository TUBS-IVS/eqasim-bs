"""synpp stage: external (non-ZGB) Gemeinde points with population (EWZ).

Used to spread each external Kreis's commuter volume across its Gemeinden by
population in the gravity gate assignment. Reads the VG250-EW GeoPackage. Flag-gated
on ``cordon_enabled``; returns an empty frame when OFF.
"""
from __future__ import annotations

import os

import geopandas as gpd

from braunschweig.data.cordon.network import read_external_gemeinden


def configure(context):
    context.config("cordon_enabled", False)
    context.config("data_path")
    context.config("cordon_vg250_path",
                   "germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip")


def execute(context):
    if not context.config("cordon_enabled"):
        return gpd.GeoDataFrame({"ars5": [], "ewz": []}, geometry=[], crs="EPSG:25832")
    path = os.path.join(context.config("data_path"), context.config("cordon_vg250_path"))
    return read_external_gemeinden(path, crs="EPSG:25832")
