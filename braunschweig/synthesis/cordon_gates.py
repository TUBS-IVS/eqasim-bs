"""Cordon gates synpp stage: deduped major gates + gravity commuter volume.

Derives gates from the ENLARGED (pre-cut) supply network intersected with the
cordon polygon. The cut later removes everything outside the cordon, but the links
crossing the cordon boundary -- the gate points -- are identical pre and post cut,
so deriving them here (upstream of synthesis) lets in-commuters be anchored at gates
before routing. Flag-gated on ``cordon_enabled``; returns empty frames when OFF.

See ``docs/superpowers/plans/2026-06-05-cordon-einpendler-injection.md`` (Task 1).
"""
from __future__ import annotations

import geopandas as gpd
import pandas as pd

from braunschweig.data.spatial.cordon import build_cordon_polygon, buffer_m_from_fraction
from braunschweig.data.cordon.gates import (
    dedupe_gates, derive_road_gates, select_major_gates)
from braunschweig.data.cordon.gate_assignment import (
    commuter_volume_by_kreis, population_gravity_gate_assignment)


def build_gates_with_volume(municipalities, links, gemeinden, flows, zgb_kreise,
                            buffer_fraction, road_classes, beta, capacity_exponent):
    """Pure end-to-end gate build.

    cordon polygon (dissolved municipalities + fractional buffer) -> road gates
    (links crossing the boundary) -> dedupe (one gate per crossing) -> major gates
    (Autobahn/Bundesstrasse only) -> per-Kreis population-gravity volume.

    Returns:
        (gates, assignment) where
        gates: GeoDataFrame [link_id, capacity, road_class, gate_id, geometry]
        assignment: DataFrame [ars5, gate_id, inbound, outbound].
    """
    buffer_m = buffer_m_from_fraction(municipalities, buffer_fraction)
    cordon = build_cordon_polygon(municipalities, buffer_m)
    gates = derive_road_gates(links, cordon)
    gates = dedupe_gates(gates, tolerance_m=100.0)
    allowed = road_classes if ("road_class" in gates.columns
                               and gates["road_class"].notna().any()) else None
    major = select_major_gates(gates, allowed_classes=allowed).reset_index(drop=True)
    major["gate_id"] = [f"gate_{i:04d}" for i in range(len(major))]
    volume = commuter_volume_by_kreis(flows, zgb_kreise)
    assignment = population_gravity_gate_assignment(
        gemeinden, major[["gate_id", "capacity", "geometry"]], volume,
        beta=beta, capacity_exponent=capacity_exponent)
    return major, assignment


def configure(context):
    context.config("cordon_enabled", False)
    if not context.config("cordon_enabled"):
        return
    context.config("cordon_network_buffer_fraction", 0.10)
    context.config("cordon_gate_road_classes", ("motorway", "trunk", "primary"))
    context.config("cordon_gravity_beta", -0.05)
    context.config("cordon_gravity_capacity_exponent", 1.0)
    context.config("braunschweig.political_prefix")
    context.stage("data.spatial.municipalities")
    context.stage("braunschweig.data.cordon_network")     # links GeoDataFrame (Task 1b)
    context.stage("braunschweig.data.cordon_gemeinden")   # external Gemeinde EWZ (Task 1b)
    context.stage("braunschweig.data.census.pendler")


def execute(context):
    if not context.config("cordon_enabled"):
        empty_gates = gpd.GeoDataFrame(
            {"gate_id": [], "capacity": [], "road_class": []},
            geometry=[], crs="EPSG:25832")
        empty_assignment = pd.DataFrame(
            columns=["ars5", "gate_id", "inbound", "outbound"])
        return {"gates": empty_gates, "assignment": empty_assignment}

    zgb = {str(p) for p in context.config("braunschweig.political_prefix")}
    gates, assignment = build_gates_with_volume(
        context.stage("data.spatial.municipalities"),
        context.stage("braunschweig.data.cordon_network"),
        context.stage("braunschweig.data.cordon_gemeinden"),
        context.stage("braunschweig.data.census.pendler"),
        zgb_kreise=zgb,
        buffer_fraction=float(context.config("cordon_network_buffer_fraction")),
        road_classes=tuple(context.config("cordon_gate_road_classes")),
        beta=float(context.config("cordon_gravity_beta")),
        capacity_exponent=float(context.config("cordon_gravity_capacity_exponent")))
    print(f"[braunschweig.synthesis.cordon_gates] {len(gates)} major gates, "
          f"{int(assignment['inbound'].sum()):,} inbound + "
          f"{int(assignment['outbound'].sum()):,} outbound SvB")
    return {"gates": gates, "assignment": assignment}
