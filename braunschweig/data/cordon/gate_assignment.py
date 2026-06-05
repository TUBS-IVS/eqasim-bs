"""Assign external Kreise to cordon gates and aggregate per-gate commuter volume.

Each external (non-ZGB) Kreis enters/leaves the region through its nearest cordon
gate; a gate serves BOTH directions -- Einpendler (Einfahren: dest in ZGB) and
Auspendler (Ausfahren: orig in ZGB). The BA-Pendler SvB give the volume per Kreis
and direction. This yields, per gate, how many commuters use it inbound vs outbound
and which Kreise feed it -- the (ein/aus) placement basis (Phase 3) and the per-gate
usage in the validation outputs (gates.csv + gate_assignment.csv).

Region-neutral; expects a metric CRS (EPSG:25832 for ZGB). Pure functions; the
synpp stage / validator wires the data sources (BA OD, Kreis polygons, gates).

Part of the cross-cordon external-demand module; see
``docs/superpowers/specs/2026-06-05-cross-cordon-external-demand-design.md``.
"""
from __future__ import annotations

import geopandas as gpd
import pandas as pd


def commuter_volume_by_kreis(flows: pd.DataFrame, zgb_kreise) -> pd.DataFrame:
    """Inbound and outbound SvB per external Kreis (one row per external Kreis).

    - inbound  (Einpendler): dest Kreis in ZGB, orig Kreis NOT in ZGB -> by orig.
    - outbound (Auspendler):  orig Kreis in ZGB, dest Kreis NOT in ZGB -> by dest.

    The external Kreis is the origin for inbound and the destination for outbound;
    both map to the same external Kreis ARS, hence the same gate.

    Args:
        flows: columns [orig_ars, dest_ars, flow] (5-digit ARS, SvB count).
        zgb_kreise: iterable of in-scope ZGB 5-digit Kreis ARS.

    Returns:
        DataFrame [ars5, inbound, outbound] (int), one row per external Kreis that
        has any cross-cordon commute.
    """
    zgb = {str(k) for k in zgb_kreise}
    inb = (flows[flows["dest_ars"].isin(zgb) & ~flows["orig_ars"].isin(zgb)]
           .groupby("orig_ars")["flow"].sum().rename("inbound"))
    outb = (flows[flows["orig_ars"].isin(zgb) & ~flows["dest_ars"].isin(zgb)]
            .groupby("dest_ars")["flow"].sum().rename("outbound"))
    vol = pd.concat([inb, outb], axis=1).fillna(0).astype(int)
    vol.index.name = "ars5"
    return vol.reset_index()


def inbound_volume_by_kreis(flows: pd.DataFrame, zgb_kreise) -> pd.DataFrame:
    """Inbound-only convenience: [ars5, inbound]. See :func:`commuter_volume_by_kreis`."""
    vol = commuter_volume_by_kreis(flows, zgb_kreise)
    return vol[vol["inbound"] > 0][["ars5", "inbound"]].reset_index(drop=True)


def assign_kreise_to_gates_with_volume(kreise: gpd.GeoDataFrame, gates: gpd.GeoDataFrame,
                                       volume: pd.DataFrame) -> pd.DataFrame:
    """Assign each external Kreis to its nearest gate and attach its volume columns.

    Args:
        kreise: GeoDataFrame [ars5, geometry] of external Kreise (representative
            point used for the nearest-gate match).
        gates: GeoDataFrame with a ``gate_id`` column and point geometry.
        volume: DataFrame [ars5, <value columns...>] (e.g. inbound, outbound).

    Returns:
        DataFrame [ars5, gate_id, <value columns...>, distance_km], one row per
        Kreis, sorted by total volume descending. Missing volumes become 0.

    Raises:
        ValueError: if ``gates`` has no ``gate_id`` column.
    """
    if "gate_id" not in gates.columns:
        raise ValueError("assign_kreise_to_gates_with_volume: gates needs a 'gate_id' column")
    value_cols = [c for c in volume.columns if c != "ars5"]
    k = kreise.copy()
    k["geometry"] = k.geometry.representative_point()
    joined = gpd.sjoin_nearest(k, gates[["gate_id", "geometry"]], how="left",
                               distance_col="dist_m")
    joined = joined.drop(columns=[c for c in joined.columns if c.startswith("index_")])
    out = joined.merge(volume, on="ars5", how="left")
    for col in value_cols:
        out[col] = out[col].fillna(0).astype(int)
    out["distance_km"] = out["dist_m"] / 1000.0
    out["_total"] = out[value_cols].sum(axis=1)
    out = out.sort_values("_total", ascending=False).drop(columns="_total")
    return out[["ars5", "gate_id"] + value_cols + ["distance_km"]].reset_index(drop=True)


def gate_volume_summary(assignment: pd.DataFrame,
                        value_cols=("inbound", "outbound")) -> pd.DataFrame:
    """Aggregate the Kreis->gate assignment per gate, for each direction.

    Args:
        assignment: output of :func:`assign_kreise_to_gates_with_volume`.
        value_cols: the volume columns to sum per gate (e.g. inbound, outbound).

    Returns:
        DataFrame [gate_id, <value_cols summed>, n_kreise, source_kreise], sorted by
        total volume descending. ``n_kreise`` counts Kreise with any volume;
        ``source_kreise`` is a ';'-joined list of those ARS (by total volume desc).
    """
    cols = [c for c in value_cols if c in assignment.columns]
    rows = []
    for gid, sub in assignment.groupby("gate_id"):
        sub = sub.copy()
        sub["_total"] = sub[cols].sum(axis=1)
        sub = sub.sort_values("_total", ascending=False)
        contributing = sub.loc[sub["_total"] > 0, "ars5"].tolist()
        row = {"gate_id": gid}
        for c in cols:
            row[c] = int(sub[c].sum())
        row["n_kreise"] = int(len(contributing))
        row["source_kreise"] = ";".join(contributing)
        rows.append(row)
    frame = pd.DataFrame(rows, columns=["gate_id"] + cols + ["n_kreise", "source_kreise"])
    frame["_total"] = frame[cols].sum(axis=1)
    return frame.sort_values("_total", ascending=False).drop(columns="_total").reset_index(drop=True)
