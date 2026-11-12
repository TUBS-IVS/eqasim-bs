"""synpp stage: the cordon network as a link GeoDataFrame (for gate derivation).

Reads the ENLARGED (pre-cut) supply network produced by
``matsim.scenario.supply.processed`` into a link GeoDataFrame with road_class +
capacity. Flag-gated on ``cordon_enabled``; returns an empty frame when OFF.
"""
from __future__ import annotations

import geopandas as gpd

from braunschweig import synpp_tokens
from braunschweig.data.cordon import network as _network
from braunschweig.data.cordon.network import read_matsim_links

# Helpers whose code this stage runs: synpp hashes only this module's source, so their
# source goes into the stage token (audit C2, ADR-0136).
_HELPER_MODULES = (_network,)
_DEFERRED_HELPER_MODULE_NAMES = ()


def configure(context):
    context.config("cordon_enabled", False)
    if context.config("cordon_enabled"):
        context.stage("matsim.scenario.supply.processed")


def validate(context):
    """Include the helper modules' source in synpp's stage cache identity (ADR-0136)."""
    return synpp_tokens.source_token(
        _HELPER_MODULES, _DEFERRED_HELPER_MODULE_NAMES,
        stage_label=__name__ + ".validate()")


def execute(context):
    if not context.config("cordon_enabled"):
        return gpd.GeoDataFrame({"link_id": [], "capacity": [], "road_class": []},
                                geometry=[], crs="EPSG:25832")
    supply = context.stage("matsim.scenario.supply.processed")
    network_path = "%s/%s" % (context.path("matsim.scenario.supply.processed"),
                              supply["network_path"])
    return read_matsim_links(network_path, crs="EPSG:25832")
