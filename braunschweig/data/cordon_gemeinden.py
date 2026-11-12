"""synpp stage: external (non-ZGB) Gemeinde points with population (EWZ).

Used to spread each external Kreis's commuter volume across its Gemeinden by
population in the gravity gate assignment. Reads the VG250-EW GeoPackage. Flag-gated
on ``cordon_enabled``; returns an empty frame when OFF.
"""
from __future__ import annotations

import os

import geopandas as gpd

from braunschweig import synpp_tokens
from braunschweig.data.cordon import network as _network
from braunschweig.data.cordon.network import read_external_gemeinden

# Helpers whose code this stage runs: synpp hashes only this module's source, so their
# source goes into the stage token (audit C2, ADR-0136).
_HELPER_MODULES = (_network,)
_DEFERRED_HELPER_MODULE_NAMES = ()


def configure(context):
    context.config("cordon_enabled", False)
    context.config("data_path")
    context.config("cordon_vg250_path",
                   "germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip")
    context.config("braunschweig.political_prefix")


def validate(context):
    """Include the helper modules' source in synpp's stage cache identity (ADR-0136)."""
    return synpp_tokens.source_token(
        _HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES,
        stage_label=__name__ + ".validate()")


def execute(context):
    if not context.config("cordon_enabled"):
        return gpd.GeoDataFrame({"ars5": [], "ewz": []}, geometry=[], crs="EPSG:25832")
    path = os.path.join(context.config("data_path"), context.config("cordon_vg250_path"))
    # Pass the RUN's scope instead of read_external_gemeinden's hardcoded ZGB-8
    # default: on a subset-scope run (e.g. political_prefix=["03101"]) the flow
    # side treats the other ZGB Kreise as external, so they need Gemeinde rows
    # here too -- otherwise their whole commuter volume is silently dropped
    # (only visible via the downstream gate_assignment WARNING). The sibling
    # stage cordon_pt_gates already passes the config value.
    return read_external_gemeinden(
        path, crs="EPSG:25832",
        zgb_prefixes=context.config("braunschweig.political_prefix"),
    )
