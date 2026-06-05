"""Assign external Kreise to cordon gates and aggregate per-gate inbound volume.

For in-commuters, each external (non-ZGB) source Kreis enters the region through
its nearest cordon gate; the BA-Pendler inbound SvB of that Kreis is the volume
routed through that gate. This yields, per gate, *how many* commuters choose it and
*which* Kreise feed it -- both the einpendler placement basis (Phase 3) and the
per-gate usage shown in the validation outputs (gates.csv + gate_assignment.csv).

Region-neutral; expects a metric CRS (EPSG:25832 for ZGB). Pure functions; the
synpp stage / validator wires the data sources (BA OD, Kreis polygons, gates).

Part of the cross-cordon external-demand module; see
``docs/superpowers/specs/2026-06-05-cross-cordon-external-demand-design.md``.
"""
from __future__ import annotations

import geopandas as gpd
import pandas as pd


def inbound_volume_by_kreis(flows: pd.DataFrame, zgb_kreise) -> pd.DataFrame:
    """Inbound SvB per external source Kreis.

    Keeps flows whose destination Kreis is in ZGB and whose origin Kreis is NOT in
    ZGB (true in-commuters), summed per origin Kreis.

    Args:
        flows: columns [orig_ars, dest_ars, flow] (5-digit ARS, SvB count).
        zgb_kreise: iterable of in-scope ZGB 5-digit Kreis ARS.

    Returns:
        DataFrame [ars5, inbound] (one row per external source Kreis), inbound int.
    """
    zgb = {str(k) for k in zgb_kreise}
    mask = flows["dest_ars"].isin(zgb) & ~flows["orig_ars"].isin(zgb)
    agg = (flows[mask].groupby("orig_ars", as_index=False)["flow"].sum()
           .rename(columns={"orig_ars": "ars5", "flow": "inbound"}))
    agg["inbound"] = agg["inbound"].astype(int)
    return agg


def assign_kreise_to_gates_with_volume(kreise: gpd.GeoDataFrame, gates: gpd.GeoDataFrame,
                                       inbound: pd.DataFrame) -> pd.DataFrame:
    """Assign each external Kreis to its nearest gate and attach its inbound volume.

    Args:
        kreise: GeoDataFrame [ars5, geometry] of external source Kreise (any
            geometry; the representative point is used for the nearest-gate match).
        gates: GeoDataFrame with a ``gate_id`` column and point geometry.
        inbound: DataFrame [ars5, inbound] from :func:`inbound_volume_by_kreis`.

    Returns:
        DataFrame [ars5, gate_id, inbound, distance_km], one row per Kreis, sorted
        by inbound descending. Kreise with no inbound flow get inbound 0.

    Raises:
        ValueError: if ``gates`` has no ``gate_id`` column.
    """
    if "gate_id" not in gates.columns:
        raise ValueError("assign_kreise_to_gates_with_volume: gates needs a 'gate_id' column")
    k = kreise.copy()
    k["geometry"] = k.geometry.representative_point()
    joined = gpd.sjoin_nearest(k, gates[["gate_id", "geometry"]], how="left",
                               distance_col="dist_m")
    joined = joined.drop(columns=[c for c in joined.columns if c.startswith("index_")])
    out = joined.merge(inbound, on="ars5", how="left")
    out["inbound"] = out["inbound"].fillna(0).astype(int)
    out["distance_km"] = out["dist_m"] / 1000.0
    return (out[["ars5", "gate_id", "inbound", "distance_km"]]
            .sort_values("inbound", ascending=False).reset_index(drop=True))


def gate_volume_summary(assignment: pd.DataFrame) -> pd.DataFrame:
    """Aggregate the Kreis->gate assignment per gate.

    Args:
        assignment: output of :func:`assign_kreise_to_gates_with_volume`.

    Returns:
        DataFrame [gate_id, n_commuters_inbound, n_kreise, source_kreise], sorted by
        n_commuters_inbound descending. ``source_kreise`` is a ';'-joined list of the
        contributing Kreis ARS (those with inbound > 0), sorted by inbound desc.
    """
    rows = []
    for gid, sub in assignment.groupby("gate_id"):
        sub = sub.sort_values("inbound", ascending=False)
        contributing = sub.loc[sub["inbound"] > 0, "ars5"].tolist()
        rows.append({
            "gate_id": gid,
            "n_commuters_inbound": int(sub["inbound"].sum()),
            "n_kreise": int(len(contributing)),
            "source_kreise": ";".join(contributing),
        })
    return (pd.DataFrame(rows, columns=["gate_id", "n_commuters_inbound", "n_kreise",
                                        "source_kreise"])
            .sort_values("n_commuters_inbound", ascending=False).reset_index(drop=True))
