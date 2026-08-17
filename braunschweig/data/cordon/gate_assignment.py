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
import numpy as np
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
    # Join-coverage transparency (CLAUDE.md no-silent-fallback): a volume row
    # whose ars5 has no Kreis geometry is silently DROPPED by this left merge;
    # a Kreis without a volume row is zero-filled below. Both must be visible,
    # or a key-format drift would just zero out the cordon demand.
    lost = volume[~volume["ars5"].isin(set(joined["ars5"]))]
    if len(lost):
        lost_total = int(lost[value_cols].sum().sum())
        print(
            f"[cordon.gate_assignment] WARNING: {len(lost)} volume row(s) "
            f"({lost_total:,} commuters) have no Kreis geometry and were "
            f"DROPPED from the gate assignment: {sorted(lost['ars5'].astype(str))}"
        )
    zero_filled = out.loc[out[value_cols].isna().all(axis=1), "ars5"]
    if len(zero_filled):
        print(
            f"[cordon.gate_assignment] {len(zero_filled)} Kreis(e) with geometry "
            f"but no volume row -> filled 0: {sorted(zero_filled.astype(str))}"
        )
    for col in value_cols:
        out[col] = out[col].fillna(0).astype(int)
    out["distance_km"] = out["dist_m"] / 1000.0
    out["_total"] = out[value_cols].sum(axis=1)
    out = out.sort_values("_total", ascending=False).drop(columns="_total")
    return out[["ars5", "gate_id"] + value_cols + ["distance_km"]].reset_index(drop=True)


def population_gravity_gate_assignment(gemeinden: gpd.GeoDataFrame, gates: gpd.GeoDataFrame,
                                       kreis_volume: pd.DataFrame, beta: float = -0.05,
                                       capacity_exponent: float = 1.0,
                                       value_cols=("inbound", "outbound")) -> pd.DataFrame:
    """Smart, gravity-based Kreis->gate assignment with population weighting.

    Instead of routing a whole Kreis through its single nearest gate, distribute each
    external Kreis's commuter volume FIRST across its Gemeinden by population (so the
    geographic spread of where people live is respected), THEN across the gates by a
    gravity model -- gate "attraction" = ``capacity^capacity_exponent`` times a
    distance decay ``exp(beta * dist_km)``. A Kreis therefore splits realistically
    over several corridors (e.g. Region Hannover between the A2 motorway gate and the
    nearer Bundesstrasse gates) instead of dumping everything on one gate.

    Args:
        gemeinden: GeoDataFrame [ars5, ewz, geometry] of external Gemeinden (ars5 =
            5-digit Kreis ARS, ewz = population; any geometry, representative point used).
        gates: GeoDataFrame [gate_id, capacity, geometry(point)].
        kreis_volume: DataFrame [ars5, <value_cols...>] (e.g. inbound, outbound).
        beta: distance-decay slope per km (negative; default -0.05).
        capacity_exponent: exponent on gate capacity as attraction (default 1.0).
        value_cols: the volume columns to distribute.

    Returns:
        DataFrame [ars5, gate_id, <value_cols...>] with each Kreis's volume spread
        across the gates it plausibly uses (integer counts; zero rows dropped).
    """
    if "gate_id" not in gates.columns:
        raise ValueError("population_gravity_gate_assignment: gates needs a 'gate_id' column")
    value_cols = list(value_cols)
    g = gemeinden.copy()
    g["geometry"] = g.geometry.representative_point()
    g["ewz"] = pd.to_numeric(g["ewz"], errors="coerce").fillna(0.0).astype(float)
    kreis_ewz = g.groupby("ars5")["ewz"].transform("sum")
    g["pop_share"] = np.where(kreis_ewz.to_numpy() > 0, g["ewz"] / kreis_ewz, 0.0)

    gx = g.geometry.x.to_numpy(); gy = g.geometry.y.to_numpy()
    tx = gates.geometry.x.to_numpy(); ty = gates.geometry.y.to_numpy()
    cap = pd.to_numeric(gates["capacity"], errors="coerce").fillna(0.0).to_numpy(dtype=float)
    cap = np.where(cap <= 0, 1.0, cap)
    gate_ids = gates["gate_id"].to_numpy()

    # Gravity share of each Gemeinde over the gates (n_gem x n_gate), summing to 1.
    dist_km = np.sqrt((gx[:, None] - tx[None, :]) ** 2
                      + (gy[:, None] - ty[None, :]) ** 2) / 1000.0
    weight = (cap[None, :] ** capacity_exponent) * np.exp(beta * dist_km)
    wsum = weight.sum(axis=1, keepdims=True)
    grav = np.where(wsum > 0, weight / wsum, 0.0)

    # Combined Gemeinde weight over gates = population share * gravity share, then
    # aggregate per Kreis -> the fraction of the Kreis volume routed to each gate.
    combined = g["pop_share"].to_numpy()[:, None] * grav
    factor = pd.DataFrame(combined, columns=gate_ids)
    factor["ars5"] = g["ars5"].to_numpy()
    factor = factor.groupby("ars5", as_index=True).sum()
    long = (factor.reset_index()
            .melt(id_vars="ars5", var_name="gate_id", value_name="factor"))
    # Join-coverage transparency (CLAUDE.md no-silent-fallback): a kreis_volume
    # row whose ars5 has no Gemeinde in ``gemeinden`` gets no factor row, so its
    # entire commuter volume silently vanishes from the assignment.
    dropped = kreis_volume[~kreis_volume["ars5"].isin(set(factor.index))]
    if len(dropped):
        dropped_total = int(dropped[value_cols].sum().sum())
        print(
            f"[cordon.gate_assignment] WARNING: {len(dropped)} kreis_volume "
            f"row(s) ({dropped_total:,} commuters) have no Gemeinde in the "
            f"gemeinden frame -> volume DROPPED: {sorted(dropped['ars5'].astype(str))}"
        )
    long = long.merge(kreis_volume, on="ars5", how="left")
    for col in value_cols:
        long[col] = (long[col].fillna(0.0) * long["factor"]).round().astype(int)
    long = long[long[value_cols].sum(axis=1) > 0]
    return long[["ars5", "gate_id"] + value_cols].reset_index(drop=True)


def sample_gate_per_agent(orig_ars, gate_assignment: pd.DataFrame, rng,
                          weight_col: str = "inbound"):
    """Draw one entry/exit gate per agent from its Kreis's gravity gate distribution.

    Each injected commuter belongs to an external Kreis; this samples its gate
    categorically, weighted by that Kreis's per-gate volume (``weight_col``) from
    :func:`population_gravity_gate_assignment`. So agents from a Kreis spread over its
    gates in the same proportions the gravity model produced.

    Args:
        orig_ars: iterable of each agent's external Kreis ARS (origin for inbound,
            destination for outbound).
        gate_assignment: DataFrame [ars5, gate_id, <weight_col>, ...].
        rng: a numpy Generator (seeded for reproducibility).
        weight_col: the volume column to weight gate choice by (default "inbound").

    Returns:
        list of gate_id (one per agent); ``None`` where the Kreis has no positive
        volume in ``gate_assignment``.
    """
    dist = {}
    for ars5, sub in gate_assignment.groupby("ars5"):
        sub = sub[sub[weight_col] > 0]
        if len(sub):
            w = sub[weight_col].to_numpy(dtype=float)
            dist[ars5] = (sub["gate_id"].to_numpy(), w / w.sum())
    out = []
    for ars5 in orig_ars:
        choice = dist.get(ars5)
        if choice is None:
            out.append(None)
        else:
            gids, probs = choice
            out.append(gids[int(rng.choice(len(gids), p=probs))])
    return out


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
