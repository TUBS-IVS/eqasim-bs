"""synpp stage: PT entry stops per source Kreis (one-seat ride into ZGB).

PT in-commuters board at these stops so the transit router yields direct, low-transfer
trips. Derived from the ENLARGED (pre-cut) supply transit schedule -- the boundary
stops where regional lines enter ZGB are the same pre and post cut. Flag-gated on
``cordon_enabled``; returns an empty frame when OFF.
"""
from __future__ import annotations

import os

import pandas as pd

from braunschweig.data.cordon.network import read_kreise, read_transit_stops_routes
from braunschweig.synthesis.incommuters import build_pt_entry_stops


def configure(context):
    context.config("cordon_enabled", False)
    context.config("data_path")
    context.config("cordon_vg250_path",
                   "germany/vg250-ew_12-31.utm32s.gpkg.ebenen.zip")
    context.config("braunschweig.political_prefix")
    if context.config("cordon_enabled"):
        context.stage("matsim.scenario.supply.processed")


def execute(context):
    if not context.config("cordon_enabled"):
        return pd.DataFrame(
            columns=["source_ars5", "stop_id", "x", "y", "n_zgb_routes", "is_rail"])
    supply = context.stage("matsim.scenario.supply.processed")
    schedule_path = "%s/%s" % (context.path("matsim.scenario.supply.processed"),
                               supply["schedule_path"])
    stops, routes = read_transit_stops_routes(schedule_path)
    vg250 = os.path.join(context.config("data_path"), context.config("cordon_vg250_path"))
    kreise = read_kreise(vg250, crs="EPSG:25832")
    zgb = {str(p) for p in context.config("braunschweig.political_prefix")}
    df = build_pt_entry_stops(stops, routes, kreise, zgb)
    n_rail = int(df["is_rail"].sum()) if len(df) else 0
    n_multi = int((df["n_zgb_routes"] >= 2).sum()) if len(df) else 0
    print(f"[braunschweig.data.cordon_pt_gates] {len(df)} PT entry stops across "
          f"{df['source_ars5'].nunique() if len(df) else 0} source Kreise "
          f"({n_rail} regional-rail, {n_multi} on >=2 ZGB-serving routes)")
    return df
