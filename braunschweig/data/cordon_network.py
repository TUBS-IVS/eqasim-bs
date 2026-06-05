"""synpp stage: the cordon network as a link GeoDataFrame (for gate derivation).

Reads the ENLARGED (pre-cut) supply network produced by
``matsim.scenario.supply.processed`` into a link GeoDataFrame with road_class +
capacity. Flag-gated on ``cordon_enabled``; returns an empty frame when OFF.
"""
from __future__ import annotations

import geopandas as gpd

from braunschweig.data.cordon.network import read_matsim_links


def configure(context):
    context.config("cordon_enabled", False)
    if context.config("cordon_enabled"):
        context.stage("matsim.scenario.supply.processed")


def execute(context):
    if not context.config("cordon_enabled"):
        return gpd.GeoDataFrame({"link_id": [], "capacity": [], "road_class": []},
                                geometry=[], crs="EPSG:25832")
    supply = context.stage("matsim.scenario.supply.processed")
    network_path = "%s/%s" % (context.path("matsim.scenario.supply.processed"),
                              supply["network_path"])
    return read_matsim_links(network_path, crs="EPSG:25832")
